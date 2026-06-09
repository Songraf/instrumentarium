#!/usr/bin/env python3
"""
Instrumentarium — Setup Wizard + Server
Checks dependencies, installs yt-dlp, starts the server, opens browser.
"""

import http.server, json, logging, os, platform, shutil, ssl, subprocess, sys, threading, time, urllib.request, uuid, zipfile
from urllib.parse import urlparse, parse_qs

# ── Logging ──────────────────────────────────────────────────────────
log = logging.getLogger("instrumentarium.server")

def _wait_deps(handler, timeout=30):
    """Wait for silent dep check to complete, then respond."""
    import time as _t
    _deadline = _t.time() + timeout
    while _t.time() < _deadline:
        if setup_state["phase"] == "done":
            # Re-process the download request now that deps are ready
            handler._json({"ok": True, "deps_ready": True})
            return
        if setup_state["phase"] == "error":
            handler._json({"error": "Setup failed: " + str(setup_state.get("error", "unknown"))})
            return
        _t.sleep(0.5)
    handler._json({"error": "Timeout waiting for dependencies"})

def _rotate_log(log_path, keep_days=5):
    """Remove log lines older than keep_days. Each line starts with 'YYYY-MM-DD HH:MM:SS'."""
    if not os.path.isfile(log_path):
        return
    try:
        cutoff = time.time() - keep_days * 86400
        from datetime import datetime
        tmp_path = log_path + ".tmp"
        kept = 0
        total = 0
        with open(log_path, "r", encoding="utf-8") as fin, open(tmp_path, "w", encoding="utf-8") as fout:
            for line in fin:
                total += 1
                # Try to parse timestamp at start of line: "YYYY-MM-DD HH:MM:SS"
                ts_str = line[:19] if len(line) >= 19 else ""
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
                    if ts >= cutoff:
                        fout.write(line)
                        kept += 1
                    continue
                except (ValueError, OSError):
                    pass
                # No valid timestamp — keep the line (could be continuation/stacktrace)
                fout.write(line)
                kept += 1
        os.replace(tmp_path, log_path)
        if total - kept > 0:
            log.info("Log rotation: removed %d old lines, kept %d", total - kept, kept)
    except Exception as e:
        log.warning("Log rotation failed: %s", e)

def _ensure_log_handler():
    """Make sure the server logger has at least one handler."""
    if not log.handlers and not logging.getLogger().handlers:
        if hasattr(sys, "_MEIPASS"):
            _base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            _base = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(_base, exist_ok=True)
        _log_path = os.path.join(_base, "instrumentarium.log")
        # Rotate old log entries (>5 days) before starting fresh
        _rotate_log(_log_path, keep_days=5)
        # Create empty log file immediately so it exists for open-log
        open(_log_path, "a").close()
        _fh = logging.FileHandler(_log_path, encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(_fh)
        log.info("=== Server started ===")

_ensure_log_handler()

def _safe_print(*args, **kwargs):
    """Print safely — skip when stdout is None (PyInstaller console=False on Windows)."""
    if sys.stdout:
        try:
            print(*args, **kwargs)
        except Exception:
            pass



# ── Config ──────────────────────────────────────────────────────────
PORT = 18765

# ── Working directory ───────────────────────────────────────────────
# All working files (logs, .bin, downloads, .setup_done, lock) go in the
# installation directory. Can be overridden by the launcher (app.py).
if not globals().get("_BASE_DIR"):
    if hasattr(sys, "_MEIPASS"):
        _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    else:
        _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(_BASE_DIR, exist_ok=True)

SETUP_MARKER = os.path.join(_BASE_DIR, ".setup_done")
_LOCK_PATH = os.path.join(_BASE_DIR, ".instrumentarium.lock")

# When running from PyInstaller, __file__ points to temp _MEI dir.
# Use sys.executable location for runtime data (downloads, .bin).
if hasattr(sys, "_MEIPASS"):
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    SCRIPT_DIR = _EXE_DIR
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Use system Downloads folder as output directory
import platform
if platform.system() == "Windows":
    DOWNLOADS_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "Downloads")
elif platform.system() == "Darwin":
    DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
else:  # Linux
    DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
OUTPUT_BASE = DOWNLOADS_DIR

# yt-dlp binary locations — all beside the exe
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

# ── Cookies ──────────────────────────────────────────────────────────
# Path to a cookies.txt file (Netscape format) for sites that require
# authentication (e.g. LinkedIn). Set via /cookies endpoint at runtime.
_cookies_path = [None]  # list-based mutable global

# ── Subprocess helper — no console windows on Windows ───────────────
def _popen(cmd, **kwargs):
    """subprocess.Popen that never flashes a console window on Windows."""
    if platform.system() == "Windows":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)
    kwargs.setdefault("text", True)
    # Do NOT set bufsize=1 — line-buffered text mode can deadlock with
    # stderr redirected to stdout. Use default buffering and read all at once.
    return subprocess.Popen(cmd, **kwargs)

# ── Setup state (shared with HTTP handler) ──────────────────────────
setup_state = {
    "phase": "idle",        # idle | checking | silent_check | installing_python | installing_ytdlp | done | error
    "progress": 0,          # 0-100
    "messages": [],         # list of {text, type}
    "python_ok": False,
    "ytdlp_ok": False,
    "server_started": False,
    "error": None,
    "setup_done": False,    # True if .setup_done marker exists
}

def msg(text, type="info"):
    setup_state["messages"].append({"text": text, "type": type, "time": time.time()})

# ── Dependency checks ───────────────────────────────────────────────
def find_system_python():
    """Find any usable Python 3.7+ on the system."""
    global _MACOS_PATH_EXTENDED
    candidates = ["python3", "python", "py"]
    if platform.system() == "Windows":
        candidates = ["py", "python", "python3"]

    # On macOS, expand PATH to include Homebrew paths (bundle launches with limited PATH)
    if platform.system() == "Darwin" and not globals().get("_MACOS_PATH_EXTENDED"):
        _MACOS_PATH_EXTENDED = True
        _extra_paths = [
            "/opt/homebrew/bin", "/opt/homebrew/sbin",
            "/usr/local/bin", "/usr/local/sbin",
            "/usr/bin",
        ]
        _current = os.environ.get("PATH", "")
        _new_parts = [p for p in _extra_paths if p and p not in _current]
        if _new_parts:
            os.environ["PATH"] = os.pathsep.join(_new_parts) + os.pathsep + _current
            log.info("macOS PATH expanded: %s", os.environ["PATH"])

    for c in candidates:
        p = shutil.which(c)
        if p:
            try:
                out = subprocess.check_output([p, "--version"], stderr=subprocess.STDOUT, text=True,
                                             creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0).strip()
                parts = out.split()
                if len(parts) >= 2:
                    ver = parts[1].split(".")
                    major, minor = int(ver[0]), int(ver[1])
                    if major >= 3 and (major > 3 or minor >= 7):
                        return p, out
            except:
                continue

    # On macOS, also try running Homebrew python directly (shutil.which may miss it)
    if platform.system() == "Darwin":
        for direct_path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]:
            if os.path.isfile(direct_path):
                try:
                    out = subprocess.check_output([direct_path, "--version"], stderr=subprocess.STDOUT, text=True).strip()
                    parts = out.split()
                    if len(parts) >= 2:
                        ver = parts[1].split(".")
                        major, minor = int(ver[0]), int(ver[1])
                        if major >= 3 and (major > 3 or minor >= 7):
                            return direct_path, out
                except:
                    pass

    return None, None

