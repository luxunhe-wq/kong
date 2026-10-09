"""Capture real dashboard screenshots using a temporary account and database.

KONG_TEST_BROWSER=/path/to/chromium python3 tests/capture_screenshots.py
"""
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auth import AuthStore
from server import Monitor, make_handler


def main():
    output = ROOT / 'docs' / 'screenshots'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kong-screenshots-') as directory:
        files = Path(directory) / 'files'
        (files / 'logs').mkdir(parents=True)
        (files / 'backups').mkdir()
        (files / 'cache').mkdir()
        (files / 'logs' / 'application.log').write_bytes(b'l' * (1024 * 1024))
        (files / 'backups' / 'daily-backup.bin').write_bytes(b'b' * (8 * 1024 * 1024))
        (files / 'cache' / 'download.bin').write_bytes(b'c' * (2 * 1024 * 1024))
        auth = AuthStore(Path(directory) / 'account')
        monitor = Monitor()
        http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(monitor, auth))
        monitor.start()
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True, executable_path=os.environ.get('KONG_TEST_BROWSER'), args=['--no-sandbox'])
                context = browser.new_context(viewport={'width':1440, 'height':1000}, device_scale_factor=1)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{http.server_port}'
                page.goto(base)
                page.locator('#auth-username').fill('preview_admin')
                page.locator('#auth-password').fill('screenshot-preview-password')
                page.locator('#auth-confirm').fill('screenshot-preview-password')
                page.locator('#auth-form button[type="submit"]').click()
                expect(page.locator('.metric-card')).to_have_count(4)
                page.wait_for_function('() => state.data.history.length >= 20 && !state.data.docker.loading', timeout=60000)
                print('Captured 20 real metric samples; Docker collection finished.', flush=True)

                def capture(name):
                    page.evaluate('window.scrollTo(0,0)')
                    page.evaluate('document.fonts.ready')
                    expect(page.locator('#connection-error')).to_be_hidden()
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    page.screenshot(path=str(output / name), full_page=True, animations='disabled',
                                    mask=[page.locator('.host-card strong')], mask_color='#b9c5cf')

                capture('overview-light.png')
                page.locator('a[data-page="settings"]').click()
                page.locator('[data-theme="dark"]').click()
                page.locator('a[data-page="overview"]').click()
                capture('overview-dark.png')
                page.locator('a[data-page="settings"]').click()
                page.locator('[data-theme="light"]').click()
                page.locator('a[data-page="docker"]').click()
                expect(page.locator('#docker-table-body tr[data-container]').first).to_be_visible()
                capture('docker.png')
                page.goto(base + '/#resources/disk?path=' + quote(str(files), safe=''))
                expect(page.locator('#storage-result')).to_be_visible(timeout=10000)
                capture('disk-analysis.png')
                page.goto(base + '/#resources/memory')
                expect(page.locator('.chart-panel')).to_have_count(1)
                capture('memory.png')
                page.goto(base + '/#overview')
                page.set_viewport_size({'width':390, 'height':844})
                expect(page.locator('.metric-card')).to_have_count(4)
                capture('overview-mobile.png')
                assert not errors, errors
                browser.close()
                print('Saved six real screenshots to docs/screenshots; no JavaScript errors.', flush=True)
        finally:
            monitor.stop_event.set()
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == '__main__':
    main()
