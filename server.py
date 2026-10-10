#!/usr/bin/env python3
"""MoniLite · A small, read-only Linux server monitor."""
import argparse
import copy
import json
import hmac
import mimetypes
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psutil
from auth import AuthError, AuthStore, SESSION_SECONDS
from storage import StorageAnalyzer, StorageError
from ports import collect_ports, docker_bindings, port_snapshot
from host import HostSystem, configure_host

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SAMPLE_INTERVAL = 2
HISTORY_SECONDS = 3600


def command_json(command, timeout=10):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def percent_number(value):
    try:
        return float(value.rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def operating_system(host=None):
    try:
        for line in (host or HostSystem()).read_text("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return platform.system()


class Monitor:
    def __init__(self, host=None):
        self.host = host or HostSystem()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.history = deque(maxlen=HISTORY_SECONDS // SAMPLE_INTERVAL)
        self.current = None
        self.docker = {"available": False, "loading": True, "containers": [], "error": None, "updated_at": None}
        self.ports = {"available": False, "loading": True, "items": [], "error": None, "updated_at": None}
        self.system = {
            "hostname": self.host.read_text("/proc/sys/kernel/hostname").strip() if self.host.root else socket.gethostname(),
            "os": operating_system(self.host),
            "kernel": platform.release(), "architecture": platform.machine(),
            "cpu_model": cpu_model(), "cpu_logical": psutil.cpu_count() or 1,
            "cpu_physical": psutil.cpu_count(logical=False) or 1,
            "boot_time": psutil.boot_time(), "sample_interval": SAMPLE_INTERVAL,
        }
        self.previous_net = psutil.net_io_counters(pernic=True)
        self.previous_disk = psutil.disk_io_counters()
        self.previous_time = time.monotonic()
        # Prime psutil's nonblocking CPU counters before the first measured sample.
        psutil.cpu_percent(percpu=True)
        self.processes()

    def start(self):
        self.sample()
        threading.Thread(target=self.sample_loop, daemon=True, name="monilite-metrics").start()
        threading.Thread(target=self.docker_loop, daemon=True, name="monilite-docker").start()
        threading.Thread(target=self.ports_loop, daemon=True, name="monilite-ports").start()

    def processes(self):
        result = []
        for proc in psutil.process_iter(["pid", "name", "uids", "memory_info", "status"]):
            try:
                info = proc.info
                result.append({
                    "pid": info["pid"], "name": info["name"] or "unknown",
                    "user": self.host.username(info["uids"].real) if info["uids"] else "—", "status": info["status"],
                    "cpu": round(proc.cpu_percent(), 1),
                    "memory": info["memory_info"].rss if info["memory_info"] else 0,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self.memory_processes = sorted(result, key=lambda p: (p["memory"], p["cpu"]), reverse=True)[:20]
        return sorted(result, key=lambda p: (p["cpu"], p["memory"]), reverse=True)[:100]

    def disks(self):
        disks, seen, seen_devices = [], set(), set()
        mounts = [(p.mountpoint, p.device, p.fstype) for p in self.host.partitions()]
        if not any(mount == "/" for mount, _, _ in mounts):
            mounts.insert(0, ("/", "/", "root"))
        mounts.sort(key=lambda item: item[0] != "/")
        for mount, device, fstype in mounts:
            if mount in seen or device in seen_devices or mount.startswith(("/snap/", "/boot/efi")):
                continue
            try:
                usage = psutil.disk_usage(self.host.path(mount))
            except (OSError, PermissionError):
                continue
            seen.add(mount)
            seen_devices.add(device)
            disks.append({"mount": mount, "device": device, "filesystem": fstype,
                          "total": usage.total, "used": usage.used, "free": usage.free,
                          "percent": usage.percent})
        return disks[:12]

    def sample(self):
        now, monotonic = time.time(), time.monotonic()
        elapsed = max(monotonic - self.previous_time, 0.001)
        cpu_per_core = psutil.cpu_percent(percpu=True)
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        net = psutil.net_io_counters(pernic=True)
        addresses = psutil.net_if_addrs()
        statuses = psutil.net_if_stats()
        interfaces = []
        for name, counters in net.items():
            if name == "lo":
                continue
            old = self.previous_net.get(name, counters)
            status = statuses.get(name)
            interfaces.append({
                "name": name, "up": bool(status and status.isup),
                "address": next((a.address for a in addresses.get(name, []) if a.family == socket.AF_INET), "—"),
                "sent": counters.bytes_sent, "received": counters.bytes_recv,
                "tx_rate": max(0, counters.bytes_sent - old.bytes_sent) / elapsed,
                "rx_rate": max(0, counters.bytes_recv - old.bytes_recv) / elapsed,
            })
        # Aggregate physical/primary interfaces only: bridges and veths would double count Docker traffic.
        primary = [i for i in interfaces if i["up"] and not i["name"].startswith(("veth", "br-", "docker", "virbr", "tailscale", "tun", "tap", "wg"))]
        disk_io = psutil.disk_io_counters()
        disk_rates = {"read_rate": 0, "write_rate": 0}
        if disk_io and self.previous_disk:
            disk_rates = {
                "read_rate": max(0, disk_io.read_bytes - self.previous_disk.read_bytes) / elapsed,
                "write_rate": max(0, disk_io.write_bytes - self.previous_disk.write_bytes) / elapsed,
            }
        cpu = round(sum(cpu_per_core) / max(len(cpu_per_core), 1), 1)
        network = {"rx_rate": sum(i["rx_rate"] for i in primary),
                   "tx_rate": sum(i["tx_rate"] for i in primary), "interfaces": interfaces,
                   "received": sum(i["received"] for i in primary), "sent": sum(i["sent"] for i in primary)}
        processes = self.processes()
        snapshot = {
            "timestamp": now, "system": {**self.system, "uptime": max(0, now - self.system["boot_time"])},
            "cpu": {"percent": cpu, "cores": cpu_per_core, "load": list(os.getloadavg())},
            "memory": {"total": memory.total, "used": memory.total - memory.available,
                       "available": memory.available, "cached": getattr(memory, "cached", 0),
                       "percent": memory.percent, "swap_total": swap.total, "swap_used": swap.used,
                       "swap_percent": swap.percent},
            "disks": self.disks(), "disk_io": disk_rates, "network": network,
            "processes": processes, "memory_processes": self.memory_processes, "process_count": len(psutil.pids()),
        }
        point = {"timestamp": now, "cpu": cpu, "memory": memory.percent,
                 "disk": snapshot["disks"][0]["percent"] if snapshot["disks"] else 0,
                 "rx": network["rx_rate"], "tx": network["tx_rate"],
                 "disk_read": disk_rates["read_rate"], "disk_write": disk_rates["write_rate"]}
        with self.lock:
            self.current = snapshot
            self.history.append(point)
        self.previous_net, self.previous_disk, self.previous_time = net, disk_io, monotonic

    def sample_loop(self):
        while not self.stop_event.wait(SAMPLE_INTERVAL):
            try:
                self.sample()
            except Exception as exc:
                print(f"Metric collection failed: {exc}", flush=True)

    def ports_loop(self):
        while not self.stop_event.is_set():
            try:
                ports = collect_ports(self.host)
                with self.lock:
                    self.ports = ports
            except Exception as exc:
                with self.lock:
                    self.ports = {"available": False, "loading": False, "items": [],
                                  "updated_at": time.time(), "error": "端口采集失败，请稍后刷新"}
                print(f"Port collection failed: {exc}", flush=True)
            self.stop_event.wait(5)

    def docker_loop(self):
        while not self.stop_event.is_set():
            try:
                if not shutil.which("docker"):
                    raise RuntimeError("未安装 Docker CLI")
                containers = command_json(["docker", "ps", "--all", "--no-trunc", "--format", "{{json .}}"])
                stats = command_json(["docker", "stats", "--all", "--no-stream", "--format", "{{json .}}"], timeout=15) if containers else []
                stats_by_name = {s.get("Name"): s for s in stats}
                rows = []
                for container in containers:
                    name = container.get("Names", "")
                    stat = stats_by_name.get(name, {})
                    rows.append({"id": container["ID"], "name": name, "image": container["Image"],
                                 "state": container["State"], "status": container["Status"],
                                 "ports": container.get("Ports", ""), "created": container.get("CreatedAt", ""),
                                 "cpu": percent_number(stat.get("CPUPerc", "0%")),
                                 "memory_percent": percent_number(stat.get("MemPerc", "0%")),
                                 "memory_usage": stat.get("MemUsage", "—"), "network_io": stat.get("NetIO", "—"),
                                 "block_io": stat.get("BlockIO", "—"), "pids": stat.get("PIDs", "—")})
                docker = {"available": True, "loading": False, "containers": rows, "error": None,
                          "updated_at": time.time(), "running": sum(c["state"] == "running" for c in rows),
                          "stopped": sum(c["state"] != "running" for c in rows)}
                docker["bindings"], docker["ports_error"] = [], None
                running_ids = [c["id"] for c in rows if c["state"] == "running"]
                if running_ids:
                    try:
                        # Read bindings separately so an inspect failure does not hide resource metrics.
                        inspections = []
                        for offset in range(0, len(running_ids), 100):
                            inspections.extend(command_json(["docker", "inspect", "--format", "{{json .}}", *running_ids[offset:offset + 100]]))
                        docker["bindings"] = docker_bindings(inspections)
                    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                        docker["ports_error"] = "Docker 端口映射读取失败，当前仅展示宿主机端口"
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
                message = "Docker 连接失败，请检查服务状态及当前用户的访问权限"
                if isinstance(exc, RuntimeError):
                    message = str(exc)
                docker = {"available": False, "loading": False, "containers": [],
                          "error": message, "updated_at": time.time(), "running": 0, "stopped": 0}
            with self.lock:
                self.docker = docker
            self.stop_event.wait(10)

    def snapshot(self, seconds=900):
        with self.lock:
            result = copy.deepcopy(self.current)
            result["docker"] = copy.deepcopy(self.docker)
            result["ports"] = port_snapshot(self.ports, self.docker)
            cutoff = time.time() - seconds
            result["history"] = [dict(point) for point in self.history if point["timestamp"] >= cutoff]
        return result


def make_handler(monitor, auth, storage=None):
    storage = storage or StorageAnalyzer(host=monitor.host)
    class Handler(BaseHTTPRequestHandler):
        def session(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                name = "monilite_session" if "monilite_session" in cookie else "kong_session"
                token = cookie[name].value if name in cookie else None
            except CookieError:
                token = None
            return auth.session(token)

        def require_session(self):
            session = self.session()
            if not session:
                raise AuthError("请先登录", 401)
            return session

        def require_admin_session(self):
            session = self.require_session()
            if session["user"]["role"] != "admin":
                raise AuthError("目录占用分析仅对管理员开放", 403)
            return session

        def cookie(self, token=None):
            secure = "; Secure" if os.environ.get("MONILITE_SECURE_COOKIE", os.environ.get("KONG_SECURE_COOKIE")) == "1" else ""
            return f"monilite_session={token or ''}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS if token else 0}{secure}"

        def check_origin(self):
            origin = self.headers.get("Origin")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise AuthError("不允许跨站请求", 403)
            if origin:
                parsed_origin = urlsplit(origin)
                if parsed_origin.scheme not in ("http", "https") or parsed_origin.netloc.lower() != self.headers.get("Host", "").lower():
                    raise AuthError("不允许跨站请求", 403)

        def body_json(self):
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise AuthError("请求需使用 application/json", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise AuthError("无效的请求长度")
            if not 0 < length <= 8192:
                raise AuthError("请求体为空或过大", 413)
            self.connection.settimeout(10)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise AuthError("请求体不完整")
                data = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise AuthError("无效的 JSON 请求")
            if not isinstance(data, dict):
                raise AuthError("请求体必须是 JSON 对象")
            return data

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/api/auth/status":
                self.send_json(auth.status(self.session()))
                return
            if parsed.path == "/api/metrics":
                try:
                    self.require_session()
                except AuthError as exc:
                    self.send_json({"error": str(exc)}, exc.status)
                    return
                try:
                    seconds = min(3600, max(60, int(parse_qs(parsed.query).get("seconds", [900])[0])))
                except ValueError:
                    self.send_json({"error": "seconds must be an integer"}, 400)
                    return
                self.send_json(monitor.snapshot(seconds))
                return
            if parsed.path == "/api/admin/users":
                try:
                    session = self.require_session()
                    self.send_json(auth.users(session["user"]["id"]))
                except AuthError as exc:
                    self.send_json({"error": str(exc)}, exc.status)
                return
            if parsed.path == "/api/storage/scan":
                try:
                    session = self.require_admin_session()
                    identifier = parse_qs(parsed.query).get("id", [""])[0]
                    self.send_json(storage.get(identifier, session["user"]["id"]))
                except (AuthError, StorageError) as exc:
                    self.send_json({"error": str(exc)}, exc.status)
                return
            if parsed.path == "/api/health":
                with monitor.lock:
                    timestamp = monitor.current["timestamp"] if monitor.current else 0
                healthy = time.time() - timestamp < 10
                self.send_json({"status": "ok" if healthy else "degraded", "sampled_at": timestamp}, 200 if healthy else 503)
                return
            if parsed.path.startswith("/api/"):
                self.send_json({"error": "Not found"}, 404)
                return
            relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            file = (STATIC / relative).resolve()
            if STATIC not in file.parents or not file.is_file():
                self.send_error(404)
                return
            content = file.read_bytes()
            self.send_response(200)
            self.headers_common()
            self.send_header("Content-Type", (mimetypes.guess_type(str(file))[0] or "application/octet-stream") + ("; charset=utf-8" if file.suffix in (".html", ".js", ".css") else ""))
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            self.mutate()

        def do_PATCH(self):
            self.mutate()

        def do_DELETE(self):
            self.mutate()

        def mutate(self):
            path = urlsplit(self.path).path
            method = self.command
            try:
                self.check_origin()
                public = method == "POST" and path in ("/api/auth/setup", "/api/auth/login", "/api/auth/register")
                session = None
                if public:
                    auth.limiter.check((self.client_address[0], path), limit=8 if path.endswith("login") else 5)
                else:
                    session = self.require_session()
                    csrf = self.headers.get("X-CSRF-Token", "")
                    if not hmac.compare_digest(csrf.encode(), session["csrf_token"].encode()):
                        raise AuthError("请求验证失败，请刷新页面重试", 403)
                data = self.body_json()
                if public:
                    username, password = data.get("username"), data.get("password")
                    if path.endswith("register"):
                        self.send_json(auth.register(username, password), 201)
                    else:
                        result, token = auth.setup(username, password) if path.endswith("setup") else auth.login(username, password)
                        self.send_json(result, 201 if path.endswith("setup") else 200, self.cookie(token))
                    return
                actor = session["user"]["id"]
                if method == "POST" and path == "/api/storage/scan":
                    if session["user"]["role"] != "admin":
                        raise AuthError("目录占用分析仅对管理员开放", 403)
                    self.send_json(storage.start(actor, data.get("path", "/"), data.get("force", False)), 202)
                elif method == "POST" and path == "/api/auth/logout":
                    auth.logout(session)
                    self.send_json({"message": "已退出登录"}, cookie=self.cookie())
                elif method == "POST" and path == "/api/auth/password":
                    auth.limiter.check((actor, "password-change"), limit=8)
                    result, token = auth.change_password(session, data.get("current_password"), data.get("new_password"))
                    self.send_json(result, cookie=self.cookie(token))
                elif method == "POST" and path == "/api/admin/users":
                    self.send_json({"user": auth.create_user(actor, data.get("username"), data.get("password"), data.get("role", "viewer"))}, 201)
                elif method == "PATCH" and path == "/api/admin/settings":
                    self.send_json(auth.set_registration(actor, data.get("registration_enabled")))
                elif path.startswith("/api/admin/users/") and method in ("PATCH", "DELETE"):
                    try:
                        user_id = int(path.rsplit("/", 1)[1])
                    except ValueError:
                        raise AuthError("无效的用户 ID")
                    if method == "PATCH":
                        self.send_json({"user": auth.update_user(actor, user_id, data)})
                    else:
                        auth.delete_user(actor, user_id)
                        self.send_json({"message": "用户已删除"})
                else:
                    raise AuthError("接口不存在", 404)
            except (AuthError, StorageError) as exc:
                self.send_json({"error": str(exc)}, exc.status)
            except TimeoutError:
                self.send_json({"error": "请求超时"}, 408)

        def headers_common(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")

        def send_json(self, data, status=200, cookie=None):
            content = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.headers_common()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                # Clear the legacy cookie on login, password changes and logout.
                self.send_header("Set-Cookie", "kong_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, fmt, *args):
            if len(args) > 1 and str(args[1]) not in ("200", "304"):
                super().log_message(fmt, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="MoniLite server monitor")
    parser.add_argument("--host", default=os.environ.get("MONILITE_HOST", os.environ.get("KONG_HOST", "0.0.0.0")))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MONILITE_PORT", os.environ.get("KONG_PORT", "8080"))))
    args = parser.parse_args()
    host = configure_host()
    monitor = Monitor(host)
    auth = AuthStore(os.environ.get("MONILITE_DATA_DIR", os.environ.get("KONG_DATA_DIR", str(ROOT / "data"))))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(monitor, auth))
    monitor.start()
    print(f"MoniLite is listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
