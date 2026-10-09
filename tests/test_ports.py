import os
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from ports import address_scope, collect_ports, docker_bindings, endpoint, port_snapshot


class PortTests(unittest.TestCase):
    def connection(self, port, status=psutil.CONN_LISTEN, protocol=socket.SOCK_STREAM,
                   address="127.0.0.1", pid=42, remote=()):
        return SimpleNamespace(laddr=(address, port), raddr=remote, status=status,
                               type=protocol, family=socket.AF_INET6 if ":" in address else socket.AF_INET, pid=pid)

    def test_only_listening_tcp_and_unconnected_udp_are_collected(self):
        connections = [self.connection(8080), self.connection(9999, status=psutil.CONN_ESTABLISHED),
                       self.connection(53, status=psutil.CONN_NONE, protocol=socket.SOCK_DGRAM),
                       self.connection(3333, protocol=socket.SOCK_DGRAM, remote=("1.1.1.1", 53)),
                       self.connection(8080, pid=43), self.connection(8080, pid=42),
                       self.connection(8080, address="0.0.0.0", pid=None)]
        with patch("ports.psutil.net_connections", return_value=connections), patch("ports.process_details", side_effect=lambda pid: {"pid": pid, "name": "test"}):
            result = collect_ports()
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["items"][0]["status"], "bound")
        tcp = next(row for row in result["items"] if row["port"] == 8080 and row["address"] == "127.0.0.1")
        self.assertEqual([p["pid"] for p in tcp["processes"]], [42, 43])
        self.assertTrue(result["available"])

    def test_address_scopes_include_ipv6_and_mapped_loopback(self):
        for address in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertEqual(address_scope(address), "local")
        for address in ("0.0.0.0", "::"):
            self.assertEqual(address_scope(address), "all")
        for address in ("100.83.129.124", "fe80::1%eth0", "192.168.1.1"):
            self.assertEqual(address_scope(address), "interface")

    def test_permission_failure_does_not_look_like_empty_inventory(self):
        with patch("ports.psutil.net_connections", side_effect=psutil.AccessDenied()):
            result = collect_ports()
        self.assertFalse(result["available"])
        self.assertTrue(result["error"])
        self.assertFalse(result["loading"])

    def test_live_bound_sockets_have_correct_process(self):
        with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            tcp.bind(("127.0.0.1", 0))
            tcp.listen()
            udp.bind(("127.0.0.1", 0))
            rows = {r["id"]: r for r in collect_ports()["items"]}
            for protocol, sock in (("tcp", tcp), ("udp", udp)):
                row = rows[endpoint(protocol, "127.0.0.1", sock.getsockname()[1])]
                self.assertEqual(row["scope"], "local")
                self.assertIn(os.getpid(), [p["pid"] for p in row["processes"]])

    def inspection(self, running=True):
        return {"Id": "abc", "Name": "/test-web", "Config": {"Image": "nginx:stable"},
                "State": {"Running": running}, "NetworkSettings": {"Ports": {
                    "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}, {"HostIp": "::", "HostPort": "8081"}],
                    "53/udp": [{"HostIp": "0.0.0.0", "HostPort": "5353"}], "443/tcp": None}}}

    def test_docker_uses_published_ports_and_ignores_stopped_and_exposed(self):
        bindings = docker_bindings([self.inspection(), self.inspection(running=False)])
        self.assertEqual(len(bindings), 3)
        self.assertEqual(bindings[0]["name"], "test-web")
        self.assertEqual(bindings[0]["target_port"], 80)
        self.assertEqual(bindings[1]["address"], "::")

    def test_nat_mapping_without_socket_and_distinct_addresses(self):
        with patch("ports.psutil.net_connections", return_value=[self.connection(8080, pid=None), self.connection(8080, address="100.83.129.124", pid=None)]):
            collected = collect_ports()
        docker = {"bindings": docker_bindings([self.inspection()]), "updated_at": 100, "loading": False}
        result = port_snapshot(collected, docker)
        rows = {r["id"]: r for r in result["items"]}
        self.assertEqual(len(rows), 4)
        mapped = rows[endpoint("tcp", "127.0.0.1", 8080)]
        self.assertEqual(mapped["containers"][0]["name"], "test-web")
        self.assertFalse(rows[endpoint("tcp", "100.83.129.124", 8080)]["containers"])
        self.assertEqual(rows[endpoint("tcp", "::", 8081)]["status"], "published")
        mapped["containers"][0]["name"] = "changed"
        self.assertEqual(docker["bindings"][0]["name"], "test-web")
        self.assertFalse(collected["items"][0]["containers"])


if __name__ == "__main__":
    unittest.main()
