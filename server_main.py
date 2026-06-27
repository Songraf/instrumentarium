#!/usr/bin/env python3
"""
Instrumentarium — Desktop App Launcher + Server (v0.1.0).
"""

import http.server
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlparse, parse_qs

from server import (
    state,
    detect_platform,
    human_size,
    parse_speed,
    popen_no_console as _popen,
    find_ffmpeg as _find_ffmpeg,
    has_ffmpeg as _has_ffmpeg,
    map_ytdlp_error as _map_ytdlp_error,
    find_system_python,
    check_ytdlp,
    get_python_install_url,
    install_ytdlp,
    install_python,
    install_ffmpeg,
    write_marker as _write_marker,
    clear_marker as _clear_marker,
    ensure_deps as _ensure_deps,
    run_setup as _run_setup,
    download_jobs,
    probe_meta_jobs,
    _run_probe_meta,
    JobLogger,
    cleanup_old_jobs,
    mark_job_completed,
)

# ── Find yt-dlp (module-level helper) ──────────────────────────────────

def _find_ytdlp():
    """Find yt-dlp binary. Returns path or None."""
    for d in _BIN_CANDIDATES:
        candidate = os.path.join(d, "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")

# ── Logging ───────────────────────────────────────────────────────────

log = logging.getLogger("instrumentarium.server")


