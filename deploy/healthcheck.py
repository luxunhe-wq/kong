"""Container health check, including custom ports and loopback-only listeners."""
import os
from urllib.request import ProxyHandler, build_opener


host = os.environ.get("KONG_HOST", "0.0.0.0")
if host == "0.0.0.0":
    host = "127.0.0.1"
elif host == "::":
    host = "::1"
if ":" in host:
    host = f"[{host}]"
port = int(os.environ.get("KONG_PORT", "8080"))
with build_opener(ProxyHandler({})).open(f"http://{host}:{port}/api/health", timeout=3) as response:
    if response.status != 200:
        raise SystemExit(1)
