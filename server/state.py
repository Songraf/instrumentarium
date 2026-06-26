#!/usr/bin/env python3
"""Instrumentarium — shared application state.

Replaces list-based mutable globals with a proper class container.
Thread-safe access to cookies path, active subprocess, and setup state.
"""

import threading


class AppState:
    """Thread-safe container for shared application state."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cookies_path = None
        self._active_proc = None
        self._setup_error = None

        # Setup wizard state
        self.setup_phase = "idle"
        self.setup_progress = 0
        self.setup_messages = []
        self.python_ok = False
        self.ytdlp_ok = False
        self.server_started = False

        # Runtime configuration (set by server.py or app.py)
        self.output_base: str | None = None
        self.bin_candidates: list | None = None

    # ── Cookies ─────────────────────────────────────────────────────

    @property
    def cookies_path(self):
        with self._lock:
            return self._cookies_path

    @cookies_path.setter
    def cookies_path(self, value):
        with self._lock:
            self._cookies_path = value

    # ── Active subprocess ───────────────────────────────────────────

    @property
    def active_proc(self):
        with self._lock:
            return self._active_proc

    @active_proc.setter
    def active_proc(self, value):
        with self._lock:
            self._active_proc = value

    def kill_active_proc(self):
        """Kill the currently running subprocess and its children. Returns True if a process was killed."""
        import os
        import signal
        import platform
        with self._lock:
            proc = self._active_proc
            if proc and proc.poll() is None:
                try:
                    if platform.system() == "Windows":
                        # On Windows, use taskkill /T to kill process tree
                        import subprocess
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            capture_output=True, timeout=5
                        )
                    else:
                        # Kill entire process group (catches ffmpeg, aria2c children)
                        pgid = os.getpgid(proc.pid)
                        os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, Exception):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                # Also kill by PID directly as fallback
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                self._active_proc = None
                return True
            self._active_proc = None
            return False

    # ── Setup error ─────────────────────────────────────────────────

    @property
    def setup_error(self):
        with self._lock:
            return self._setup_error

    @setup_error.setter
    def setup_error(self, value):
        with self._lock:
            self._setup_error = value

    def set_setup_error(self, value):
        """Set setup error (thread-safe, explicit method)."""
        self.setup_error = value  # delegates to property setter

    # ── Setup state helpers ─────────────────────────────────────────

    def reset_setup(self):
        """Reset setup state for a fresh run (thread-safe)."""
        with self._lock:
            self.setup_phase = "idle"
            self.setup_progress = 0
            self.setup_messages = []
            self.python_ok = False
            self.ytdlp_ok = False
            self.server_started = False
            self._setup_error = None

    def set_phase(self, phase):
        """Set setup phase (thread-safe)."""
        with self._lock:
            self.setup_phase = phase

    def set_progress(self, progress):
        """Set setup progress (thread-safe)."""
        with self._lock:
            self.setup_progress = progress

    def set_python_ok(self, value):
        with self._lock:
            self.python_ok = value

    def set_ytdlp_ok(self, value):
        with self._lock:
            self.ytdlp_ok = value

    def set_server_started(self, value):
        with self._lock:
            self.server_started = value

    def add_message(self, text, msg_type="info"):
        """Add a setup progress message (thread-safe)."""
        import time
        with self._lock:
            self.setup_messages.append({
                "text": text,
                "type": msg_type,
                "time": time.time(),
            })

    def get_setup_response(self, setup_done_marker_exists=False):
        """Build the JSON response for the /status endpoint (thread-safe)."""
        with self._lock:
            return {
                "phase": self.setup_phase,
                "progress": self.setup_progress,
                "messages": list(self.setup_messages),
                "python_ok": self.python_ok,
                "ytdlp_ok": self.ytdlp_ok,
                "server_started": self.server_started,
                "error": self._setup_error,
                "setup_done": setup_done_marker_exists,
            }


# ── Singleton ──────────────────────────────────────────────────────────

state = AppState()