def _ensure_log_handler():
    """Make sure the server logger has at least one handler."""
    if not log.handlers and not logging.getLogger().handlers:
        if hasattr(sys, "_MEIPASS"):
            _base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            _base = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(_base, exist_ok=True)
        _log_path = os.path.join(_base, "instrumentarium.log")
        open(_log_path, "a").close()
        _fh = logging.FileHandler(_log_path, encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(_fh)
        log.info("=== Server started ===")


_ensure_log_handler()


def _safe_print(*args, **kwargs):
    """Print safely — skip when stdout is None (PyInstaller console=False)."""
    if sys.stdout:
        try:
            print(*args, **kwargs)
        except Exception:
            pass


# ── Config ────────────────────────────────────────────────────────────

PORT = 18765

# ── System data directory (persistent across restarts) ─────────────────
# Windows: %APPDATA%\.instrumentarium  (C:\Users\<user>\AppData\Roaming\.instrumentarium)
# Linux:   ~/.instrumentarium
# macOS:   ~/Library/Application Support/.instrumentarium
def _get_system_data_dir():
    """Return platform-specific persistent data directory."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        path = os.path.join(base, ".instrumentarium")
    elif system == "Darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", ".instrumentarium")
    else:
        path = os.path.join(os.path.expanduser("~"), ".instrumentarium")
    os.makedirs(path, exist_ok=True)
    return path

DATA_DIR = _get_system_data_dir()

# ── Working directory ─────────────────────────────────────────────────
if not globals().get("_BASE_DIR"):
    if hasattr(sys, "_MEIPASS"):
        _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    else:
        _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(_BASE_DIR, exist_ok=True)

SETUP_MARKER = os.path.join(_BASE_DIR, ".setup_done")
LOCK_PATH = os.path.join(_BASE_DIR, ".instrumentarium.lock")
COOKIES_FILE = os.path.join(DATA_DIR, "cookies.txt")

if hasattr(sys, "_MEIPASS"):
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    SCRIPT_DIR = _EXE_DIR
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# Use system Downloads folder as output directory
if platform.system() == "Windows":
    DOWNLOADS_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "Downloads")
elif platform.system() == "Darwin":
    DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
else:
    DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
OUTPUT_BASE = DOWNLOADS_DIR

# yt-dlp binary locations
if hasattr(sys, "_MEIPASS"):
    _BIN_CANDIDATES = [
        os.path.join(_BASE_DIR, ".bin"),
        os.path.join(_EXE_DIR, ".bin"),
        os.path.join(sys._MEIPASS, ".bin"),
    ]
else:
    _BIN_CANDIDATES = [os.path.join(SCRIPT_DIR, ".bin")]

YT_DLP_DIR = _BIN_CANDIDATES[0]
YT_DLP = os.path.join(YT_DLP_DIR, "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp")

# Set state configuration
state.output_base = OUTPUT_BASE
state.bin_candidates = _BIN_CANDIDATES


# ── Rate limiter ───────────────────────────────────────────────────────

class _RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, max_tokens=5, refill_rate=1.0):
        self._max_tokens = max_tokens
        self._tokens = max_tokens
        self._refill_rate = refill_rate
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def acquire(self):
        """Try to acquire a token. Returns True if allowed, False if rate limited."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False


_probe_limiter = _RateLimiter(max_tokens=3, refill_rate=0.5)
_download_limiter = _RateLimiter(max_tokens=2, refill_rate=0.2)


# ── Serve UI ──────────────────────────────────────────────────────────

_HTML_CANDIDATES = []

if hasattr(sys, "_MEIPASS"):
    _HTML_CANDIDATES.append(os.path.join(sys._MEIPASS, "download.html"))

_HTML_CANDIDATES.append(os.path.join(SCRIPT_DIR, "download.html"))
# Also check parent directory (dev layout: download.html is next to server/)
parent_dir = os.path.dirname(SCRIPT_DIR)
_HTML_CANDIDATES.append(os.path.join(parent_dir, "download.html"))

if hasattr(sys, "_MEIPASS"):
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _HTML_CANDIDATES.append(os.path.join(_exe_dir, "download.html"))


def _serve_html_file(handler):
    """Serve the HTML UI — search multiple locations."""
    html = None

    for path in _HTML_CANDIDATES:
        try:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()

            log.info("Serving HTML from: %s", path)
            break
        except FileNotFoundError:

            continue
    if html is None:
        html = "<h1>UI file not found</h1><p>Tried: " + ", ".join(_HTML_CANDIDATES) + "</p>"
        log.error("download.html not found in any location: %s", _HTML_CANDIDATES)
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ── HTTP handler ──────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler with CORS support and rate limiting."""

    def _cors_headers(self):
        """Add CORS headers to response."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)

        if p.path in ("/", "/index.html"):
            _serve_html_file(self)
            return

        if p.path == "/open-folder":
            folder = os.path.abspath(OUTPUT_BASE)
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", folder], creationflags=0x08000000)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
            self._json({"ok": True})
            return

        if p.path == "/open-log":
            log_path = os.path.join(_BASE_DIR, "instrumentarium.log")
            if platform.system() == "Darwin":
                subprocess.Popen(["open", log_path])
            elif platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", log_path], creationflags=0x08000000)
            else:
                subprocess.Popen(["xdg-open", log_path])
            self._json({"ok": True})
            return

        if p.path == "/open-python-download":
            if platform.system() == "Darwin":
                subprocess.Popen(["open", "https://www.python.org/downloads/macos/"])
            elif platform.system() == "Windows":
                subprocess.Popen(["cmd", "/c", "start", "https://www.python.org/downloads/windows/"], creationflags=0x08000000)
            else:
                subprocess.Popen(["xdg-open", "https://www.python.org/downloads/"])
            self._json({"ok": True})
            return

        if p.path == "/status":
            if state.setup_phase == "idle" and os.path.exists(SETUP_MARKER):
                state.set_phase("silent_check")
                t = threading.Thread(target=_ensure_deps_wrapper, daemon=True)
                t.start()
            self._json(state.get_setup_response(os.path.exists(SETUP_MARKER)))
            return

        if p.path == "/log":
            qs = parse_qs(p.query)
            jid = qs.get("job", [""])[0]
            off = int(qs.get("offset", ["0"])[0])
            j = download_jobs.get(jid)
            if not j:
                self._json({"error": "Job not found", "status": "error"})
                return
            self._json({
                "lines": j["log"][off:],
                "status": j["status"],
                "cancelled": j.get("cancelled", False),
                "speed": j.get("speed"),
                "filesize": j.get("filesize"),
                "downloaded_bytes": j.get("downloaded_bytes"),
                "stall_warning": j.get("stall_warning"),
            })
            return

        if p.path == "/probe":
            try:
                self._handle_probe()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if p.path == "/probe-meta":
            self._handle_probe_meta()
            return

        if p.path == "/probe-meta-status":
            qs = parse_qs(p.query)
            jid = qs.get("id", [""])[0].strip()
            job = probe_meta_jobs.get(jid)
            if not job:
                self._json({"error": "Job not found"})
                return
            self._json(job)
            return

        if p.path == "/cookies":
            self._handle_cookies()
            return

        if p.path == "/cancel":
            self._handle_cancel()
            return

        self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/setup":
            if state.setup_phase == "done" and os.path.exists(SETUP_MARKER):
                self._json({"ok": True, "already_done": True})
                return
            if state.setup_phase in ("idle", "error", "done"):
                t = threading.Thread(target=_run_setup_wrapper, daemon=True)
                t.start()
            self._json({"ok": True})
            return

        if self.path == "/cookies":
            self._handle_cookies()
            return

        if self.path == "/download":
            self._handle_download()
            return

        if urlparse(self.path).path == "/cancel":
            self._handle_cancel()
            return

        if self.path == "/shutdown":
            log.info("/shutdown received")
            state.kill_active_proc()
            threading.Thread(target=lambda: (time.sleep(0.2), srv.shutdown()), daemon=True).start()
            self._json({"ok": True})
            return

        self.send_error(404)

    def log_message(self, format, *args):
        log.debug("HTTP %s %s", self.command, self.path)

    def _json(self, data):
        try:
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_probe(self):
        """Handle /probe endpoint with rate limiting."""
        if not _probe_limiter.acquire():
            self._json({"error": "Rate limit exceeded — please wait before probing again"})
            return

        qs = parse_qs(urlparse(self.path).query)
        url = qs.get("url", [None])[0]

        if not url:
            self._json({"error": "URL is required"})
            return
        url = url.strip()

        # Validate URL
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                self._json({"error": "Invalid URL"})
                return
        except Exception:
            self._json({"error": "Invalid URL"})
            return

        yt = _find_ytdlp()
        if not yt:
            self._json({"error": "yt-dlp not found"})
            return
        try:
            cmd = [yt, "--dump-single-json", "--no-download", "--no-playlist", "--no-check-certificates"]
            if state.cookies_path:
                cmd += ["--cookies", state.cookies_path]
            cmd.append(url)
            proc = _popen(cmd)
            try:
                stdout_data, _ = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                self._json({"error": "Probe timed out (30s)"})
                return
            if proc.returncode != 0:
                err_text = stdout_data[:500] if stdout_data else "(no output)"
                friendly = _map_ytdlp_error(err_text)
                self._json({"error": friendly, "details": err_text})
                return
            if not stdout_data or not stdout_data.strip():
                self._json({"error": "Empty response from yt-dlp"})
                return
            json_start = stdout_data.find('{')
            if json_start < 0:
                self._json({"error": "No JSON in yt-dlp output", "details": stdout_data[:300]})
                return
            data = json.loads(stdout_data[json_start:])
            title = data.get("title", "Unknown")
            duration = data.get("duration", 0)
            thumbnail = data.get("thumbnail", "")
            formats_raw = data.get("formats", [])
            formats = []
            audio_formats = []
            for f in formats_raw:
                width = f.get("width") or 0
                height = f.get("height") or 0
                ext = f.get("ext", "?")
                raw_filesize = f.get("filesize")
                approx_filesize = f.get("filesize_approx")
                filesize = raw_filesize or approx_filesize or 0
                is_approx = raw_filesize is None and approx_filesize is not None
                vcodec = f.get("vcodec") or "none"
                acodec = f.get("acodec") or "none"
                video_ext = f.get("video_ext") or "none"
                audio_ext = f.get("audio_ext") or "none"
                format_note = f.get("format_note", "")
                format_id = f.get("format_id", "")
                is_video = (vcodec != "none" and vcodec is not None) or (video_ext != "none" and video_ext is not None)
                if not is_video:
                    abr = f.get("abr") or f.get("tbr") or 0
                    raw_audio_fs = f.get("filesize")
                    approx_audio_fs = f.get("filesize_approx")
                    audio_filesize = raw_audio_fs or approx_audio_fs or 0
                    audio_is_approx = raw_audio_fs is None and approx_audio_fs is not None
                    if abr > 0 or audio_filesize > 0:
                        audio_formats.append({
                            "format_id": f.get("format_id", ""),
                            "ext": ext,
                            "abr": round(abr, 1) if abr else 0,
                            "filesize": audio_filesize,
                            "is_approx": audio_is_approx,
                            "acodec": acodec,
                        })
                    continue
                is_vertical = height > width if (height and width) else False
                if height and width:
                    eff_height = height if not is_vertical else width
                elif height:
                    eff_height = height
                elif width:
                    eff_height = width
                else:
                    eff_height = 0
                if format_note and "DASH" not in format_note.upper():
                    res_label = format_note
                elif eff_height > 0:
                    res_label = f"{eff_height}p"
                elif format_id and str(format_id) not in ("0", ""):
                    res_label = str(format_id).upper()
                else:
                    res_label = "Скачать видео"
                formats.append({
                    "format_id": f.get("format_id", ""),
                    "ext": ext,
                    "height": eff_height,
                    "display_label": res_label,
                    "filesize": filesize,
                    "is_approx": is_approx,
                    "vcodec": vcodec,
                    "acodec": acodec,
                })
            formats.sort(key=lambda x: (-x["height"], -x["filesize"]))

            def nearest_std(h):
                buckets = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]
                return min(buckets, key=lambda b: abs(b - h))

            seen_res = set()
            unique_formats = []
            for f in formats:
                bucket = nearest_std(f["height"])
                if bucket not in seen_res:
                    seen_res.add(bucket)
                    unique_formats.append(f)

            seen_abr = set()
            unique_audio = []
            audio_formats.sort(key=lambda x: (-x["abr"], -x["filesize"]))
            for af in audio_formats:
                if af["abr"] <= 0:
                    continue
                abr_key = round(af["abr"] / 16) * 16
                if abr_key not in seen_abr:
                    seen_abr.add(abr_key)
                    unique_audio.append(af)
            unique_audio = unique_audio[:3]

            self._json({
                "title": title,
                "duration": duration,
                "thumbnail": thumbnail,
                "formats": unique_formats,
                "audio_formats": unique_audio,
            })
        except Exception as e:
            log.error("/probe: exception: %s", e, exc_info=True)
            self._json({"error": str(e)})

    def _handle_probe_meta(self):
        """Handle /probe-meta endpoint (async, returns job_id)."""
        from server.download import _probe_meta_cache, _cache_key
        qs = parse_qs(urlparse(self.path).query)
        url = qs.get("url", [""])[0].strip()
        format_id = qs.get("format_id", [""])[0].strip()
        duration = qs.get("duration", ["0"])[0].strip()
        if not url:
            self._json({"error": "URL is required"})
            return
        # Check cache first — return immediately if already probed for this format
        cache_key = _cache_key(url, format_id)
        if cache_key in _probe_meta_cache:
            cached = _probe_meta_cache[cache_key]
            self._json({
                "filesize": cached["filesize"],
                "duration": cached.get("duration", 0),
                "job_id": "cached",
                "probe_duration": cached.get("duration", 0),
                "estimated": cached.get("estimated", False),
                "status": "done",
            })
            return
        yt = _find_ytdlp()
        if not yt:
            self._json({"error": "yt-dlp not found"})
            return
        jid = str(uuid.uuid4())[:8]
        probe_meta_jobs[jid] = {"status": "running", "filesize": None, "probe_duration": None}
        t = threading.Thread(
            target=_run_probe_meta,
            args=(jid, url, yt, format_id, int(float(duration)) if duration else 0),
            daemon=True
        )
        t.start()
        self._json({"job_id": jid})

    def _handle_cookies(self):
        """Handle /cookies endpoint with validation."""
        if self.command == "GET":
            # Return current cookies content so UI can display it
            if state.cookies_path and os.path.isfile(COOKIES_FILE):
                try:
                    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                        content = f.read()
                    self._json({"ok": True, "content": content, "path": COOKIES_FILE})
                except Exception:
                    self._json({"ok": True, "content": "", "path": None})
            else:
                self._json({"ok": True, "content": "", "path": None})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        path = body.get("path", "").strip()
        content = body.get("content", "").strip()

        # Clear cookies
        if not path and not content:
            state.cookies_path = None
            # Remove the cookies file from disk
            if os.path.isfile(COOKIES_FILE):
                try:
                    os.remove(COOKIES_FILE)
                except Exception:
                    pass
            self._json({"ok": True, "path": None})
            return

        # Content provided — validate and save
        if content:
            import base64
            try:
                try:
                    raw = base64.b64decode(content).decode("utf-8")
                except Exception:
                    raw = content
                # Basic validation: check for Netscape cookie format
                if raw and not any(line.startswith("#") or "\t" in line for line in raw.split("\n")[:5]):
                    self._json({"error": "Invalid cookies format — expected Netscape cookie file"})
                    return
                # Limit size to 1MB
                if len(raw) > 1_000_000:
                    self._json({"error": "Cookies file too large (max 1MB)"})
                    return
                with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                    f.write(raw)
                state.cookies_path = COOKIES_FILE
                self._json({"ok": True, "path": COOKIES_FILE})
            except Exception as e:
                self._json({"error": str(e)})
            return

        # Path provided — use file directly
        if path and os.path.isfile(path):
            state.cookies_path = path
            self._json({"ok": True, "path": path})
            return

        self._json({"error": "File not found: " + path})

    def _handle_download(self):
        """Handle /download endpoint with rate limiting."""
        if not _download_limiter.acquire():
            self._json({"error": "Rate limit exceeded — please wait before starting a new download"})
            return

        if state.setup_error:
            self._json({"error": "Setup failed: " + str(state.setup_error)})
            return
        if state.setup_phase not in ("done", "silent_check"):
            self._json({"error": "Setup not complete"})
            return
        if state.setup_phase == "silent_check":
            self._wait_deps()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        url = body.get("url", "").strip()
        dl_mode = body.get("mode", "video")
        format_id = body.get("format_id", "")
        req_acodec = body.get("acodec", "")
        if dl_mode == "audio" and format_id == "__best_audio__":
            format_id = ""
        if not url:
            self._json({"error": "URL is required"})
            return
        jid = str(uuid.uuid4())[:8]
        download_jobs[jid] = {"log": [], "status": "running", "_partial_filepath": None}
        yt = _find_ytdlp()
        if not yt:
            self._json({"error": "yt-dlp not found — run setup first"})
            return
        JobLogger(jid, url, dl_mode, yt, format_id, req_acodec).start()
        self._json({"job_id": jid, "platform": detect_platform(url)})

    def _handle_cancel(self):
        """Handle /cancel endpoint — kill active download and clean up partial files."""
        qs = parse_qs(urlparse(self.path).query)
        req_job_id = qs.get("job_id", [None])[0]
        killed = state.kill_active_proc()
        # Find the job to clean up
        job = None
        jid = None
        for j, jobj in download_jobs.items():
            if req_job_id is None or j == req_job_id:
                if jobj.get("status") == "running":
                    job = jobj
                    jid = j
                    break
        # Mark job as cancelled immediately (before cleanup) to prevent race with JobLogger
        if job:
            job["cancelled"] = True
            job["status"] = "error"
            job["log"].append("[cancelled] Download cancelled by user")
            # Remove only files created by this job (tracked via stdout parsing)
            created_files = job.get("_created_files", set())
            for fpath in created_files:
                if fpath and os.path.exists(fpath):
                    try:
                        if os.path.isfile(fpath) or os.path.islink(fpath):
                            os.remove(fpath)
                            log.info("/cancel: removed file %s", fpath)
                    except OSError as e:
                        log.warning("/cancel: failed to remove %s: %s", fpath, e)
        # Also cancel any active probe-meta jobs
        for pjid, pjobj in list(probe_meta_jobs.items()):
            if pjobj.get("status") == "running":
                pjobj["status"] = "error"
                pjobj["error"] = "cancelled"
        if killed or job:
            self._json({"ok": True, "message": "Download cancelled"})
        else:
            self._json({"ok": True, "message": "No active download to cancel"})

    def _wait_deps(self, timeout=30):
        """Wait for silent dep check to complete."""
        _deadline = time.time() + timeout
        while time.time() < _deadline:
            if state.setup_phase == "done":
                self._json({"ok": True, "deps_ready": True})
                return
            if state.setup_phase == "error":
                self._json({"error": "Setup failed: " + str(state.setup_error)})
                return
            time.sleep(0.5)
        self._json({"error": "Timeout waiting for dependencies"})