def check_ytdlp():
    """Check if yt-dlp exists in any known location."""
    # Check all bin candidates (beside exe first, then bundle)
    for d in _BIN_CANDIDATES:
        candidate = os.path.join(d, "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp")
        log.info("check_ytdlp: trying %s (exists=%s)", candidate, os.path.isfile(candidate))
        if os.path.isfile(candidate):
            try:
                out = subprocess.check_output([candidate, "--version"], stderr=subprocess.STDOUT, text=True,
                                             creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0).strip()
                log.info("check_ytdlp: found %s version=%s", candidate, out)
                return True, out
            except Exception as e:
                log.info("check_ytdlp: %s exists but --version failed: %s", candidate, e)
    # Also check system PATH
    sys_yt = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if sys_yt:
        try:
            out = subprocess.check_output([sys_yt, "--version"], stderr=subprocess.STDOUT, text=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0).strip()
            return True, out
        except:
            pass
    log.info("check_ytdlp: not found in any location")
    return False, None

def install_ytdlp():
    """Download yt-dlp into .bin/."""
    os.makedirs(YT_DLP_DIR, exist_ok=True)
    is_win = platform.system() == "Windows"

    # Multiple URLs to try (GitHub + mirrors)
    if is_win:
        urls = [
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        ]
    else:
        urls = [
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
            "https://github.yongqinget.cn/https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
        ]

    setup_state["phase"] = "installing_ytdlp"
    msg(f"⬇️  Скачиваю yt-dlp…", "info")
    log.info("Downloading yt-dlp, trying %d URLs", len(urls))
    setup_state["progress"] = 50

    last_err = None
    for url in urls:
        log.info("Trying: %s", url)
        try:
            # Use curl for better proxy/redirect support
            if shutil.which("curl"):
                cmd = ["curl", "-s", "-L", "-f", "--connect-timeout", "15", "--max-time", "120",
                       "-o", YT_DLP, url]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=130,
                                       creationflags=subprocess.CREATE_NO_WINDOW if is_win else 0)
                if result.returncode != 0:
                    err_msg = result.stderr[:300] if result.stderr else f"exit code {result.returncode}"
                    log.warning("curl failed for %s: %s", url, err_msg)
                    last_err = err_msg
                    continue
            else:
                # Fallback to urllib
                urllib.request.urlretrieve(url, YT_DLP)

            if not is_win:
                os.chmod(YT_DLP, 0o755)

            ver = subprocess.check_output([YT_DLP, "--version"], stderr=subprocess.STDOUT, text=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW if is_win else 0).strip()
            msg(f"✅ yt-dlp {ver} установлен", "ok")
            log.info("yt-dlp %s installed at %s", ver, YT_DLP)
            setup_state["progress"] = 70
            return True
        except Exception as e:
            log.warning("Failed to download from %s: %s", url, e)
            last_err = str(e)
            continue

    msg(f"❌ Ошибка загрузки yt-dlp: {last_err or 'все источники недоступны'}", "err")
    log.error("yt-dlp installation failed from all URLs, last error: %s", last_err)
    setup_state["error"] = f"yt-dlp download failed: {last_err or 'all sources unavailable'}"
    setup_state["phase"] = "error"
    return False

def get_python_install_url():
    """Return the official Python download URL for current OS."""
    system = platform.system()
    arch = platform.machine().lower()
    if system == "Windows":
        if "64" in arch or arch == "amd64":
            return "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe"
        return "https://www.python.org/ftp/python/3.12.9/python-3.12.9.exe"
    elif system == "Darwin":
        return "https://www.python.org/ftp/python/3.12.9/python-3.12.9-macos11.pkg"
    else:
        return None  # Linux — use package manager

def install_python():
    """Download and install Python on Windows. On Linux/Mac, show instructions."""
    system = platform.system()
    setup_state["phase"] = "installing_python"

    if system == "Linux":
        msg("🐧 Установи Python через пакетный менеджер:", "info")
        msg("   Ubuntu/Debian: sudo apt install python3", "info")
        msg("   Fedora:        sudo dnf install python3", "info")
        msg("   Arch:          sudo pacman -S python", "info")
        msg("   Затем перезапусти start.sh", "info")
        setup_state["phase"] = "error"
        setup_state["error"] = "Python not installed"
        return False

    if system == "Darwin":
        # Check if Homebrew Python is available but maybe just in a non-standard location
        homebrew_python = None
        for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]:
            if os.path.isfile(p):
                try:
                    out = subprocess.check_output([p, "--version"], stderr=subprocess.STDOUT, text=True).strip()
                    if "Python 3" in out:
                        homebrew_python = p
                        break
                except:
                    pass

        if homebrew_python:
            msg(f"✅ Python найден: {homebrew_python}", "ok")
            setup_state["python_ok"] = True
            return True

        # No Python — auto-download and install python.org .pkg
        import subprocess as _sp, subprocess

        # Detect architecture for correct download URL
        import platform as _plat
        arch = _plat.machine()  # 'arm64' or 'x86_64'
        if arch == "arm64":
            pkg_url = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-macos11.pkg"
            pkg_name = "python-3.12.4-macos11.pkg"
        else:
            pkg_url = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-macosx10.9.pkg"
            pkg_name = "python-3.12.4-macosx10.9.pkg"
        msg(f"⬇️  Скачиваю Python 3.12 (~45 MB)…", "info")
        log.info("Downloading macOS Python from %s", pkg_url)
        setup_state["phase"] = "installing_python"
        setup_state["progress"] = 5

        pkg_path = os.path.join(SCRIPT_DIR, pkg_name)
        try:
            def _reporthook(block_num, block_size, total_size):
                if total_size > 0:
                    pct = min(int(block_num * block_size * 60 / total_size), 60)
                    setup_state["progress"] = 5 + pct

            urllib.request.urlretrieve(pkg_url, pkg_path, _reporthook)
            msg("✅ Python скачен. Запускаю установщик…", "ok")
            log.info("Python pkg downloaded: %s", pkg_path)
            setup_state["progress"] = 70

            # Install .pkg using macOS installer command
            _sp.check_call(["/usr/bin/sudo", "/usr/bin/installer", "-pkg", pkg_path, "-target", "/"])
            msg("✅ Python установлен!", "ok")
            log.info("Python pkg installed successfully")
            setup_state["progress"] = 85
            setup_state["python_ok"] = True
            setup_state["phase"] = "checking"
            _clear_marker()
            return True

        except Exception as e:
            msg(f"❌ Ошибка установки Python: {e}", "err")
            msg(f"   Скачай вручную: https://www.python.org/downloads/macos/", "info")
            log.error("Python installation failed: %s", e)
            setup_state["phase"] = "error"
            setup_state["error"] = str(e)
            return False

    # Windows — auto-install
    url = get_python_install_url()
    if not url:
        msg("❌ Не удалось определить ссылку для скачивания Python", "err")
        log.error("Could not determine Python download URL for %s", system)
        setup_state["phase"] = "error"
        return False

    installer_path = os.path.join(SCRIPT_DIR, "python_installer.exe")
    msg(f"⬇️  Скачиваю Python 3.12… (~25 MB)", "info")
    log.info("Downloading Python 3.12 from %s", url)
    setup_state["progress"] = 10

    try:
        def reporthook(block_num, block_size, total_size):
            if total_size > 0:
                pct = min(int(block_num * block_size * 40 / total_size), 40)
                setup_state["progress"] = 10 + pct

        urllib.request.urlretrieve(url, installer_path, reporthook)
        msg("✅ Python скачен. Запускаю установщик…", "ok")
        log.info("Python installer downloaded, running setup…")
        setup_state["progress"] = 55
        setup_state["phase"] = "installing_python"

        # Run installer silently with PATH addition
        subprocess.check_call([
            installer_path,
            "/quiet", "InstallAllUsers=0",
            "PrependPath=1",
            "Include_pip=1",
        ])
        msg("✅ Python установлен! Перезапусти приложение вручную.", "ok")
        log.info("Python installed successfully — user should relaunch")
        setup_state["progress"] = 100
        setup_state["phase"] = "done"
        setup_state["python_ok"] = True
        setup_state["server_started"] = True
        # Do NOT os.execv — let the user relaunch manually

    except Exception as e:
        msg(f"❌ Ошибка установки Python: {e}", "err")
        msg(f"   Скачай вручную: {url}", "info")
        log.error("Python installation failed: %s", e)
        setup_state["phase"] = "error"
        setup_state["error"] = str(e)
        return False

