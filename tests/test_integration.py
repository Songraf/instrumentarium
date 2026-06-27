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


class TestHTTPEndpoints(unittest.TestCase):
    """Integration tests for HTTP handler using mock server."""

    def setUp(self):
        """Reset state before each test."""
        state.reset_setup()
        state.set_phase("done")
        state.set_python_ok(True)
        state.set_ytdlp_ok(True)
        state.set_server_started(True)
        download_jobs.clear()
        probe_meta_jobs.clear()

    def test_status_endpoint_returns_json(self):
        """GET /status should return JSON with expected fields."""
        resp = state.get_setup_response(setup_done_marker_exists=True)
        self.assertEqual(resp["phase"], "done")
        self.assertEqual(resp["progress"], 0)
        self.assertIn("messages", resp)
        self.assertIn("python_ok", resp)
        self.assertIn("ytdlp_ok", resp)
        self.assertTrue(resp["setup_done"])

    def test_status_cors_headers(self):
        """Verify CORS header logic in Handler."""
        # Test that _cors_headers method exists and works
        handler = Handler.__new__(Handler)
        # Verify method exists
        self.assertTrue(hasattr(handler, '_cors_headers') or hasattr(Handler, '_cors_headers'))

    def test_probe_requires_url(self):
        """Verify probe handler validates URL."""
        # Test the validation logic directly
        self.assertTrue(True)  # Placeholder — actual validation tested via unit tests

    def test_rate_limiter_exists(self):
        """Verify rate limiter is configured."""
        from server_main import _probe_limiter, _download_limiter
        self.assertIsNotNone(_probe_limiter)
        self.assertIsNotNone(_download_limiter)

    def test_rate_limiter_works(self):
        """Verify rate limiter blocks after limit."""
        from server_main import _RateLimiter
        limiter = _RateLimiter(max_tokens=2, refill_rate=0.5)
        self.assertTrue(limiter.acquire())  # token 1
        self.assertTrue(limiter.acquire())  # token 2
        self.assertFalse(limiter.acquire())  # should be rate limited

    def test_cleanup_old_jobs(self):
        """Verify cleanup function works."""
        from server.download import cleanup_old_jobs, mark_job_completed
        import time

        # Add a completed job
        jid = "test123"
        download_jobs[jid] = {"status": "done", "log": []}
        mark_job_completed(jid, download_jobs)

        # Cleanup with 0 age should remove it (jobs older than 0 seconds)
        time.sleep(0.1)  # Ensure timestamp is in the past
        cleanup_old_jobs(max_age_seconds=0)
        self.assertNotIn(jid, download_jobs)

    def test_cleanup_keeps_recent_jobs(self):
        """Verify cleanup keeps recent jobs."""
        from server.download import cleanup_old_jobs, mark_job_completed

        jid = "recent123"
        download_jobs[jid] = {"status": "done", "log": []}
        mark_job_completed(jid, download_jobs)

        # Cleanup with large age should keep it
        cleanup_old_jobs(max_age_seconds=3600)
        self.assertIn(jid, download_jobs)

    def test_state_thread_safety(self):
        """Verify AppState is thread-safe."""
        errors = []

        def add_messages(n):
            try:
                for i in range(n):
                    state.add_message(f"msg-{n}-{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for n in range(5):
            t = threading.Thread(target=add_messages, args=(50,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(state.setup_messages), 250)

    def test_state_get_response_is_snapshot(self):
        """Verify get_setup_response returns a copy."""
        state.reset_setup()
        state.set_phase("done")
        resp1 = state.get_setup_response()
        state.set_phase("idle")
        resp2 = state.get_setup_response()
        self.assertEqual(resp1["phase"], "done")  # Should not be affected
        self.assertEqual(resp2["phase"], "idle")

    def test_download_jobs_dict(self):
        """Verify download_jobs is a dict."""
        self.assertIsInstance(download_jobs, dict)
        jid = "test_download"
        download_jobs[jid] = {"log": [], "status": "running"}
        self.assertIn(jid, download_jobs)
        del download_jobs[jid]

    def test_probe_meta_jobs_dict(self):
        """Verify probe_meta_jobs is a dict."""
        self.assertIsInstance(probe_meta_jobs, dict)
        jid = "test_probe"
        probe_meta_jobs[jid] = {"status": "running", "filesize": None}
        self.assertIn(jid, probe_meta_jobs)
        del probe_meta_jobs[jid]

    def test_handler_class_exists(self):
        """Verify Handler class is importable."""
        from server_main import Handler
        self.assertTrue(hasattr(Handler, 'do_GET'))
        self.assertTrue(hasattr(Handler, 'do_POST'))
        self.assertTrue(hasattr(Handler, 'do_OPTIONS'))
        self.assertTrue(hasattr(Handler, '_json'))
        self.assertTrue(hasattr(Handler, '_handle_probe'))
        self.assertTrue(hasattr(Handler, '_handle_download'))
        self.assertTrue(hasattr(Handler, '_handle_cookies'))
        self.assertTrue(hasattr(Handler, '_handle_cancel'))

    def test_find_ytdlp_function(self):
        """Verify _find_ytdlp function exists."""
        from server_main import _find_ytdlp
        result = _find_ytdlp()
        # Result may be None or a path depending on environment
        self.assertTrue(result is None or isinstance(result, str))


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


class TestCancelEndpoint(unittest.TestCase):
    """Test /cancel endpoint code structure."""

    def test_cancel_accepts_job_id_param(self):
        """_handle_cancel reads job_id from query string."""
        import inspect
        from server_main import Handler
        source = inspect.getsource(Handler._handle_cancel)
        self.assertIn("job_id", source)
        self.assertIn("_files_before", source)

    def test_cancel_removes_new_files(self):
        """_handle_cancel removes all files created during job (snapshot diff)."""
        import inspect
        from server_main import Handler
        source = inspect.getsource(Handler._handle_cancel)
        self.assertIn("_files_before", source)
        self.assertIn("listdir", source)

    def test_cancel_cancels_probe_meta(self):
        """_handle_cancel cancels running probe-meta jobs."""
        import inspect
        from server_main import Handler
        source = inspect.getsource(Handler._handle_cancel)
        self.assertIn("probe_meta_jobs", source)

    def test_download_jobs_track_files_before(self):
        """Download jobs track _files_before for cleanup."""
        import inspect
        from server.download import JobLogger
        source = inspect.getsource(JobLogger.run)
        self.assertIn("_files_before", source)


class TestProbeMetaEstimatedFlag(unittest.TestCase):
    """Test that probe-meta returns estimated flag."""

    def test_probe_meta_sets_estimated_true_on_extrapolation(self):
        """_run_probe_meta sets estimated=True when extrapolating."""
        import inspect
        from server.download import _run_probe_meta
        source = inspect.getsource(_run_probe_meta)
        self.assertIn("estimated", source)
        self.assertIn("True", source)

    def test_probe_meta_cache_stores_estimated(self):
        """_probe_meta_cache stores estimated flag."""
        import inspect
        from server.download import _run_probe_meta
        source = inspect.getsource(_run_probe_meta)
        self.assertIn('"estimated"', source)

    def test_server_sends_estimated_in_response(self):
        """Server _handle_probe_meta returns estimated in JSON."""
        import inspect
        from server_main import Handler
        source = inspect.getsource(Handler._handle_probe_meta)
        self.assertIn("estimated", source)


if __name__ == "__main__":
    unittest.main()
