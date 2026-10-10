import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from host import HostSystem
from server import Monitor, operating_system
from storage import StorageAnalyzer, StorageError
from test_storage import completed


class HostTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        for path in ("etc", "usr/lib", "proc/1", "srv/files", "data disk"):
            (self.root / path).mkdir(parents=True)
        (self.root / "etc/passwd").write_text("host_owner:x:12345:12345::/home/owner:/bin/sh\n")
        (self.root / "usr/lib/os-release").write_text('PRETTY_NAME="Host Linux"\n')
        (self.root / "etc/os-release").symlink_to("/usr/lib/os-release")
        (self.root / "proc/filesystems").write_text("nodev\tproc\nnodev\toverlay\n\text4\n")
        (self.root / "proc/1/mounts").write_text(
            "overlay / overlay rw 0 0\n"
            "proc /proc proc rw 0 0\n"
            "/dev/sda2 /data\\040disk ext4 rw 0 0\n")
        (self.root / "srv/files/host-only.txt").write_bytes(b"x" * 8192)
        (self.root / "alias").symlink_to("/srv/files", target_is_directory=True)
        (self.root / "virtual").symlink_to("/proc", target_is_directory=True)
        self.host = HostSystem(self.root)

    def tearDown(self):
        self.directory.cleanup()

    def test_host_metadata_and_absolute_symlinks_use_host_root(self):
        self.assertEqual(operating_system(self.host), "Host Linux")
        self.assertEqual(self.host.username(12345), "host_owner")
        self.assertEqual(self.host.username(54321), "54321")
        self.assertEqual(self.host.resolve("/alias"), "/srv/files")
        (self.root / "relative").symlink_to("srv/../srv/files")
        self.assertEqual(self.host.resolve("/relative"), "/srv/files")
        self.assertEqual(self.host.path("/../../srv/files"), self.root / "srv/files")
        (self.root / "loop").symlink_to("/loop")
        with self.assertRaises(OSError):
            self.host.resolve("/loop")

    def test_legacy_host_root_and_new_variable_precedence(self):
        with patch.dict(os.environ, {"KONG_HOST_ROOT": str(self.root)}, clear=True):
            self.assertEqual(HostSystem().root, self.root)
        with patch.dict(os.environ, {"KONG_HOST_ROOT": "/does-not-exist", "MONILITE_HOST_ROOT": str(self.root)}, clear=True):
            self.assertEqual(HostSystem().root, self.root)

    def test_mounts_and_usage_are_from_host_not_container(self):
        partitions = self.host.partitions()
        self.assertEqual([part.mountpoint for part in partitions], ["/", "/data disk"])
        monitor = object.__new__(Monitor)
        monitor.host = self.host
        from types import SimpleNamespace
        usage = SimpleNamespace(total=100, used=40, free=60, percent=40)
        with patch("server.psutil.disk_usage", return_value=usage) as disk_usage:
            disks = monitor.disks()
        self.assertEqual([row["mount"] for row in disks], ["/", "/data disk"])
        self.assertEqual([call.args[0] for call in disk_usage.call_args_list],
                         [self.root, self.root / "data disk"])

    def test_storage_scans_host_files_and_returns_host_paths(self):
        analyzer = StorageAnalyzer(host=self.host)
        result = completed(analyzer, analyzer.start(1, "/alias"))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["path"], "/srv/files")
        self.assertEqual(result["items"][0]["path"], "/srv/files/host-only.txt")
        self.assertEqual(result["items"][0]["logical_size"], 8192)
        self.assertEqual(result["largest_files"][0]["path"], "/srv/files/host-only.txt")
        with self.assertRaises(StorageError) as raised:
            analyzer.start(1, "/virtual")
        self.assertEqual(raised.exception.status, 403)
        with self.assertRaises(StorageError) as raised:
            analyzer.start(1, "/srv/missing")
        self.assertEqual(raised.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
