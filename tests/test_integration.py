#!/usr/bin/env python3
"""Integration tests for HTTP endpoints."""
import json
import os
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import state, download_jobs, probe_meta_jobs
from server_main import Handler

PORT = 18766  # Use different port for tests


class TestHTTPEndpoints(unittest.TestCase):
    """Integration tests for HTTP handler."""

    @classmethod
    def setUpClass(cls):
        """Start test server in background thread."""
        import http.server

        TEST_PORT = 18766
        state.reset_setup()
        state.set_phase("done")
        state.set_python_ok(True)
        state.set_ytdlp_ok(True)
        state.set_server_started(True)

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)  # Wait for server to start

    @classmethod
    def tearDownClass(cls):
        """Shutdown test server."""
        cls.server.shutdown()

    def setUp(self):
        """Reset state before each test."""
        state.reset_setup()
        state.set_phase("done")
        state.set_python_ok(True)
        state.set_ytdlp_ok(True)
        state.set_server_started(True)
        download_jobs.clear()
        probe_meta_jobs.clear()

    def _request(self, method, path, body=None, headers=None):
        """Make HTTP request to test server."""
        conn = HTTPConnection("127.0.0.1", PORT, timeout=5)
        try:
            if body and isinstance(body, dict):
                body = json.dumps(body)
                headers = headers or {}
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read().decode()
            return resp.status, json.loads(data) if data else {}
        finally:
            conn.close()

    # ── /status ─────────────────────────────────────────────────────

    def test_status_endpoint_returns_json(self):
        """GET /status should return JSON with expected fields."""
        status, data = self._request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertIn("phase", data)
        self.assertIn("progress", data)
        self.assertIn("messages", data)
        self.assertIn("python_ok", data)
        self.assertIn("ytdlp_ok", data)
        self.assertIn("setup_done", data)

    def test_status_cors_headers(self):
        """GET /status should include CORS headers."""
        conn = HTTPConnection("127.0.0.1", 18766, timeout=5)
        try:
            conn.request("GET", "/status")
            resp = conn.getresponse()
            self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), "*")
        finally:
            conn.close()

    # ── /probe ──────────────────────────────────────────────────────

    def test_probe_requires_url(self):
        """GET /probe without URL should return error."""
        status, data = self._request("GET", "/probe")
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    def test_probe_invalid_url(self):
        """GET /probe with invalid URL should return error."""
        status, data = self._request("GET", "/probe?url=not-a-url")
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    def test_probe_rate_limit(self):
        """GET /probe should rate-limit after 3 rapid requests."""
        # Make 4 rapid requests (limit is 3)
        for i in range(3):
            status, data = self._request("GET", "/probe?url=not-a-url")
            self.assertEqual(status, 200)

        # 4th request should be rate limited
        status, data = self._request("GET", "/probe?url=not-a-url")
        self.assertEqual(status, 200)
        self.assertIn("Rate limit", data.get("error", ""))

    # ── /probe-meta ─────────────────────────────────────────────────

    def test_probe_meta_requires_url(self):
        """GET /probe-meta without URL should return error."""
        status, data = self._request("GET", "/probe-meta")
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    def test_probe_meta_returns_job_id(self):
        """GET /probe-meta should return job_id."""
        with patch("server.download._popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0

            status, data = self._request("GET", "/probe-meta?url=https://example.com/video&format_id=137&duration=100")
            self.assertEqual(status, 200)
            self.assertIn("job_id", data)
            self.assertEqual(len(data["job_id"]), 8)

    def test_probe_meta_status_running(self):
        """GET /probe-meta-status should return running status for new job."""
        with patch("server.download._popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0

            _, data = self._request("GET", "/probe-meta?url=https://example.com/video&format_id=137")
            jid = data["job_id"]

            # Immediately check status — should be running
            status, status_data = self._request("GET", f"/probe-meta-status?id={jid}")
            self.assertEqual(status, 200)
            self.assertIn("status", status_data)

    def test_probe_meta_status_not_found(self):
        """GET /probe-meta-status with invalid id should return error."""
        status, data = self._request("GET", "/probe-meta-status?id=nonexistent")
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    # ── /download ───────────────────────────────────────────────────

    def test_download_requires_url(self):
        """POST /download without URL should return error."""
        status, data = self._request("POST", "/download", body={})
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    def test_download_rate_limit(self):
        """POST /download should rate-limit after 2 rapid requests."""
        with patch("server.download._popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0
            mock_proc.stdout = None
            mock_proc.poll.return_value = 0

            # Make 2 rapid requests (limit is 2)
            for i in range(2):
                status, data = self._request("POST", "/download", body={"url": "https://example.com/video"})
                self.assertEqual(status, 200)

            # 3rd request should be rate limited
            status, data = self._request("POST", "/download", body={"url": "https://example.com/video"})
            self.assertEqual(status, 200)
            self.assertIn("Rate limit", data.get("error", ""))

    # ── /cancel ─────────────────────────────────────────────────────

    def test_cancel_no_active_download(self):
        """POST /cancel with no active download should return ok."""
        status, data = self._request("GET", "/cancel")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))

    # ── /log ────────────────────────────────────────────────────────

    def test_log_job_not_found(self):
        """GET /log with invalid job id should return error."""
        status, data = self._request("GET", "/log?job=nonexistent")
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    def test_log_returns_lines(self):
        """GET /log should return log lines for existing job."""
        jid = "test123"
        download_jobs[jid] = {"log": ["line1", "line2"], "status": "running"}

        status, data = self._request("GET", f"/log?job={jid}")
        self.assertEqual(status, 200)
        self.assertEqual(data["lines"], ["line1", "line2"])
        self.assertEqual(data["status"], "running")

    # ── /cookies ────────────────────────────────────────────────────

    def test_cookies_clear(self):
        """POST /download with empty body should clear cookies."""
        status, data = self._request("POST", "/cookies", body={})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIsNone(data.get("path"))

    def test_cookies_save_content(self):
        """POST /cookies with content should save and return path."""
        import base64
        content = base64.b64encode(b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tname\tvalue").decode()

        status, data = self._request("POST", "/cookies", body={"content": content})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn(".cookies.txt", data.get("path", ""))

    def test_cookies_invalid_format(self):
        """POST /cookies with invalid format should return error."""
        import base64
        content = base64.b64encode(b"not a cookie file at all").decode()

        status, data = self._request("POST", "/cookies", body={"content": content})
        self.assertEqual(status, 200)
        self.assertIn("error", data)

    # ── /setup ──────────────────────────────────────────────────────

    def test_setup_already_done(self):
        """POST /setup when already done should return already_done."""
        state.set_phase("done")
        # Create marker in the expected location (current working directory)
        marker = os.path.join(os.getcwd(), ".setup_done")
        with open(marker, "w") as f:
            f.write("test")

        try:
            status, data = self._request("POST", "/setup")
            self.assertEqual(status, 200)
            self.assertTrue(data.get("already_done"))
        finally:
            if os.path.exists(marker):
                os.remove(marker)

    # ── 404 ─────────────────────────────────────────────────────────

    def test_unknown_endpoint_returns_404(self):
        """GET /unknown should return 404."""
        conn = HTTPConnection("127.0.0.1", 18766, timeout=5)
        try:
            conn.request("GET", "/unknown")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
        finally:
            conn.close()


class TestAppStateThreadSafety(unittest.TestCase):
    """Test thread safety of AppState."""

    def test_concurrent_add_message(self):
        """Concurrent add_message calls should not lose messages."""
        from server.state import AppState

        s = AppState()
        threads = []
        errors = []

        def add_messages(n):
            try:
                for i in range(n):
                    s.add_message(f"msg-{n}-{i}")
            except Exception as e:
                errors.append(e)

        for n in range(10):
            t = threading.Thread(target=add_messages, args=(100,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(s.setup_messages), 1000)

    def test_concurrent_get_set(self):
        """Concurrent get/set should not crash."""
        from server.state import AppState

        s = AppState()
        errors = []

        def writer():
            try:
                for i in range(100):
                    s.set_phase("checking" if i % 2 == 0 else "done")
                    s.set_progress(i)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    resp = s.get_setup_response()
                    self.assertIn("phase", resp)
                    self.assertIn("progress", resp)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
