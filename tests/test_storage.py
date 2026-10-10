import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from auth import AuthStore
from server import Monitor, make_handler
from storage import StorageAnalyzer, StorageError


def completed(analyzer, job, owner=1):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = analyzer.get(job["id"], owner)
        if result["status"] != "running":
            return result
        time.sleep(.01)
    raise AssertionError("Storage scan did not complete")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "small.log").write_bytes(b"x" * 4096)
        (self.root / "large.bin").write_bytes(b"x" * 65536)
        (self.root / "nested").mkdir()
        (self.root / "nested" / "inside.txt").write_bytes(b"y" * 32768)
        self.analyzer = StorageAnalyzer()

    def tearDown(self):
        self.directory.cleanup()

    def test_sizes_sorting_and_recursive_large_files(self):
        result = completed(self.analyzer, self.analyzer.start(1, str(self.root)))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["entries_scanned"], 4)
        items = {item["name"]: item for item in result["items"]}
        self.assertEqual(items["large.bin"]["size"], (self.root / "large.bin").stat().st_blocks * 512)
        nested = self.root / "nested"
        self.assertEqual(items["nested"]["size"], nested.stat().st_blocks * 512 + (nested / "inside.txt").stat().st_blocks * 512)
        self.assertEqual(result["bytes_scanned"], self.root.stat().st_blocks * 512 + sum(item["size"] for item in result["items"]))
        self.assertEqual([item["size"] for item in result["items"]], sorted((item["size"] for item in result["items"]), reverse=True))
        self.assertTrue(any(file["path"] == str(nested / "inside.txt") for file in result["largest_files"]))
        self.assertNotIn("owner", result)

    def test_sparse_files_use_allocated_blocks_and_symlinks_are_not_followed(self):
        with (self.root / "sparse.bin").open("wb") as file:
            file.truncate(8 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "outside-secret-name.txt").write_bytes(b"z" * 4096)
            (self.root / "linked-directory").symlink_to(outside, target_is_directory=True)
            result = completed(self.analyzer, self.analyzer.start(1, str(self.root)))
        sparse = next(item for item in result["items"] if item["name"] == "sparse.bin")
        self.assertEqual(sparse["logical_size"], 8 * 1024 * 1024)
        self.assertLess(sparse["size"], sparse["logical_size"])
        link = next(item for item in result["items"] if item["name"] == "linked-directory")
        self.assertEqual(link["type"], "symlink")
        self.assertFalse(link["browseable"])
        self.assertFalse(any(file["name"] == "outside-secret-name.txt" for file in result["largest_files"]))

    def test_hard_links_count_only_once(self):
        os.link(self.root / "large.bin", self.root / "large-link.bin")
        result = completed(self.analyzer, self.analyzer.start(1, str(self.root)))
        linked = [item for item in result["items"] if item["name"].startswith("large")]
        self.assertEqual(sum(item["size"] for item in linked), (self.root / "large.bin").stat().st_blocks * 512)
        self.assertTrue(all(item["logical_size"] == 65536 for item in linked))
        self.assertEqual(result["duplicate_links"], 1)

    def test_invalid_virtual_and_missing_paths(self):
        for path, status in [("relative", 400), ("/proc", 403), ("/sys", 403), (str(self.root / "missing"), 404), (str(self.root / "large.bin"), 400)]:
            with self.subTest(path=path), self.assertRaises(StorageError) as raised:
                self.analyzer.start(1, path)
            self.assertEqual(raised.exception.status, status)

    def test_cached_snapshot_force_refresh_and_owner_isolation(self):
        first = completed(self.analyzer, self.analyzer.start(1, str(self.root)))
        cached = self.analyzer.start(1, str(self.root))
        self.assertTrue(cached["cached"])
        self.assertEqual(first["id"], cached["id"])
        with self.assertRaises(StorageError) as raised:
            self.analyzer.get(first["id"], 2)
        self.assertEqual(raised.exception.status, 404)
        (self.root / "new.txt").write_bytes(b"new")
        fresh = completed(self.analyzer, self.analyzer.start(1, str(self.root), force=True))
        self.assertNotEqual(fresh["id"], first["id"])
        self.assertEqual(fresh["items_count"], first["items_count"] + 1)

    def test_budget_limits_are_explicit_partial_results(self):
        for analyzer in (StorageAnalyzer(max_entries=1), StorageAnalyzer(max_seconds=0)):
            with self.subTest(limit=analyzer.max_entries):
                result = completed(analyzer, analyzer.start(1, str(self.root)))
                self.assertEqual(result["status"], "partial")
                self.assertTrue(any("上限" in note for note in result["notes"]))
                self.assertLessEqual(result["entries_scanned"], analyzer.max_entries)


class StorageAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name) / "files"
        cls.root.mkdir()
        (cls.root / "sample.txt").write_bytes(b"sample")
        auth = AuthStore(Path(cls.directory.name) / "auth")
        session, token = auth.setup("storage_owner", "storage-owner-password")
        cls.admin = {"Cookie": f"monilite_session={token}", "X-CSRF-Token": session["csrf_token"]}
        auth.create_user(session["user"]["id"], "storage_reader", "storage-reader-password")
        viewer, viewer_token = auth.login("storage_reader", "storage-reader-password")
        cls.viewer = {"Cookie": f"monilite_session={viewer_token}", "X-CSRF-Token": viewer["csrf_token"]}
        monitor = Monitor()
        monitor.sample()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(monitor, auth))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, method="GET", path="/api/storage/scan", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port)
        headers = dict(headers or {})
        if method == "POST":
            headers["Content-Type"] = "application/json"
        connection.request(method, path, json.dumps(body or {}) if method == "POST" else None, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_admin_only_and_csrf(self):
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(headers=self.viewer)[0], 403)
        self.assertEqual(self.request("POST", headers=self.viewer, body={"path":str(self.root)})[0], 403)
        headers = {**self.admin, "X-CSRF-Token":"wrong"}
        self.assertEqual(self.request("POST", headers=headers, body={"path":str(self.root)})[0], 403)

    def test_async_scan_and_reading_results(self):
        status, job = self.request("POST", headers=self.admin, body={"path":str(self.root)})
        self.assertEqual(status, 202)
        for _ in range(100):
            status, result = self.request(path=f'/api/storage/scan?id={job["id"]}', headers=self.admin)
            if result["status"] != "running":
                break
            time.sleep(.01)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["items"][0]["name"], "sample.txt")
        self.assertEqual(self.request(path=f'/api/storage/scan?id={job["id"]}', headers=self.viewer)[0], 403)


if __name__ == "__main__":
    unittest.main()
