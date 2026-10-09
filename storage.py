"""Bounded, asynchronous, metadata-only disk usage analysis."""
import copy
import heapq
import os
import secrets
import stat
import threading
import time
from types import SimpleNamespace
from host import HostSystem


class StorageError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class StorageAnalyzer:
    BLOCKED = ("/proc", "/sys", "/dev", "/run")

    def __init__(self, max_entries=800_000, max_seconds=25, cache_seconds=300, host=None):
        self.host = host or HostSystem()
        self.max_entries = max_entries
        self.max_seconds = max_seconds
        self.cache_seconds = cache_seconds
        self.lock = threading.Lock()
        self.jobs = {}
        self.active = None

    @classmethod
    def blocked(cls, path):
        return any(path == prefix or path.startswith(prefix + "/") for prefix in cls.BLOCKED)

    @staticmethod
    def text(value):
        return str(value).encode("utf-8", "replace").decode("utf-8")

    def validate_path(self, path):
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 4096 or "\0" in path:
            raise StorageError("请输入以 / 开头的绝对目录路径")
        try:
            path.encode("utf-8")
            resolved = self.host.resolve(path)
            info = os.stat(self.host.path(resolved))
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise StorageError("目录不存在或无法访问", 404) from exc
        if self.blocked(resolved):
            raise StorageError("虚拟系统目录不参与磁盘占用分析", 403)
        if not stat.S_ISDIR(info.st_mode):
            raise StorageError("请选择目录，文件信息可在分析结果中查看")
        return resolved, info

    def start(self, owner, path, force=False):
        if not isinstance(force, bool):
            raise StorageError("force 必须为布尔值")
        path, info = self.validate_path(path)
        now = time.time()
        with self.lock:
            for identifier, job in list(self.jobs.items()):
                if job["status"] != "running" and now - job["started_at"] > self.cache_seconds:
                    del self.jobs[identifier]
            for job in self.jobs.values():
                if job["owner"] == owner and job["path"] == path and (job["status"] == "running" or not force and job["status"] in ("complete", "partial")):
                    return self.public(job, cached=job["status"] != "running")
            if self.active is not None:
                raise StorageError("另一个目录正在分析，请稍后重试", 409)
            if len(self.jobs) >= 16:
                oldest = min(self.jobs, key=lambda identifier: self.jobs[identifier]["started_at"])
                del self.jobs[oldest]
            identifier = secrets.token_urlsafe(18)
            job = {"id": identifier, "owner": owner, "path": path, "status": "running", "started_at": now,
                   "finished_at": None, "entries_scanned": 0, "bytes_scanned": 0,
                   "items": [], "largest_files": [], "items_count": 0, "error": None,
                   "skipped": 0, "unreadable": 0, "duplicate_links": 0, "notes": []}
            self.jobs[identifier] = job
            self.active = identifier
            result = self.public(job)
            threading.Thread(target=self.scan, args=(identifier, path, info), name="kong-storage", daemon=True).start()
            return result

    def public(self, job, cached=False):
        return {**copy.deepcopy({k: v for k, v in job.items() if k != "owner"}),
                "elapsed": round((job["finished_at"] or time.time()) - job["started_at"], 1),
                "cached": cached, "limits": {"entries": self.max_entries, "seconds": self.max_seconds}}

    def get(self, identifier, owner):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job["owner"] != owner:
                raise StorageError("分析任务不存在或已过期", 404)
            return self.public(job)

    def scan(self, identifier, path, root_info):
        started = time.monotonic()
        counts = {"entries_scanned": 0, "bytes_scanned": root_info.st_blocks * 512,
                  "skipped": 0, "unreadable": 0, "duplicate_links": 0}
        notes, items, largest = [], [], []
        seen_dirs, seen_links = {(root_info.st_dev, root_info.st_ino)}, set()
        limited, depth_limited = False, False
        last_progress = started

        class BudgetReached(Exception):
            pass

        def check_budget():
            nonlocal last_progress
            now = time.monotonic()
            if counts["entries_scanned"] >= self.max_entries or now - started >= self.max_seconds:
                raise BudgetReached()
            if now - last_progress >= .5:
                with self.lock:
                    self.jobs[identifier].update(counts)
                last_progress = now

        def metadata(entry, info):
            kind = "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode) else "special"
            return {"name": self.text(entry.name), "path": self.text(entry.path), "type": kind,
                    "size": 0, "logical_size": 0, "modified_at": info.st_mtime,
                    "browseable": kind == "directory" and self.text(entry.path) == entry.path,
                    "excluded": False}

        def visit(entry, info, item, depth, parent_fd):
            nonlocal depth_limited
            check_budget()
            counts["entries_scanned"] += 1
            is_directory = stat.S_ISDIR(info.st_mode)
            if self.blocked(entry.path) or info.st_dev != root_info.st_dev or not (is_directory or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                counts["skipped"] += 1
                if depth == 0:
                    item.update(excluded=True, browseable=False)
                return
            node = (info.st_dev, info.st_ino)
            if is_directory:
                if node in seen_dirs:
                    counts["duplicate_links"] += 1
                    return
                seen_dirs.add(node)
            elif info.st_nlink > 1:
                if node in seen_links:
                    counts["duplicate_links"] += 1
                    if depth == 0:
                        item["logical_size"] = info.st_size
                    return
                seen_links.add(node)
            allocated = info.st_blocks * 512
            item["size"] += allocated
            item["logical_size"] += info.st_size
            counts["bytes_scanned"] += allocated
            if stat.S_ISREG(info.st_mode) and info.st_size > 0:
                row = metadata(entry, info)
                row.update(size=allocated, logical_size=info.st_size)
                key = (allocated, counts["entries_scanned"], row)
                if len(largest) < 50:
                    heapq.heappush(largest, key)
                elif key[:2] > largest[0][:2]:
                    heapq.heapreplace(largest, key)
            if is_directory:
                if depth >= 64:
                    depth_limited = True
                    counts["skipped"] += 1
                    return
                directory_fd = None
                try:
                    directory_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                    opened = os.fstat(directory_fd)
                    if (opened.st_dev, opened.st_ino) != node:
                        counts["unreadable"] += 1
                        return
                    with os.scandir(directory_fd) as children:
                        for child in children:
                            check_budget()
                            try:
                                child_info = child.stat(follow_symlinks=False)
                            except OSError:
                                counts["unreadable"] += 1
                                continue
                            child_entry = SimpleNamespace(name=child.name, path=os.path.join(entry.path, child.name))
                            visit(child_entry, child_info, item, depth + 1, directory_fd)
                except OSError:
                    counts["unreadable"] += 1
                finally:
                    if directory_fd is not None:
                        os.close(directory_fd)

        error = None
        root_fd = None
        try:
            root_fd = os.open(self.host.path(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened = os.fstat(root_fd)
            if (opened.st_dev, opened.st_ino) != (root_info.st_dev, root_info.st_ino):
                raise OSError("Directory changed")
            with os.scandir(root_fd) as children:
                for child in children:
                    check_budget()
                    if len(items) >= 10_000:
                        raise BudgetReached()
                    try:
                        info = child.stat(follow_symlinks=False)
                    except OSError:
                        counts["unreadable"] += 1
                        continue
                    entry = SimpleNamespace(name=child.name, path=os.path.join(path, child.name))
                    item = metadata(entry, info)
                    items.append(item)
                    visit(entry, info, item, 0, root_fd)
        except BudgetReached:
            limited = True
            notes.append("达到扫描时间或条目上限，显示已扫描部分；进入具体子目录可以继续定位。")
        except OSError:
            error = "目录无法读取，请检查权限或确认目录仍然存在"
        except Exception:
            error = "目录分析失败，请稍后重试"
        finally:
            if root_fd is not None:
                os.close(root_fd)
        if counts["unreadable"]:
            notes.append(f"{counts['unreadable']} 项无法读取或在扫描过程中发生变化，统计未包含这些内容。")
        if depth_limited:
            notes.append("部分目录层级过深，进入具体子目录后可以继续分析。")
        notes.append("按实际分配的磁盘块统计；不读取文件内容、不跟随符号链接，跳过虚拟目录与其他文件系统，硬链接只统计一次。")
        notes.append("结果是按需扫描的快照，可能与文件系统已用空间不同；已删除但仍打开的文件和文件系统元数据不包含在目录统计中。")
        with self.lock:
            job = self.jobs[identifier]
            job.update(counts, items=sorted(items, key=lambda item: item["size"], reverse=True)[:200],
                       items_count=len(items), largest_files=[row for _, _, row in sorted(largest, key=lambda value: value[:2], reverse=True)],
                       status="error" if error else "partial" if limited or depth_limited or counts["unreadable"] else "complete",
                       error=error, notes=notes, finished_at=time.time())
            self.active = None