# ── Setup wrappers ────────────────────────────────────────────────────

def _ensure_deps_wrapper():
    """Wrapper that calls ensure_deps with the correct arguments."""
    _ensure_deps(_BASE_DIR, YT_DLP_DIR, YT_DLP, _BIN_CANDIDATES, OUTPUT_BASE)


def _run_setup_wrapper():
    """Wrapper that calls run_setup with the correct arguments."""
    _run_setup(_BASE_DIR, YT_DLP_DIR, YT_DLP, _BIN_CANDIDATES, OUTPUT_BASE, SETUP_MARKER)


# ── Job cleanup thread ────────────────────────────────────────────────

def _cleanup_worker():
    """Periodically clean up old completed jobs."""
    while True:
        time.sleep(300)  # every 5 minutes
        try:
            cleanup_old_jobs(max_age_seconds=3600)
        except Exception as e:
            log.error("Job cleanup error: %s", e)


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":

    auto_setup = "--auto-setup" in sys.argv or "--setup" in sys.argv

    if auto_setup:
        t = threading.Thread(target=_run_setup_wrapper, daemon=True)
        t.start()

    # Start cleanup thread
    cleanup_thread = threading.Thread(target=_cleanup_worker, daemon=True)
    cleanup_thread.start()

    # Restore cookies from disk if file exists
    if os.path.isfile(COOKIES_FILE):
        state.cookies_path = COOKIES_FILE
        log.info("Restored cookies from %s", COOKIES_FILE)

    _safe_print(f"🎬 Video Downloader Server")
    _safe_print(f"   URL: http://localhost:{PORT}")
    _safe_print(f"   Output: {OUTPUT_BASE}/")
    _safe_print()



    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _safe_print("\nDone.")
        srv.shutdown()
