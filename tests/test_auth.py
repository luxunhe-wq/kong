import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

from auth import AuthError, AuthStore, password_matches
from server import Monitor, make_handler


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.monitor = Monitor()
        cls.monitor.sample()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthStore(self.directory.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.monitor, self.auth))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, method="GET", body=None, session=None, headers=None):
        request_headers = dict(headers or {})
        if session:
            request_headers["Cookie"] = session["cookie"]
            request_headers.setdefault("X-CSRF-Token", session["csrf_token"])
        if method != "GET":
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=10)
        connection.request(method, path, json.dumps(body or {}) if method != "GET" else None, request_headers)
        response = connection.getresponse()
        status, response_headers, payload = response.status, dict(response.getheaders()), json.loads(response.read())
        connection.close()
        if "Set-Cookie" in response_headers:
            payload["cookie"] = response_headers["Set-Cookie"].split(";", 1)[0]
        return status, payload, response_headers

    def setup_admin(self):
        status, session, _ = self.request("/api/auth/setup", "POST", {"username": "owner", "password": "owner-test-password"})
        self.assertEqual(status, 201)
        return session

    def create_viewer(self, admin, username="reader"):
        status, payload, _ = self.request("/api/admin/users", "POST", {"username": username, "password": "reader-test-password"}, admin)
        self.assertEqual(status, 201)
        return payload["user"]

    def login(self, username="reader", password="reader-test-password"):
        return self.request("/api/auth/login", "POST", {"username": username, "password": password})

    def test_first_admin_setup_and_persistence(self):
        status, before, _ = self.request("/api/auth/status")
        self.assertFalse(before["initialized"])
        self.assertEqual(self.request("/api/metrics")[0], 401)
        admin = self.setup_admin()
        self.assertEqual(admin["user"]["role"], "admin")
        self.assertNotIn("password_hash", admin["user"])
        self.assertEqual(self.request("/api/metrics", session=admin)[0], 200)
        self.assertEqual(self.request("/api/auth/setup", "POST", {"username":"hijacker", "password":"not-the-admin-password"})[0], 409)
        persisted = AuthStore(self.directory.name)
        self.assertTrue(persisted.status()["initialized"])
        token = admin["cookie"].split("=", 1)[1]
        self.assertEqual(persisted.session(token)["user"]["username"], "owner")
        with persisted.connection() as db:
            encoded = db.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("owner-test-password", encoded)
        self.assertTrue(password_matches("owner-test-password", encoded))

    def test_concurrent_setup_has_only_one_winner(self):
        def setup(username):
            try:
                self.auth.setup(username, "concurrent-test-password")
                return 201
            except AuthError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(setup, ["owner_one", "owner_two"]))
        self.assertEqual(sorted(statuses), [201, 409])
        with self.auth.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_registration_requires_approval_and_cannot_choose_admin(self):
        admin = self.setup_admin()
        status, _, _ = self.request("/api/auth/register", "POST", {"username":"new_reader", "password":"reader-test-password", "role":"admin", "status":"active"})
        self.assertEqual(status, 201)
        self.assertEqual(self.login("new_reader")[0], 403)
        status, listing, _ = self.request("/api/admin/users", session=admin)
        pending = next(u for u in listing["users"] if u["username"] == "new_reader")
        self.assertEqual((pending["role"], pending["status"]), ("viewer", "pending"))
        self.assertEqual(self.request(f'/api/admin/users/{pending["id"]}', "PATCH", {"status":"active"}, admin)[0], 200)
        self.assertEqual(self.login("new_reader")[0], 200)

    def test_viewer_cannot_manage_users_or_registration(self):
        admin = self.setup_admin()
        self.create_viewer(admin)
        _, viewer, _ = self.login()
        self.assertEqual(self.request("/api/metrics", session=viewer)[0], 200)
        self.assertEqual(self.request("/api/admin/users", session=viewer)[0], 403)
        self.assertEqual(self.request("/api/admin/users", "POST", {"username":"intruder", "password":"reader-test-password", "role":"admin"}, viewer)[0], 403)
        self.assertEqual(self.request("/api/admin/settings", "PATCH", {"registration_enabled":False}, viewer)[0], 403)

    def test_csrf_origin_and_password_validation(self):
        admin = self.setup_admin()
        self.assertEqual(self.request("/api/admin/settings", "PATCH", {"registration_enabled":False}, admin, {"X-CSRF-Token":"invalid"})[0], 403)
        self.assertEqual(self.request("/api/admin/settings", "PATCH", {"registration_enabled":False}, admin, {"Origin":"https://other-site.example"})[0], 403)
        self.assertEqual(self.request("/api/auth/register", "POST", {"username":"reader", "password":"123"})[0], 400)
        self.assertEqual(self.request("/api/auth/login", "POST", {"username":"' OR 1=1 --", "password":"invalid-password"})[0], 401)
        self.assertTrue(self.auth.status()["registration_enabled"])

    def test_disable_and_reset_revoke_existing_sessions(self):
        admin = self.setup_admin()
        user = self.create_viewer(admin)
        _, viewer, _ = self.login()
        path = f'/api/admin/users/{user["id"]}'
        self.assertEqual(self.request(path, "PATCH", {"status":"disabled"}, admin)[0], 200)
        self.assertEqual(self.request("/api/metrics", session=viewer)[0], 401)
        self.assertEqual(self.login()[0], 403)
        self.request(path, "PATCH", {"status":"active"}, admin)
        _, viewer, _ = self.login()
        self.assertEqual(self.request(path, "PATCH", {"password":"new-reader-password"}, admin)[0], 200)
        self.assertEqual(self.request("/api/metrics", session=viewer)[0], 401)
        self.assertEqual(self.login()[0], 401)
        self.assertEqual(self.login(password="new-reader-password")[0], 200)

    def test_password_change_keeps_new_session_and_revokes_old_ones(self):
        admin = self.setup_admin()
        _, second, _ = self.login("owner", "owner-test-password")
        status, new, _ = self.request("/api/auth/password", "POST", {"current_password":"owner-test-password", "new_password":"new-owner-password"}, admin)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/metrics", session=admin)[0], 401)
        self.assertEqual(self.request("/api/metrics", session=second)[0], 401)
        self.assertEqual(self.request("/api/metrics", session=new)[0], 200)
        self.assertEqual(self.login("owner", "owner-test-password")[0], 401)
        self.assertEqual(self.login("owner", "new-owner-password")[0], 200)

    def test_cannot_delete_or_disable_current_admin(self):
        admin = self.setup_admin()
        path = f'/api/admin/users/{admin["user"]["id"]}'
        self.assertEqual(self.request(path, "DELETE", {}, admin)[0], 409)
        self.assertEqual(self.request(path, "PATCH", {"role":"viewer"}, admin)[0], 409)
        self.assertEqual(self.request(path, "PATCH", {"status":"disabled"}, admin)[0], 409)
        self.assertEqual(self.request("/api/metrics", session=admin)[0], 200)

    def test_registration_switch_uniqueness_and_deletion(self):
        admin = self.setup_admin()
        user = self.create_viewer(admin)
        self.assertEqual(self.request("/api/admin/users", "POST", {"username":"READER", "password":"reader-test-password"}, admin)[0], 409)
        self.assertEqual(self.request("/api/admin/settings", "PATCH", {"registration_enabled":False}, admin)[0], 200)
        self.assertFalse(self.request("/api/auth/status")[1]["registration_enabled"])
        self.assertEqual(self.request("/api/auth/register", "POST", {"username":"another_reader", "password":"reader-test-password"})[0], 403)
        _, viewer, _ = self.login()
        self.assertEqual(self.request(f'/api/admin/users/{user["id"]}', "DELETE", {}, admin)[0], 200)
        self.assertEqual(self.request("/api/metrics", session=viewer)[0], 401)
        self.assertEqual(self.login()[0], 401)

    def test_logout_expiration_cookie_flags_and_rate_limit(self):
        status, admin, headers = self.request("/api/auth/setup", "POST", {"username":"owner", "password":"owner-test-password"})
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertEqual(self.request("/api/auth/logout", "POST", {}, admin)[0], 200)
        self.assertEqual(self.request("/api/metrics", session=admin)[0], 401)
        _, session, _ = self.login("owner", "owner-test-password")
        with self.auth.connection(write=True) as db:
            db.execute("UPDATE sessions SET expires_at=0")
        self.assertEqual(self.request("/api/metrics", session=session)[0], 401)
        for _ in range(7):
            self.assertEqual(self.login("owner", "wrong-password")[0], 401)
        self.assertEqual(self.login("owner", "wrong-password")[0], 429)


if __name__ == "__main__":
    unittest.main()
