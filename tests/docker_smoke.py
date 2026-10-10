"""Build first with docker compose build, then run this isolated Docker deployment check."""
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import operating_system
from auth import AuthStore


def check_compose(compose_file, legacy=None):
    project = f"monilite-smoke-{os.getpid()}"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    env = {**os.environ, "MONILITE_DATA_VOLUME":f"{project}_monilite-data", "MONILITE_DATA_VOLUME_EXTERNAL":"false", "MONILITE_HOST":"127.0.0.1", "MONILITE_PORT":str(port), "MONILITE_IMAGE":"monilite:local"}
    if legacy:
        env.update(MONILITE_DATA_VOLUME=legacy['volume'], MONILITE_DATA_VOLUME_EXTERNAL="true")

    def compose(*args, capture=False):
        result = subprocess.run(["docker", "compose", "-f", str(compose_file), "-p", project, *args], cwd=compose_file.parent,
                                env=env, check=True, text=True, capture_output=capture, timeout=100)
        return result.stdout.strip() if capture else None

    client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = legacy['session']['csrf_token'] if legacy else None

    def request(path, body=None):
        headers = {"Content-Type":"application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if legacy:
            headers['Cookie'] = 'kong_session=' + legacy['token']
        request = Request(f"http://127.0.0.1:{port}{path}", headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        with client.open(request, timeout=10) as response:
            return json.load(response)

    def wait_for(predicate, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.5)
        raise AssertionError("Container check timed out")

    try:
        compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "60")
        if legacy:
            assert request('/api/auth/status')['initialized']
            assert request('/api/auth/status')['user']['username'] == 'legacy_owner'
            print('Legacy Compose volume, account database and session are preserved.', flush=True)
        else:
            assert not request('/api/auth/status')['initialized']
            account = request('/api/auth/setup', {"username":"docker_owner", "password":"docker-owner-password"})
            csrf = account['csrf_token']
        metrics = request('/api/metrics')
        assert metrics['system']['hostname'] == socket.gethostname()
        assert metrics['system']['os'] == operating_system()
        assert metrics['system']['cpu_logical'] == psutil.cpu_count()
        assert metrics['memory']['total'] == psutil.virtual_memory().total
        assert metrics['disks'][0]['mount'] == '/'
        assert metrics['disks'][0]['total'] == psutil.disk_usage('/').total
        assert {i['name'] for i in metrics['network']['interfaces']} == set(psutil.net_if_addrs()) - {'lo'}
        wait_for(lambda: request('/api/metrics')['timestamp'] > metrics['timestamp'])
        print('Custom port, health check, host metadata, CPU, memory, disk and live network metrics passed.', flush=True)

        with socket.socket() as marker:
            marker.bind(('127.0.0.1', 0))
            marker.listen()
            bound_port = marker.getsockname()[1]

            def socket_visible():
                return next((row for row in request('/api/metrics')['ports']['items']
                             if row['port'] == bound_port and row['protocol'] == 'tcp'), None)

            row = wait_for(socket_visible)
            assert any(p['pid'] == os.getpid() and p['executable'] for p in row['processes']), row
        container = compose('ps', '-q', 'monilite', capture=True)
        wait_for(lambda: any(c['id'] == container for c in request('/api/metrics')['docker']['containers']))
        print('Host listening ports with process attribution and Docker container discovery passed.', flush=True)

        with tempfile.TemporaryDirectory(prefix='monilite-docker-files-') as directory:
            files = Path(directory)
            (files / 'sample.bin').write_bytes(b'x' * 65536)
            job = request('/api/storage/scan', {'path':directory})

            def scan_complete():
                result = request('/api/storage/scan?id=' + job['id'])
                return result if result['status'] != 'running' else None

            analysis = wait_for(scan_complete)
            assert analysis['status'] == 'complete', analysis
            assert analysis['path'] == directory
            assert analysis['items'][0]['path'] == str(files / 'sample.bin')
            assert analysis['items'][0]['logical_size'] == 65536
            compose('exec', '-T', 'monilite', 'python', '-c',
                    "import os; assert os.statvfs('/host').f_flag & os.ST_RDONLY; "
                    "assert os.statvfs('/app').f_flag & os.ST_RDONLY")
        print('Host directory analysis returns original paths; host and container roots are read-only.', flush=True)

        compose('up', '-d', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '60')
        assert request('/api/auth/status')['initialized']
        assert request('/api/metrics')['system']['hostname'] == socket.gethostname()
        print('Account and login session survive container recreation. Docker checks passed.', flush=True)
    finally:
        compose('logs', '--tail', '20')
        compose('down', '--volumes')


def main():
    # Image-only deployments must work with a downloaded Compose file and no source/Dockerfile.
    with tempfile.TemporaryDirectory(prefix='monilite-compose-') as directory:
        compose_file = Path(directory) / 'compose.yaml'
        shutil.copyfile(ROOT / 'compose.yaml', compose_file)
        check_compose(compose_file)
        # A real old-style, Compose-labelled volume must also work with the new project.
        volume = f'monilite-legacy-smoke-{os.getpid()}'
        seed = Path(directory) / 'seed'
        auth = AuthStore(seed)
        session, token = auth.setup('legacy_owner', 'legacy-owner-password')
        auth.path.rename(seed / 'kong.sqlite3')
        subprocess.run(['docker', 'volume', 'create', '--label', 'com.docker.compose.project=kong',
                        '--label', 'com.docker.compose.volume=kong-data', volume], check=True)
        try:
            subprocess.run(['docker', 'run', '--rm', '--network', 'none',
                            '--mount', f'type=bind,src={seed},dst=/seed,readonly',
                            '--mount', f'type=volume,src={volume},dst=/target',
                            'monilite:local', 'python', '-c',
                            "import shutil; shutil.copy('/seed/kong.sqlite3', '/target/kong.sqlite3')"], check=True)
            check_compose(compose_file, {'volume':volume, 'session':session, 'token':token})
        finally:
            subprocess.run(['docker', 'volume', 'rm', volume], check=True)


if __name__ == '__main__':
    main()
