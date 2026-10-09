"""Translate host paths when kong runs with the Linux host mounted at /host."""
import os
import pwd
import re
import stat
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import psutil


class HostSystem:
    def __init__(self, root=None):
        configured = os.environ.get("KONG_HOST_ROOT", "") if root is None else root
        self.root = Path(configured).resolve(strict=True) if configured else None
        if self.root is not None and not self.root.is_dir():
            raise ValueError("KONG_HOST_ROOT must be a mounted host directory")
        self.users = {}
        if self.root is not None:
            try:
                for line in self.read_text("/etc/passwd").splitlines():
                    fields = line.split(":")
                    if len(fields) >= 3 and fields[2].isdigit():
                        self.users[int(fields[2])] = fields[0]
            except OSError:
                pass

    def path(self, path):
        if self.root is None:
            return Path(path)
        return self.root / os.path.normpath("/" + str(path).lstrip("/")).lstrip("/")

    def resolve(self, path):
        if self.root is None:
            return str(Path(path).resolve(strict=True))
        # Resolve absolute symlinks inside the host filesystem, rather than the container's /.
        pending = deque(str(path).split("/"))
        parts, links = [], 0
        while pending:
            part = pending.popleft()
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            candidate = self.root.joinpath(*parts, part)
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode):
                links += 1
                if links > 40:
                    raise OSError("Too many symbolic links")
                target = os.readlink(candidate)
                if target.startswith("/"):
                    parts.clear()
                pending.extendleft(reversed(target.split("/")))
            else:
                if pending and not stat.S_ISDIR(info.st_mode):
                    raise NotADirectoryError(str(candidate))
                parts.append(part)
        return "/" + "/".join(parts)

    def read_text(self, path):
        return self.path(self.resolve(path)).read_text()

    def username(self, uid):
        if self.root is not None:
            return self.users.get(uid, str(uid))
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)

    def partitions(self):
        if self.root is None:
            return psutil.disk_partitions()
        filesystems = set()
        for line in self.read_text("/proc/filesystems").splitlines():
            fields = line.split()
            if len(fields) == 1 or fields == ["nodev", "zfs"]:
                filesystems.add(fields[-1])
        partitions = []
        for line in self.read_text("/proc/1/mounts").splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            device, mount, fstype = [re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), field)
                                      for field in fields[:3]]
            if mount == "/" or fstype in filesystems:
                partitions.append(SimpleNamespace(device=device, mountpoint=mount, fstype=fstype))
        return partitions


def configure_host():
    host = HostSystem()
    if host.root is not None:
        proc = host.path("/proc")
        if not (proc / "stat").is_file() or not (proc / "1/mounts").is_file():
            raise ValueError("KONG_HOST_ROOT must include the host /proc; use the provided Compose configuration")
        psutil.PROCFS_PATH = str(proc)
    return host
