"""Read-only host socket inventory and Docker published-port attribution."""
import copy
import ipaddress
import socket
import time
from pathlib import Path

import psutil


def address_scope(address):
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if ip.is_loopback:
            return "local"
        return "all" if ip.is_unspecified else "interface"
    except ValueError:
        return "interface"


def endpoint(protocol, address, port):
    return f"{protocol}|{address}|{port}"


def process_details(pid, host=None):
    result = {"pid": pid, "name": "未识别", "user": "—", "executable": "", "service": "", "limited": True}
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            result.update(name=proc.name(), user=host.username(proc.uids().real) if host else proc.username(), limited=False)
            try:
                result["executable"] = proc.exe()
            except (psutil.Error, OSError):
                pass
        try:
            groups = Path(psutil.PROCFS_PATH, str(pid), "cgroup").read_text().splitlines()
            services = [part for line in groups for part in line.split(":", 2)[-1].split("/") if part.endswith(".service")]
            result["service"] = services[-1] if services else ""
        except OSError:
            pass
    except (psutil.Error, OSError):
        pass
    return result


def collect_ports(host=None):
    result = {"loading": False, "available": True, "error": None, "updated_at": time.time(), "items": []}
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        result.update(available=False, error="无法读取监听端口，请检查服务的系统读取权限")
        return result
    rows, processes = {}, {}
    for connection in connections:
        if not connection.laddr:
            continue
        if connection.type == socket.SOCK_STREAM:
            if connection.status != psutil.CONN_LISTEN:
                continue
            protocol, status = "tcp", "listening"
        elif connection.type == socket.SOCK_DGRAM:
            # Connected UDP clients are outbound traffic, not bound service endpoints.
            if connection.raddr:
                continue
            protocol, status = "udp", "bound"
        else:
            continue
        address, port = connection.laddr[:2]
        key = endpoint(protocol, address, port)
        row = rows.setdefault(key, {
            "id": key, "protocol": protocol, "address": address, "port": port,
            "family": "IPv6" if connection.family == socket.AF_INET6 else "IPv4",
            "scope": address_scope(address), "status": status, "source": "host",
            "processes": [], "containers": [],
        })
        if connection.pid is not None:
            if connection.pid not in processes:
                processes[connection.pid] = process_details(connection.pid, host) if host else process_details(connection.pid)
            if not any(p["pid"] == connection.pid for p in row["processes"]):
                row["processes"].append(processes[connection.pid])
    result["items"] = sorted(rows.values(), key=lambda row: (row["port"], row["protocol"], row["address"]))
    return result


def docker_bindings(inspections):
    """Use actual published bindings; EXPOSE alone never means a host port is open."""
    bindings = []
    for container in inspections:
        if not container.get("State", {}).get("Running"):
            continue
        for target, addresses in (container.get("NetworkSettings", {}).get("Ports") or {}).items():
            try:
                target_port, protocol = target.split("/", 1)
                target_port = int(target_port)
            except (ValueError, AttributeError):
                continue
            if protocol not in ("tcp", "udp"):
                continue
            for binding in addresses or []:
                try:
                    host_port = int(binding["HostPort"])
                    address = binding.get("HostIp") or "0.0.0.0"
                    ipaddress.ip_address(address)
                    if not 0 < host_port < 65536:
                        continue
                except (KeyError, ValueError, TypeError):
                    continue
                bindings.append({"id": container["Id"], "name": container.get("Name", "").lstrip("/"),
                                 "image": container.get("Config", {}).get("Image", ""),
                                 "address": address, "port": host_port, "protocol": protocol,
                                 "target_port": target_port})
    return bindings


def port_snapshot(collected, docker):
    result = copy.deepcopy(collected)
    rows = {row["id"]: row for row in result["items"]}
    for binding in docker.get("bindings", []):
        address, protocol, port = binding["address"], binding["protocol"], binding["port"]
        key = endpoint(protocol, address, port)
        row = rows.setdefault(key, {
            "id": key, "address": address, "protocol": protocol, "port": port,
            "family": "IPv6" if ":" in address else "IPv4", "scope": address_scope(address),
            "status": "published", "source": "docker", "processes": [], "containers": [],
        })
        row["containers"].append(copy.deepcopy(binding))
    result["items"] = sorted(rows.values(), key=lambda row: (row["port"], row["protocol"], row["address"]))
    result["docker_updated_at"] = docker.get("updated_at")
    result["docker_error"] = docker.get("ports_error") or (docker.get("error") if not docker.get("loading") else None)
    result["docker_loading"] = docker.get("loading", False)
    return result
