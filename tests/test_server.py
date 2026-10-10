import json
import gzip
import threading
import time
import unittest
import tempfile
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psutil

from server import Monitor, make_handler, percent_number
from auth import AuthStore


class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.monitor = Monitor()
        cls.monitor.sample()
        cls.directory = tempfile.TemporaryDirectory()
        cls.auth = AuthStore(cls.directory.name)
        _, token = cls.auth.setup("test_admin", "unit-test-password")
        cls.cookie = f"monilite_session={token}"
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.monitor, cls.auth))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def test_live_metrics_match_host(self):
        with urlopen(Request(self.base + "/api/metrics", headers={"Cookie": self.cookie})) as response:
            data = json.load(response)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(data["system"]["cpu_logical"], psutil.cpu_count())
        self.assertEqual(data["memory"]["total"], psutil.virtual_memory().total)
        self.assertGreater(data["process_count"], 0)
        self.assertEqual(data["disks"][0]["mount"], "/")
        self.assertGreater(data["disks"][0]["total"], 0)
        self.assertGreater(len(data["cpu"]["cores"]), 0)
        self.assertTrue(all(0 <= cpu <= 100 for cpu in data["cpu"]["cores"]))

    def test_snapshot_does_not_allow_mutation_of_collector(self):
        snapshot = self.monitor.snapshot()
        original = self.monitor.current["memory"]["total"]
        snapshot["memory"]["total"] = -1
        snapshot["history"][0]["cpu"] = -1
        self.assertEqual(self.monitor.current["memory"]["total"], original)
        self.assertGreaterEqual(self.monitor.history[0]["cpu"], 0)

    def test_metrics_compression_and_encoding_negotiation(self):
        with urlopen(Request(self.base + "/api/metrics", headers={"Cookie": self.cookie})) as response:
            plain = response.read()
            self.assertIsNone(response.headers.get("Content-Encoding"))
        for encoding in ("gzip", "br, GZip ; q=0.5"):
            with self.subTest(encoding=encoding), urlopen(Request(self.base + "/api/metrics", headers={"Cookie": self.cookie, "Accept-Encoding": encoding})) as response:
                compressed = response.read()
                self.assertEqual(response.headers["Content-Encoding"], "gzip")
                self.assertEqual(response.headers["Vary"], "Accept-Encoding")
                self.assertEqual(int(response.headers["Content-Length"]), len(compressed))
            self.assertEqual(json.loads(gzip.decompress(compressed)), json.loads(plain))
            self.assertLess(len(compressed), len(plain))
        for encoding in ("gzip;q=0, *;q=1", "gzip;q=invalid", "br"):
            with self.subTest(encoding=encoding), urlopen(Request(self.base + "/api/metrics", headers={"Cookie": self.cookie, "Accept-Encoding": encoding})) as response:
                self.assertIsNone(response.headers.get("Content-Encoding"))
                self.assertEqual(json.load(response), json.loads(plain))

    def test_compressed_javascript_preserves_source(self):
        with urlopen(Request(self.base + "/app.js", headers={"Accept-Encoding":"gzip"})) as response:
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            content = gzip.decompress(response.read())
        with urlopen(self.base + "/app.js") as response:
            self.assertEqual(content, response.read())

    def test_bind_mounts_do_not_duplicate_storage(self):
        from types import SimpleNamespace
        partitions = [SimpleNamespace(mountpoint="/", device="/dev/root", fstype="xfs"),
                      SimpleNamespace(mountpoint="/tmp", device="/dev/root", fstype="xfs")]
        with patch("server.psutil.disk_partitions", return_value=partitions):
            disks = self.monitor.disks()
        self.assertEqual([disk["mount"] for disk in disks], ["/"])

    def test_history_range(self):
        with self.monitor.lock:
            self.monitor.history.appendleft({"timestamp": time.time() - 1800, "cpu": 0})
        try:
            short = self.monitor.snapshot(300)["history"]
            long = self.monitor.snapshot(3600)["history"]
            self.assertEqual(len(long), len(short) + 1)
        finally:
            with self.monitor.lock:
                self.monitor.history.popleft()

    def test_invalid_range_returns_json_error(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(self.base + "/api/metrics?seconds=bad", headers={"Cookie": self.cookie}))
        self.assertEqual(raised.exception.code, 400)
        self.assertIn("error", json.load(raised.exception))

    def test_static_server_cannot_expose_source_or_parent_files(self):
        for path in ("/../server.py", "/server.py", "/../../etc/passwd"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                urlopen(self.base + path)
            self.assertEqual(raised.exception.code, 404)

    def test_health_detects_stalled_collection(self):
        with urlopen(self.base + "/api/health") as response:
            self.assertEqual(json.load(response)["status"], "ok")
        with self.monitor.lock:
            timestamp = self.monitor.current["timestamp"]
            self.monitor.current["timestamp"] = time.time() - 60
        try:
            with self.assertRaises(HTTPError) as raised:
                urlopen(self.base + "/api/health")
            self.assertEqual(raised.exception.code, 503)
            self.assertEqual(json.load(raised.exception)["status"], "degraded")
        finally:
            with self.monitor.lock:
                self.monitor.current["timestamp"] = timestamp

    def test_docker_missing_does_not_break_system_metrics(self):
        monitor = Monitor()
        monitor.sample()
        with patch("server.shutil.which", return_value=None), patch.object(monitor.stop_event, "wait", side_effect=lambda _: monitor.stop_event.set()):
            monitor.docker_loop()
        snapshot = monitor.snapshot()
        self.assertFalse(snapshot["docker"]["available"])
        self.assertEqual(snapshot["docker"]["error"], "未安装 Docker CLI")
        self.assertGreater(snapshot["memory"]["total"], 0)

    def test_docker_stats_join_and_state(self):
        monitor = Monitor()
        responses = [
            [{"ID": "abc", "Names": "web", "Image": "nginx", "State": "running", "Status": "Up 2 hours"},
             {"ID": "def", "Names": "worker", "Image": "redis", "State": "exited", "Status": "Exited (0)"}],
            [{"Name": "web", "CPUPerc": "150.50%", "MemPerc": "12.4%", "MemUsage": "20MiB / 1GiB", "PIDs": "3"}],
            [{"Id": "abc", "Name": "/web", "State": {"Running": True}, "Config": {"Image": "nginx"},
              "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}}}],
        ]
        with patch("server.shutil.which", return_value="/usr/bin/docker"), patch("server.command_json", side_effect=responses), patch.object(monitor.stop_event, "wait", side_effect=lambda _: monitor.stop_event.set()):
            monitor.docker_loop()
        self.assertTrue(monitor.docker["available"])
        self.assertEqual(monitor.docker["running"], 1)
        self.assertEqual(monitor.docker["stopped"], 1)
        self.assertEqual(monitor.docker["containers"][0]["cpu"], 150.5)
        self.assertEqual(monitor.docker["containers"][1]["memory_usage"], "—")
        self.assertEqual(monitor.docker["bindings"][0]["port"], 8000)
        monitor.sample()
        self.assertEqual(monitor.snapshot()["ports"]["items"][0]["containers"][0]["name"], "web")

    def test_docker_inspect_failure_preserves_container_metrics(self):
        import subprocess
        monitor = Monitor()
        responses = [[{"ID": "abc", "Names": "web", "Image": "nginx", "State": "running", "Status": "Up"}],
                     [{"Name": "web", "CPUPerc": "12%"}], subprocess.TimeoutExpired("docker", 10)]
        with patch("server.shutil.which", return_value="/usr/bin/docker"), patch("server.command_json", side_effect=responses), patch.object(monitor.stop_event, "wait", side_effect=lambda _: monitor.stop_event.set()):
            monitor.docker_loop()
        self.assertTrue(monitor.docker["available"])
        self.assertEqual(monitor.docker["containers"][0]["cpu"], 12)
        self.assertTrue(monitor.docker["ports_error"])

    def test_invalid_percentage(self):
        self.assertEqual(percent_number("--"), 0)
        self.assertEqual(percent_number("0.56%"), 0.56)


if __name__ == "__main__":
    unittest.main()