def _find_ffmpeg():
    """Look for ffmpeg binary near the exe or in PATH. Returns path or None."""
    if platform.system() == "Windows":
        names = ["ffmpeg.exe", "ffmpeg"]
    else:
        names = ["ffmpeg"]
    # Check all bin candidates
    for d in _BIN_CANDIDATES:
        for name in names:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate):
                return candidate
    # Check system PATH
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

def _has_ffmpeg():
    """Return True if ffmpeg is available."""
    return _find_ffmpeg() is not None

def _extract_from_zip(zip_path, dest_dir, names):
    """Extract specific files from a zip archive into dest_dir."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            basename = os.path.basename(member)
            if basename in names:
                target = os.path.join(dest_dir, basename)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                log.info("Extracted %s -> %s", basename, target)


def install_ffmpeg():
    """Download ffmpeg essentials into .bin/. Returns True on success."""
    system = platform.system()
    is_win = system == "Windows"

    os.makedirs(YT_DLP_DIR, exist_ok=True)

    if is_win:
        # BtbN FFmpeg Windows builds (essentials — smaller download)
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        zip_path = os.path.join(YT_DLP_DIR, "ffmpeg.zip")
        setup_state["phase"] = "installing_ffmpeg"
        msg("⬇️  Скачиваю ffmpeg (~80 MB)… Это нужно для полного качества видео.", "info")
        log.info("Downloading ffmpeg from %s", url)
        setup_state["progress"] = 35

        try:
            # Download zip
            if shutil.which("curl"):
                cmd = ["curl", "-L", "-f", "--connect-timeout", "30", "--max-time", "600",
                       "-o", zip_path, url]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=610,
                                        creationflags=subprocess.CREATE_NO_WINDOW if is_win else 0)
                if result.returncode != 0:
                    log.warning("curl failed (%d): %s", result.returncode, result.stderr[:200])
                    raise RuntimeError("curl download failed")
            else:
                urllib.request.urlretrieve(url, zip_path)

            setup_state["progress"] = 70
            msg("📦 Распаковываю ffmpeg…", "info")
            log.info("Extracting ffmpeg zip to %s", YT_DLP_DIR)

            # Extract only ffmpeg.exe and ffprobe.exe from the zip
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    basename = os.path.basename(member)
                    if basename in ("ffmpeg.exe", "ffprobe.exe"):
                        target = os.path.join(YT_DLP_DIR, basename)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        log.info("Extracted %s -> %s", basename, target)

            # Clean up zip
            os.remove(zip_path)

            ffmpeg_path = os.path.join(YT_DLP_DIR, "ffmpeg.exe")
            if os.path.isfile(ffmpeg_path):
                msg("✅ ffmpeg установлен — видео будет в полном качестве!", "ok")
                log.info("ffmpeg installed successfully at %s", ffmpeg_path)
                setup_state["progress"] = 80
                return True
            else:
                raise FileNotFoundError("ffmpeg.exe not found after extraction")

        except Exception as e:
            log.error("ffmpeg installation failed: %s", e, exc_info=True)
            msg("⚠️ Не удалось скачать ffmpeg. Качество видео может быть ограничено.", "info")
            # Clean up partial download
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
            return False

    elif system == "Darwin":
        # Try to find existing ffmpeg first
        ffmpeg_path = _find_ffmpeg()
        if ffmpeg_path:
            msg("✅ ffmpeg найден: " + ffmpeg_path, "ok")
            return True

        # Download static ffmpeg build for macOS
        msg("⬇️  Скачиваю ffmpeg…", "info")
        log.info("Downloading ffmpeg for macOS")

        # evermeet.cx static builds — universal (Intel + Apple Silicon)
        ffmpeg_url = "https://evermeet.cx/ffmpeg/getrelease/zip"
        ffprobe_url = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

        try:
            import ssl as _ssl
            _ctx = _ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = _ssl.CERT_NONE

            zip_path = os.path.join(YT_DLP_DIR, "ffmpeg-macos.zip")
            urllib.request.urlretrieve(ffmpeg_url, zip_path)
            _extract_from_zip(zip_path, YT_DLP_DIR, ["ffmpeg"])
            os.remove(zip_path)

            zip_path = os.path.join(YT_DLP_DIR, "ffprobe-macos.zip")
            urllib.request.urlretrieve(ffprobe_url, zip_path)
            _extract_from_zip(zip_path, YT_DLP_DIR, ["ffprobe"])
            os.remove(zip_path)

            # Make executable
            for name in ("ffmpeg", "ffprobe"):
                p = os.path.join(YT_DLP_DIR, name)
                if os.path.isfile(p):
                    os.chmod(p, 0o755)

            ffmpeg_path = _find_ffmpeg()
            if ffmpeg_path:
                msg("✅ ffmpeg установлен!", "ok")
                log.info("ffmpeg installed at %s", ffmpeg_path)
                return True
        except Exception as e:
            log.warning("ffmpeg auto-download failed: %s", e)
            msg("⚠️ Не удалось скачать ffmpeg автоматически.", "info")

        # Fallback: instruct user
        msg("🍎 Установи ffmpeg:", "info")
        msg("   brew install ffmpeg", "info")
        return False

    else:
        # Linux
        msg("🐧 Установи ffmpeg:", "info")
        msg("   Ubuntu/Debian: sudo apt install ffmpeg", "info")
        msg("   Fedora:        sudo dnf install ffmpeg", "info")
        msg("   Arch:          sudo pacman -S ffmpeg", "info")
        ffmpeg_path = _find_ffmpeg()
        if ffmpeg_path:
            msg("✅ ffmpeg найден: " + ffmpeg_path, "ok")
            return True
        return False
def _write_marker():
    """Write .setup_done marker file."""
    try:
        log.info("Writing setup marker to: %s", SETUP_MARKER)
        with open(SETUP_MARKER, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("Setup marker written successfully")
    except Exception as e:
        log.error("Could not write setup marker to %s: %s", SETUP_MARKER, e)

def _clear_marker():
    """Remove .setup_done marker file."""
    try:
        if os.path.exists(SETUP_MARKER):
            log.info("Removing setup marker: %s", SETUP_MARKER)
            os.remove(SETUP_MARKER)
    except Exception as e:
        log.error("Could not remove setup marker: %s", e)

def _ensure_deps():
    """Silent dependency check — no messages, no UI."""
    log.info("_ensure_deps: starting silent dep check")
    try:
        py_path, _ = find_system_python()
        if not py_path:
            log.warning("_ensure_deps: Python not found, setup needed")
            return False
        log.info("_ensure_deps: Python found: %s", py_path)

        ok, ver = check_ytdlp()
        if not ok:
            log.info("_ensure_deps: yt-dlp not found, downloading...")
            if not install_ytdlp():
                log.error("_ensure_deps: yt-dlp download failed")
                return False
        else:
            log.info("_ensure_deps: yt-dlp found: %s", ver)

        # ffmpeg is best-effort in silent mode — don't fail if download fails
        if not _has_ffmpeg():
            log.info("_ensure_deps: ffmpeg not found, attempting install...")
            install_ffmpeg()

        os.makedirs(OUTPUT_BASE, exist_ok=True)
        setup_state["python_ok"] = True
        setup_state["ytdlp_ok"] = True
        setup_state["phase"] = "done"
        setup_state["progress"] = 100
        setup_state["server_started"] = True
        _write_marker()
        log.info("_ensure_deps: all deps OK, phase=done")
        return True
    except Exception as e:
        log.error("_ensure_deps: exception: %s", e, exc_info=True)
        return False

def run_setup():
    """Full visible setup: check Python → check/install ytdlp → start server."""
    log.info("run_setup: starting full setup wizard")
    setup_state["progress"] = 0
    setup_state["messages"] = []
    setup_state["error"] = None
    _clear_marker()

    # Clear old log so user only sees current run
    log_path = os.path.join(_BASE_DIR, "instrumentarium.log")
    try:
        if os.path.isfile(log_path):
            os.remove(log_path)
    except Exception:
        pass
    # Re-create empty log
    log.info("=== Setup started (old log cleared) ===")
    msg("🔍 Проверяю зависимости…", "info")
    setup_state["progress"] = 5

    # ── Step 1: Python ───────────────────────────────────────────
    py_path, py_ver = find_system_python()
    if py_path:
        log.info("Python found: %s (%s)", py_path, py_ver)
        msg(f"✅ {py_ver} найден: {py_path}", "ok")
        setup_state["python_ok"] = True
        setup_state["progress"] = 30
    else:
        log.warning("Python 3.7+ not found")
        msg("❌ Python 3.7+ не найден", "err")
        setup_state["python_ok"] = False
        if not install_python():
            log.error("Python installation failed or not available")
            return

    # ── Step 2: yt-dlp ───────────────────────────────────────────
    ok, ver = check_ytdlp()
    if ok:
        log.info("yt-dlp found: %s", ver)
        msg(f"✅ yt-dlp {ver} найден", "ok")
        setup_state["ytdlp_ok"] = True
        setup_state["progress"] = 70
    else:
        log.info("yt-dlp not found, downloading...")
        msg("⚠️  yt-dlp не найден, скачиваю…", "info")
        if not install_ytdlp():
            log.error("yt-dlp installation failed")
            return

    # ── Step 3: ffmpeg ───────────────────────────────────────────
    if _has_ffmpeg():
        log.info("ffmpeg found: %s", _find_ffmpeg())
        msg(f"✅ ffmpeg найден", "ok")
        setup_state["progress"] = 85
    else:
        log.info("ffmpeg not found, installing...")
        install_ffmpeg()  # best-effort; don't fail setup if it errors
        setup_state["progress"] = 90

    # ── Step 4: Ready ────────────────────────────────────────────
    setup_state["progress"] = 95
    log.info("Downloads will be saved to: %s", OUTPUT_BASE)
    os.makedirs(OUTPUT_BASE, exist_ok=True)
    msg(f"✅ Готово! Загрузки сохраняются в папку «Загрузки»", "ok")
    setup_state["progress"] = 100
    setup_state["phase"] = "done"
    setup_state["server_started"] = True
    _write_marker()
    log.info("Setup complete — server ready on port %d", PORT)

def _find_ytdlp():
    """Find yt-dlp binary. Returns path or None."""
    for d in _BIN_CANDIDATES:
        candidate = os.path.join(d, "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")


def _map_ytdlp_error(err_text):
    """Map yt-dlp error output to a user-friendly Russian message."""
    err = (err_text or "").lower()
    if "unsupported url" in err:
        return "Неправильная ссылка или сайт не поддерживается"
    if "video unavailable" in err or "content is not available" in err:
        return "Видео недоступно или удалено"
    if "private video" in err or "private" in err:
        return "Видео приватное — нужны cookies для доступа"
    if "login" in err or "sign in" in err or "authentication" in err:
        return "Требуется вход в аккаунт — загрузите cookies"
    if "blocked" in err or "banned" in err or "403" in err:
        return "Доступ заблокирован — попробуйте позже или используйте cookies"
    if "404" in err or "not found" in err:
        return "Страница не найдена (404)"
    if "429" in err or "rate limit" in err or "too many requests" in err:
        return "Слишком много запросов — подождите и попробуйте снова"
    if "network" in err or "connection" in err or "timeout" in err:
        return "Ошибка сети — проверьте подключение к интернету"
    if "geo" in err or "region" in err or "country" in err:
        return "Видео недоступно в вашем регионе"
    if "removed" in err or "deleted" in err:
        return "Видео было удалено"
    if "copyright" in err or "dmca" in err:
        return "Видео заблокировано по запросу правообладателя"
    if "age" in err or "restricted" in err:
        return "Видео с возрастным ограничением — нужны cookies"
    # Fallback: return a generic friendly message
    return "Не удалось обработать ссылку — проверьте правильность или используйте cookies"

# ── HTTP handler ────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path)

        if p.path in ("/", "/index.html"):
            _serve_html_file(self)
            return

        if p.path == "/open-folder":
            folder = os.path.abspath(OUTPUT_BASE)
            log.info("/open-folder: %s", folder)
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", folder], creationflags=subprocess.CREATE_NO_WINDOW)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
            self._json({"ok": True})
            return

        if p.path == "/open-log":
            log_path = os.path.join(_BASE_DIR, "instrumentarium.log")
            log.info("/open-log: base_dir=%s log_path=%s exists=%s", _BASE_DIR, log_path, os.path.isfile(log_path))
            if platform.system() == "Darwin":
                subprocess.Popen(["open", log_path])
            elif platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", log_path], creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(["xdg-open", log_path])
            self._json({"ok": True})
            return

        if p.path == "/open-python-download":
            log.info("/open-python-download")
            if platform.system() == "Darwin":
                subprocess.Popen(["open", "https://www.python.org/downloads/macos/"])
            elif platform.system() == "Windows":
                subprocess.Popen(["cmd", "/c", "start", "https://www.python.org/downloads/windows/"], creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(["xdg-open", "https://www.python.org/downloads/"])
            self._json({"ok": True})
            return

        if p.path == "/status":
            # On first contact, check if setup was already done
            if setup_state["phase"] == "idle" and os.path.exists(SETUP_MARKER):
                # Run dep check in background thread to avoid blocking HTTP response
                setup_state["phase"] = "silent_check"
                t = threading.Thread(target=_ensure_deps, daemon=True)
                t.start()
                setup_state["setup_done"] = True
            elif setup_state["phase"] == "idle":
                setup_state["setup_done"] = False
            resp = {
                "phase": setup_state["phase"],
                "progress": setup_state["progress"],
                "messages": setup_state["messages"],
                "python_ok": setup_state["python_ok"],
                "ytdlp_ok": setup_state["ytdlp_ok"],
                "server_started": setup_state["server_started"],
                "error": setup_state["error"],
                "setup_done": os.path.exists(SETUP_MARKER),
            }
            log.info("/status: phase=%s setup_done=%s marker_exists=%s", setup_state["phase"], resp["setup_done"], os.path.exists(SETUP_MARKER))
            self._json(resp)
            return

        if p.path == "/log":
            qs = parse_qs(p.query)
            jid = qs.get("job", [""])[0]
            off = int(qs.get("offset", ["0"])[0])
            j = download_jobs.get(jid)
            if not j:
                self._json({"error": "Job not found", "status": "error"})
                return
            self._json({"lines": j["log"][off:], "status": j["status"]})
            return

        if p.path == "/probe":
            qs = parse_qs(p.query)
            url = qs.get("url", [""])[0].strip()
            if not url:
                self._json({"error": "URL is required"})
                return
            yt = _find_ytdlp()
            if not yt:
                self._json({"error": "yt-dlp not found"})
                return
            try:
                cmd = [yt, "--dump-single-json", "--no-download", "--no-playlist", "--no-check-certificates"]
                if _cookies_path[0]:
                    cmd += ["--cookies", _cookies_path[0]]
                cmd.append(url)
                log.info("/probe: cmd=%s", " ".join(cmd))
                proc = _popen(cmd)
                try:
                    stdout_data, _ = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self._json({"error": "Probe timed out (30s)"})
                    return
                if proc.returncode != 0:
                    err_text = stdout_data[:500] if stdout_data else "(no output)"
                    log.warning("/probe: yt-dlp exit=%d output=%s", proc.returncode, err_text)
                    # Map yt-dlp errors to user-friendly messages
                    friendly = _map_ytdlp_error(err_text)
                    self._json({"error": friendly, "details": err_text})
                    return
                if not stdout_data or not stdout_data.strip():
                    self._json({"error": "Empty response from yt-dlp"})
                    return
                # yt-dlp may output warnings to stderr (which we merge into stdout).
                # Find the actual JSON object starting with '{'.
                json_start = stdout_data.find('{')
                if json_start < 0:
                    self._json({"error": "No JSON in yt-dlp output", "details": stdout_data[:300]})
                    return
                data = json.loads(stdout_data[json_start:])
                # Extract relevant format info
                title = data.get("title", "Unknown")
                duration = data.get("duration", 0)
                thumbnail = data.get("thumbnail", "")
                formats_raw = data.get("formats", [])
                # Build simplified format list — only video formats with resolution
                formats = []
                audio_formats = []
                for f in formats_raw:
                    width = f.get("width") or 0
                    height = f.get("height") or 0
                    ext = f.get("ext", "?")
                    filesize = f.get("filesize") or f.get("filesize_approx") or 0
                    vcodec = f.get("vcodec") or "none"
                    acodec = f.get("acodec") or "none"
                    video_ext = f.get("video_ext") or "none"
                    audio_ext = f.get("audio_ext") or "none"
                    format_note = f.get("format_note", "")
                    format_id = f.get("format_id", "")
                    # Skip audio-only formats. Detect video by vcodec or video_ext.
                    # LinkedIn returns vcodec=None but video_ext=mp4 for video formats.
                    is_video = (vcodec != "none" and vcodec is not None) or (video_ext != "none" and video_ext is not None)
                    if not is_video:
                        # Collect audio-only formats
                        abr = f.get("abr") or f.get("tbr") or 0
                        audio_filesize = f.get("filesize") or f.get("filesize_approx") or 0
                        if abr > 0 or audio_filesize > 0:
                            audio_formats.append({
                                "format_id": f.get("format_id", ""),
                                "ext": ext,
                                "abr": round(abr, 1) if abr else 0,
                                "filesize": audio_filesize,
                                "acodec": acodec,
                            })
                        continue
                    # For vertical videos (height > width), yt-dlp reports height as the
                    # long edge (e.g. 1920 for a 1080x1920 video). Use format_note for
                    # the human-readable label, and use width as the sort key since that's
                    # the actual "resolution class" for vertical content.
                    is_vertical = height > width if (height and width) else False
                    # Effective resolution for sorting/display:
                    # - horizontal video: use height (standard 1080p, 720p etc)
                    # - vertical video: use width (that's the real resolution class)
                    # - unknown (LinkedIn etc): use 0, will be shown as "SD"
                    if height and width:
                        eff_height = height if not is_vertical else width
                    elif height:
                        eff_height = height
                    elif width:
                        eff_height = width
                    else:
                        eff_height = 0
                    # Build display label
                    if format_note and "DASH" not in format_note.upper():
                        res_label = format_note
                    elif eff_height > 0:
                        res_label = f"{eff_height}p"
                    elif format_id and str(format_id) not in ("0", ""):
                        res_label = str(format_id).upper()
                    else:
                        res_label = "Скачать видео"
                    # Skip formats with completely unknown resolution (no height, no width, no note)
                    if eff_height == 0 and not format_note:
                        # Keep it only if it's the only format (LinkedIn-style single format)
                        # but give it a display label
                        pass
                    formats.append({
                        "format_id": f.get("format_id", ""),
                        "ext": ext,
                        "height": eff_height,
                        "display_label": res_label,
                        "filesize": filesize,
                        "vcodec": vcodec,
                        "acodec": acodec,
                    })
                # Sort by effective resolution desc
                formats.sort(key=lambda x: (-x["height"], -x["filesize"]))
                # Deduplicate by resolution — group into standard buckets
                # Round to nearest standard: 144, 240, 360, 480, 720, 1080, 1440, 2160
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
                # Deduplicate audio formats by bitrate (keep highest quality per bitrate)
                # Skip formats with unknown bitrate (e.g. HLS segments with abr=None)
                seen_abr = set()
                unique_audio = []
                audio_formats.sort(key=lambda x: (-x["abr"], -x["filesize"]))
                for af in audio_formats:
                    if af["abr"] <= 0:
                        continue  # skip unknown bitrate
                    # Round abr to nearest 16kbps for dedup
                    abr_key = round(af["abr"] / 16) * 16
                    if abr_key not in seen_abr:
                        seen_abr.add(abr_key)
                        unique_audio.append(af)
                # Limit to 3 best audio formats
                unique_audio = unique_audio[:3]
                self._json({
                    "title": title,
                    "duration": duration,
                    "thumbnail": thumbnail,
                    "formats": unique_formats,
                    "audio_formats": unique_audio,
                })
                log.info("/probe: found %d video, %d unique audio for '%s'", len(unique_formats), len(unique_audio), title)
            except Exception as e:
                log.error("/probe: exception: %s", e, exc_info=True)
                self._json({"error": str(e)})
            return

        # ── /probe-meta: start async file-size probe, return job_id ──
        if p.path == "/probe-meta":
            qs = parse_qs(p.query)
            url = qs.get("url", [""])[0].strip()
            format_id = qs.get("format_id", [""])[0].strip()
            duration = qs.get("duration", ["0"])[0].strip()
            if not url:
                self._json({"error": "URL is required"})
                return
            yt = _find_ytdlp()
            if not yt:
                self._json({"error": "yt-dlp not found"})
                return
            jid = str(uuid.uuid4())[:8]
            probe_meta_jobs[jid] = {"status": "running", "filesize": None, "probe_duration": None}
            t = threading.Thread(target=_run_probe_meta, args=(jid, url, yt, format_id, int(duration) if duration else 0), daemon=True)
            t.start()
            log.info("/probe-meta: started job %s for format %s", jid, format_id)
            self._json({"job_id": jid})
            return

        # ── /probe-meta-status: poll async probe result ────────────
        if p.path == "/probe-meta-status":
            qs = parse_qs(p.query)
            jid = qs.get("id", [""])[0].strip()
            job = probe_meta_jobs.get(jid)
            if not job:
                self._json({"error": "Job not found"})
                return
            self._json(job)
            # Clean up completed jobs to prevent memory leaks
            if job["status"] in ("done", "error"):
                # Keep briefly so client can read, but allow GC
                pass
            return

        self.send_error(404)

    def do_POST(self):
        if self.path == "/setup":
            # If already done, just confirm
            if setup_state["phase"] == "done" and os.path.exists(SETUP_MARKER):
                self._json({"ok": True, "already_done": True})
                return
            # Start fresh setup in background thread
            if setup_state["phase"] in ("idle", "error", "done"):
                t = threading.Thread(target=run_setup, daemon=True)
                t.start()
            self._json({"ok": True})
            return

        if self.path == "/cookies":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            path = body.get("path", "").strip()
            content = body.get("content", "").strip()
            # Clear cookies
            if not path and not content:
                _cookies_path[0] = None
                log.info("/cookies: cleared")
                self._json({"ok": True, "path": None})
                return
            # Content provided (base64 or raw text) — save to temp file
            if content:
                import base64, tempfile
                try:
                    # Try base64 decode first, fallback to raw text
                    try:
                        raw = base64.b64decode(content).decode("utf-8")
                    except Exception:
                        raw = content
                    tmp = os.path.join(_BASE_DIR, ".cookies.txt")
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(raw)
                    _cookies_path[0] = tmp  # type: ignore
                    log.info("/cookies: saved %d chars to %s", len(raw), tmp)
                    self._json({"ok": True, "path": tmp})
                except Exception as e:
                    log.error("/cookies: save failed: %s", e)
                    self._json({"error": str(e)})
                return
            # Path provided — use file directly
            if path and os.path.isfile(path):
                _cookies_path[0] = path
                log.info("/cookies: set to %s", path)
                self._json({"ok": True, "path": path})
                return
            self._json({"error": "File not found: " + path})

        if self.path == "/download":
            log.info("/download: phase=%s, url_body_pending", setup_state["phase"])
            if setup_state["phase"] == "error":
                err = setup_state.get("error", "unknown")
                log.warning("/download: setup in error state: %s", err)
                self._json({"error": "Setup failed: " + str(err)})
                return
            if setup_state["phase"] not in ("done", "silent_check"):
                log.warning("/download: setup not complete, phase=%s", setup_state["phase"])
                self._json({"error": "Setup not complete"})
                return
            # Wait for silent dep check to finish if it's still running
            if setup_state["phase"] == "silent_check":
                log.info("/download: waiting for silent dep check (timeout=30s)...")
                _wait_deps(self)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            url = body.get("url", "").strip()
            dl_mode = body.get("mode", "video")
            format_id = body.get("format_id", "")
            req_acodec = body.get("acodec", "")
            # Fallback: if no separate audio streams, use bestaudio/best
            if dl_mode == "audio" and format_id == "__best_audio__":
                format_id = ""  # signal to use bestaudio/best fallback
            log.info("/download: url=%s mode=%s format_id=%s acodec=%s", url, dl_mode, format_id, req_acodec)
            if not url:
                self._json({"error": "URL is required"})
                return
            jid = str(uuid.uuid4())[:8]
            download_jobs[jid] = {"log": [], "status": "running"}
            # Find yt-dlp
            yt = _find_ytdlp()
            if not yt:
                self._json({"error": "yt-dlp not found — run setup first"})
                return
            log.info("/download: yt-dlp resolved to: %s", yt)
            JobLogger(jid, url, dl_mode, yt, format_id, req_acodec).start()
            log.info("/download: job started, job_id=%s", jid)
            self._json({"job_id": jid, "platform": detect_platform(url)})
            return

        if self.path == "/shutdown":
            log.info("/shutdown received — killing active subprocesses and stopping server")
            proc = _active_proc[0]
            if proc and proc.poll() is None:
                log.info("/shutdown: killing active yt-dlp pid=%d", proc.pid)
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            _active_proc[0] = None
            # Schedule server shutdown in a background thread (can't block this one)
            import threading as _th
            _th.Thread(target=lambda: (time.sleep(0.2), srv.shutdown()), daemon=True).start()
            self._json({"ok": True})
            return

        self.send_error(404)

    def log_message(self, format, *args):
        """Override default stderr logging — write to our log file instead."""
        log.debug("HTTP %s %s", self.command, self.path)

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

# ── Serve UI ────────────────────────────────────────────────────────
# Strategy: in PyInstaller one-file mode, datas files are extracted to
# _MEIPASS temp folder. But CI may not include them. So we try:
#   1. Explicit path beside the .exe (_EXE_DIR/download.html)
#   2. PyInstaller _MEIPA SS temp folder
#   3. Same dir as __file__ (dev mode)

_HTML_CANDIDATES = []

if hasattr(sys, "_MEIPASS"):
    _HTML_CANDIDATES.append(os.path.join(sys._MEIPASS, "download.html"))

# Always check beside the exe/app bundle (covers CI case)
_HTML_CANDIDATES.append(os.path.join(SCRIPT_DIR, "download.html"))

# Also check exe dir explicitly
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

# ── Download job logger ─────────────────────────────────────────────
download_jobs = {}
_active_proc = [None]  # Currently running yt-dlp subprocess (list for mutability without global)

def detect_platform(url):
    u = url.lower()
    for name, domains in [
        ("youtube", ["youtube.com", "youtu.be"]),
        ("twitter", ["twitter.com", "x.com"]),
        ("tiktok", ["tiktok.com"]),
        ("instagram", ["instagram.com"]),
        ("facebook", ["facebook.com", "fb.com", "fb.watch"]),
        ("linkedin", ["linkedin.com"]),
    ]:
        for d in domains:
            if d in u:
                return name
    return "other"

class JobLogger(threading.Thread):
    def __init__(self, job_id, url, mode, yt_dlp_path, format_id="", acodec=""):
        super().__init__(daemon=True)
        self.job_id = job_id
        self.url = url
        self.mode = mode
        self.yt = yt_dlp_path
        self.format_id = format_id
        self.acodec = acodec

    def run(self):
        j = download_jobs[self.job_id]
        j["platform"] = detect_platform(self.url)
        out_dir = OUTPUT_BASE  # flat: all downloads go directly to system Downloads folder
        os.makedirs(out_dir, exist_ok=True)

        log.info("JobLogger[%s]: starting download url=%s mode=%s", self.job_id, self.url, self.mode)
        log.info("JobLogger[%s]: out_dir=%s", self.job_id, out_dir)
        log.info("JobLogger[%s]: yt-dlp path=%s exists=%s", self.job_id, self.yt, os.path.isfile(self.yt))

        ffmpeg = _find_ffmpeg()
        ffmpeg_ok = ffmpeg is not None
        log.info("JobLogger[%s]: ffmpeg=%s ffmpeg_ok=%s", self.job_id, ffmpeg, ffmpeg_ok)

        if self.mode == "audio":
            # For platforms that provide separate audio streams (YouTube, etc.),
            # prefer m4a container. Fall back to bestaudio/best for platforms
            # like TikTok that only have video+audio muxed — ffmpeg will extract.
            if ffmpeg_ok:
                fmt = "bestaudio[ext=m4a]/bestaudio/best"
            else:
                fmt = "bestaudio[ext=m4a]/bestaudio"
            post = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            # If a specific format_id was requested (from resolution button),
            # use it. Skip +bestaudio if:
            #   - format_id already contains "+" (merged format like "137+140")
            #   - acodec != "none" (format already includes audio, e.g. YouTube 360p/720p progressive)
            if self.format_id:
                has_audio = ("+" in self.format_id) or (self.acodec and self.acodec not in ("none", ""))
                if has_audio:
                    fmt = self.format_id
                else:
                    fmt = f"{self.format_id}+bestaudio/best"
                post = ["--merge-output-format", "mp4",
                        "--postprocessor-args", "ffmpeg:-c:a aac -b:a 128k"]
                if ffmpeg_ok:
                    post += ["--audio-format", "aac"]
            elif ffmpeg_ok:
                # With ffmpeg: best video + best audio, merge + recode to mp4.
                # No container restriction on bestvideo — Shorts may have
                # better streams in webm/VP9/AV1; ffmpeg handles recoding.
                fmt = "bestvideo+bestaudio/best"
                post = ["--merge-output-format", "mp4",
                        "--postprocessor-args", "ffmpeg:-c:a aac -b:a 128k"]
            else:
                # Without ffmpeg: we cannot merge separate video+audio DASH streams.
                # YouTube Shorts / regular videos will get combined (progressive)
                # mp4 files that are typically capped at ~360p and may have no audio.
                fmt = "best[ext=mp4]/best"
                post = []
                j["log"].append(
                    "[warn] ffmpeg not found — video may have no sound. "
                    "Install ffmpeg and restart for full quality." )

        # Limit filename length to avoid OS errors on long titles (LinkedIn etc)
        # Windows MAX_PATH is 260, leave room for extension and folder path
        out_tmpl = os.path.join(out_dir, "%(title).120s [%(id)s].%(ext)s")
        cmd = [self.yt, "-f", fmt, *post, "-o", out_tmpl,
               "--no-playlist", "--retries", "3",
               "--newline", "--progress", "--force-overwrites"]
        if _cookies_path[0]:
            cmd += ["--cookies", _cookies_path[0]]
        cmd.append(self.url)
        # Embedding metadata/thumbnails requires ffmpeg
        if ffmpeg_ok:
            cmd += ["--embed-metadata", "--embed-thumbnail"]

        if ffmpeg_ok:
            cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]

        j["log"].append(f"[yt-dlp] {self.yt}")
        j["log"].append(f"[cmd] {' '.join(cmd)}")
        log.info("JobLogger[%s]: cmd=%s", self.job_id, " ".join(cmd))

        try:
            log.info("JobLogger[%s]: calling _popen...", self.job_id)
            proc = _popen(cmd)
            _active_proc[0] = proc
            log.info("JobLogger[%s]: popen returned, pid=%s", self.job_id, proc.pid)

            # Stream output line by line — no hard timeout.
            # Let yt-dlp run as long as needed (slow connections, long streams).
            # Parse speed and detect stalled downloads.
            stdout_data = []
            filepath = None
            last_progress_time = time.time()
            last_speed = None
            STALL_INTERVAL = 15  # seconds without progress before warning

            while True:
                line = proc.stdout.readline() if proc.stdout else None
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                    # Check for stalled download (no progress output)
                    now = time.time()
                    if now - last_progress_time > STALL_INTERVAL:
                        log.warning("JobLogger[%s]: no progress output for %ds", self.job_id, STALL_INTERVAL)
                        last_progress_time = now  # reset to avoid spam
                        j["stall_warning"] = "Скорость загрузки 0 B/s — проверьте подключение к интернету"
                    continue

                line = line.rstrip('\n')
                stdout_data.append(line)
                j["log"].append(line)
                last_progress_time = time.time()
                j.pop("stall_warning", None)  # clear stall warning on new output

                if "[download] Destination:" in line:
                    filepath = line.split("Destination:", 1)[1].strip()
                elif line.startswith("[Merger]") and "into" in line:
                    idx = line.rfind('"')
                    idx2 = line.rfind('"', 0, idx)
                    if idx > idx2:
                        filepath = line[idx2+1:idx]
                    j["log"].append("[info] Audio+Video merge ✓")

                # Parse speed and filesize from yt-dlp progress lines
                # Example: "[download]   5.3% of 250.00MiB at   1.23MiB/s ETA 03:25"
                if "[download]" in line and " at " in line and "%" in line and " of " in line:
                    try:
                        # Parse filesize: "of 250.00MiB"
                        size_str = line.split(" of ")[1].split(" at ")[0].strip()
                        j["filesize"] = _parse_speed(size_str)  # same unit parsing logic
                    except (IndexError, ValueError):
                        pass
                    try:
                        # Parse speed: "at   1.23MiB/s"
                        speed_str = line.split(" at ")[1].split(" ")[0]
                        j["speed"] = _parse_speed(speed_str)
                    except (IndexError, ValueError):
                        pass
                elif "[download] Downloading" in line:
                    pass  # initial line, no speed/size yet
                elif line.startswith("[download]") and "%" in line:
                    # Fallback speed parsing for different yt-dlp formats
                    for part in line.split():
                        if part.endswith("/s") and len(part) > 3:
                            try:
                                j["speed"] = _parse_speed(part)
                                last_speed = j["speed"]
                            except (IndexError, ValueError):
                                pass
                            break

            proc.wait()
            stdout_data_str = "\n".join(stdout_data)
            log.info("JobLogger[%s]: process exited, returncode=%d, output_chars=%d", self.job_id, proc.returncode, len(stdout_data_str))
            if proc.returncode != 0 or "[Merger]" not in stdout_data_str:
                log.info("JobLogger[%s]: full output:\n%s", self.job_id, stdout_data_str[:3000])

            if proc.returncode == 0:
                j["status"] = "done"
                j["speed"] = None
                if filepath and os.path.exists(filepath):
                    j["filepath"] = filepath
                    j["filename"] = os.path.basename(filepath)
                else:
                    files = sorted(
                        [f for f in os.listdir(out_dir) if not f.startswith(".")],
                        key=lambda f: os.path.getmtime(os.path.join(out_dir, f)),
                        reverse=True)
                    if files:
                        j["filepath"] = os.path.join(out_dir, files[0])
                        j["filename"] = files[0]
                size = os.path.getsize(j["filepath"]) if j.get("filepath") and os.path.exists(j.get("filepath", "")) else 0
                j["log"].append(f"[done] {j.get('filename','?')} ({_human(size)})")
                log.info("JobLogger[%s]: download complete: %s (%s)", self.job_id, j.get("filename", "?"), _human(size))
            else:
                j["status"] = "error"
                j["speed"] = None
                j["log"].append(f"[error] exit code {proc.returncode}")
                log.error("JobLogger[%s]: download failed (exit %d), output:\n%s", self.job_id, proc.returncode, stdout_data[-500:] if stdout_data else "(empty)")
        except FileNotFoundError as e:
            j["status"] = "error"
            j["log"].append(f"[error] yt-dlp not found: {self.yt}")
            j["log"].append(f"[error] {e}")
            log.error("JobLogger[%s]: yt-dlp not found: %s", self.job_id, self.yt)
        except Exception as e:
            j["status"] = "error"
            j["log"].append(f"[error] {e}")
            log.error("JobLogger[%s]: exception: %s", self.job_id, e, exc_info=True)
        finally:
            if _active_proc[0] is proc:
                _active_proc[0] = None

def _parse_speed(s):
    """Parse speed string like '1.23MiB/s' or '500.00KiB/s' into bytes/sec."""
    s = s.strip()
    mult = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3}
    for suffix, m in mult.items():
        s_upper = s.upper()
        if s_upper.endswith(suffix + '/S') or s_upper.endswith(suffix + 'IB/S'):
            try:
                return float(s_upper.split(suffix.upper())[0].rstrip('I')) * m
            except ValueError:
                pass
    # fallback: try to extract leading number
    num = ''
    for ch in s:
        if ch.isdigit() or ch == '.':
            num += ch
        else:
            break
    return float(num) if num else 0

def _human(n):
    for u in ['B','KB','MB','GB']:
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

# ── Probe-meta runner (background thread) ─────────────────────────────
_PROBE_DURATION = 15  # seconds to download before killing

def _run_probe_meta(jid, url, yt_path, format_id, video_duration):
    """Run yt-dlp probe download in a background thread and store result."""
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="instr_probe_")
    try:
        tmpl = os.path.join(tmpdir, "probe.%(ext)s")
        fmt = format_id + "+bestaudio/best" if format_id and "+" not in format_id else (format_id if format_id else "best")
        cmd = [yt_path, "-f", fmt,
               "-o", tmpl, "--no-playlist", "--no-check-certificates",
               "--retries", "1", "--newline", "--no-progress"]
        if _cookies_path[0]:
            cmd += ["--cookies", _cookies_path[0]]
        cmd.append(url)
        log.info("/probe-meta thread %s: starting", jid)
        proc = _popen(cmd)
        try:
            proc.communicate(timeout=_PROBE_DURATION)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        probe_files = [f for f in os.listdir(tmpdir) if not f.startswith(".")]
        total_size = 0
        if probe_files:
            for f in probe_files:
                total_size += os.path.getsize(os.path.join(tmpdir, f))
        result = {"filesize": total_size if total_size > 0 else None,
                  "probe_duration": _PROBE_DURATION}
        if video_duration and video_duration > _PROBE_DURATION and total_size > 0:
            result["filesize"] = int(total_size * (video_duration / _PROBE_DURATION))
        probe_meta_jobs[jid] = {"status": "done", **result}
        log.info("/probe-meta thread %s: done size=%d", jid, total_size)
    except Exception as e:
        log.error("/probe-meta thread %s: error %s", jid, e)
        probe_meta_jobs[jid] = {"status": "error", "filesize": None, "error": str(e)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Probe-meta jobs (async, non-blocking) ─────────────────────────────
# Running probe-meta downloads are stored here keyed by job_id.
# Each entry: {"status": "running"|"done"|"error", "filesize": N, "probe_duration": N}
probe_meta_jobs = {}
if __name__ == "__main__":
    # If run directly (not via start.sh), do auto-setup
    auto_setup = "--auto-setup" in sys.argv or "--setup" in sys.argv

    if auto_setup:
        t = threading.Thread(target=run_setup, daemon=True)
        t.start()

    _safe_print(f"🎬 Video Downloader Server")
    _safe_print(f"   URL: http://localhost:{PORT}")
    _safe_print(f"   Output: {OUTPUT_BASE}/<platform>/")
    _safe_print()
    _safe_print()
    srv = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _safe_print("\nDone.")
        srv.shutdown()
