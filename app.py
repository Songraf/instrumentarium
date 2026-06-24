#!/usr/bin/env python3
"""
Instrumentarium — Desktop App Launcher (Installer Stub)

This .exe is a lightweight launcher that:
1. On first run: extracts all files from the PyInstaller bundle to a permanent
   system folder (%APPDATA%/Instrumentarium on Windows, ~/Library/Application
   Support/Instrumentarium on macOS, ~/.instrumentarium on Linux).
2. On subsequent runs: launches the installed copy directly.
3. On update (new .exe downloaded): re-extracts missing/changed files.

The .exe itself can be placed anywhere (Desktop, USB drive) — it always
launches from the installed location.
"""

import os, sys, shutil, logging, subprocess

# ── Determine paths ──────────────────────────────────────────────
# When running from PyInstaller one-file: _MEIPASS = temp extraction dir
# When running installed: _MEIPASS does not exist

_BUNDLE_DIR = sys._MEIPASS if hasattr(sys, "_MEIPASS") else None
_EXE_PATH = os.path.abspath(sys.executable)

def _get_install_dir():
    """Return the permanent installation directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~")
    return os.path.join(base, ".instrumentarium")

_INSTALL_DIR = _get_install_dir()
_INSTALLED_EXE = os.path.join(_INSTALL_DIR, "Instrumentarium.exe" if sys.platform == "win32" else "Instrumentarium")
_INSTALLED_SERVER = os.path.join(_INSTALL_DIR, "server.py")
_INSTALLED_HTML = os.path.join(_INSTALL_DIR, "download.html")
_SETUP_MARKER = os.path.join(_INSTALL_DIR, ".setup_done")

# ── Logging (to install dir) ─────────────────────────────────────
# ── Check if running from installed location ─────────────────────
def _is_running_from_install_dir():
    """Check if this script is running from the installed directory."""
    if _BUNDLE_DIR:
        return False  # Running from PyInstaller temp dir
    # Check both the script path and the executable path
    _script_dir = os.path.normcase(os.path.dirname(os.path.abspath(sys.argv[0])))
    _exe_dir = os.path.normcase(os.path.dirname(_EXE_PATH))
    _install = os.path.normcase(_INSTALL_DIR)
    return _script_dir == _install or _exe_dir == _install

# ── Check if installation is complete ────────────────────────────
def _is_installed():
    """Check if all required files exist in the install dir."""
    required = [_INSTALLED_SERVER, _INSTALLED_HTML]
    return all(os.path.isfile(f) for f in required)

# ── Extract files from bundle to install dir ─────────────────────
def _extract_bundle():
    """Extract required files from the PyInstaller bundle to install dir."""
    log.info("Extracting bundle to: %s", _INSTALL_DIR)
    os.makedirs(_INSTALL_DIR, exist_ok=True)

    if not _BUNDLE_DIR or not os.path.isdir(_BUNDLE_DIR):
        log.error("Cannot extract: _MEIPASS not available (%s)", _BUNDLE_DIR)
        return False

    # Only copy the files we need, not the entire bundle (which includes
    # all PyInstaller deps like CEF, .so files, etc.)
    required_files = [
        'app.py', 'server.py', 'download.html',
        'assets/icon.png', 'assets/icon.svg', 'assets/icon.ico',
        'assets/Info.plist', 'assets/entitlements.plist',
    ]

    copied = 0
    for item in required_files:
        src = os.path.join(_BUNDLE_DIR, item)
        dst = os.path.join(_INSTALL_DIR, item)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            log.debug("Copied: %s", item)
        else:
            log.debug("Skipped (not in bundle): %s", item)

    log.info("Extracted %d files to %s", copied, _INSTALL_DIR)
    return copied > 0

# ── Update installed files (on new .exe download) ────────────────
def _update_if_needed():
    """Update installed files if the bundle has newer versions."""
    if not _BUNDLE_DIR:
        return False

    required_files = [
        'app.py', 'server.py', 'download.html',
        'assets/icon.png', 'assets/icon.svg', 'assets/icon.ico',
        'assets/Info.plist', 'assets/entitlements.plist',
    ]

    updated = False
    for item in required_files:
        src = os.path.join(_BUNDLE_DIR, item)
        dst = os.path.join(_INSTALL_DIR, item)

        if os.path.isfile(src):
            if not os.path.isfile(dst) or os.path.getsize(src) != os.path.getsize(dst):
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    updated = True
                    log.info("Updated: %s", item)
                except Exception as e:
                    log.warning("Failed to update %s: %s", item, e)

    return updated

# ── Main launcher logic ──────────────────────────────────────────
def main():
    # Setup logging now that _INSTALL_DIR is determined
    global log
    os.makedirs(_INSTALL_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(_INSTALL_DIR, "instrumentarium.log"), encoding="utf-8"),
        ],
    )
    log = logging.getLogger("instrumentarium.launcher")

    log.info("=== Instrumentarium Launcher ===")
    log.info("EXE: %s", _EXE_PATH)
    log.info("BUNDLE: %s", _BUNDLE_DIR)
    log.info("INSTALL_DIR: %s", _INSTALL_DIR)
    log.info("Running from install dir: %s", _is_running_from_install_dir())

    # Case 1: Running from installed location → just exec into the real app
    if _is_running_from_install_dir():
        log.info("Already running from install dir, launching server...")
        _launch_installed()
        return

    # Case 2: Running from bundle (first run or update)
    if _is_running_from_install_dir() is False and _BUNDLE_DIR:
        if _is_installed():
            log.info("Installation found, updating files...")
            _update_if_needed()
        else:
            log.info("First run — extracting bundle...")
            if not _extract_bundle():
                log.error("Failed to extract bundle!")
                sys.exit(1)

        # Launch installed copy
        log.info("Launching installed copy from: %s", _INSTALL_DIR)
        _launch_installed()
        return

    # Case 3: No bundle, not recognized as installed (dev mode or edge case)
    # Treat as installed if we're in the install dir
    if _is_running_from_install_dir():
        log.info("Detected install dir via sys.argv, launching server...")
        _launch_installed()
        return

    # Fallback: dev mode from source
    log.info("Development mode — running from source")
    _launch_from_bundle()


def _launch_installed():
    """Launch the app from the installed directory."""
    os.chdir(_INSTALL_DIR)
    # Import server module from installed location
    sys.path.insert(0, _INSTALL_DIR)
    _run_app()


def _launch_from_bundle():
    """Launch the app from the current bundle/temp directory."""
    if _BUNDLE_DIR:
        os.chdir(_BUNDLE_DIR)
        sys.path.insert(0, _BUNDLE_DIR)
    else:
        os.chdir(os.path.dirname(_EXE_PATH))
        sys.path.insert(0, os.path.dirname(_EXE_PATH))
    _run_app()


def _run_app():
    """Run the actual application (server + pywebview)."""
    # ── Detach from console on Windows ──────────────────────────
    if sys.platform == "win32" and not sys.stdout:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")

    import threading, time, signal

    _BASE_DIR = os.getcwd()

    # Single instance lock
    _LOCK_PATH = os.path.join(_BASE_DIR, ".instrumentarium.lock")
    _lock_fd = None

    def _acquire_lock():
        global _lock_fd
        try:
            if sys.platform == "win32":
                import msvcrt
                _lock_fd = open(_LOCK_PATH, "w")
                msvcrt.locking(_lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                _lock_fd = open(_LOCK_PATH, "w")
                fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (ImportError, OSError, IOError):
            return False

    if not _acquire_lock():
        log.info("Another instance is already running — exiting")
        sys.exit(0)

    def _cleanup_lock():
        global _lock_fd
        if _lock_fd:
            try:
                _lock_fd.close()
            except Exception:
                pass
        try:
            os.remove(_LOCK_PATH)
        except Exception:
            pass

    def _start_server_in_thread() -> None:

        log.info("=== Server thread starting ===")
        try:
            import server_main as srv
            srv.SCRIPT_DIR = _BASE_DIR
            srv._BASE_DIR = _BASE_DIR
            srv.SETUP_MARKER = os.path.join(_BASE_DIR, ".setup_done")
            srv._LOCK_PATH = os.path.join(_BASE_DIR, ".instrumentarium.lock")
            _bin_dir = os.path.join(_BASE_DIR, ".bin")
            srv.YT_DLP_DIR = _bin_dir
            srv.YT_DLP = os.path.join(_bin_dir, "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")
            srv._BIN_CANDIDATES = [_bin_dir]
            srv.state.output_base = srv.OUTPUT_BASE
            srv.state.bin_candidates = srv._BIN_CANDIDATES

            marker_exists = os.path.exists(srv.SETUP_MARKER)
            if marker_exists:
                srv.state.setup_phase = "silent_check"
                t = threading.Thread(target=srv._ensure_deps_wrapper, daemon=True)
                t.start()
            else:
                t = threading.Thread(target=srv._run_setup_wrapper, daemon=True)
                t.start()

            srv.srv = srv.http.server.HTTPServer(("0.0.0.0", srv.PORT), srv.Handler)
            srv.srv.allow_reuse_address = True
            srv.srv.timeout = 0.5
            srv.srv.serve_forever()
        except Exception as e:
            log.error("Server thread error: %s", e, exc_info=True)

    server_thread = threading.Thread(target=_start_server_in_thread, daemon=True)
    server_thread.start()

    # Wait for server
    import socket
    for _ in range(50):
        try:
            sock = socket.create_connection(("127.0.0.1", 18765), timeout=0.1)
            sock.close()

            break
        except (ConnectionRefusedError, OSError) as e:

            time.sleep(0.1)

    # ── Open window ─────────────────────────────────────────────

    log.info("Opening window...")

    def _do_cleanup():
        try:
            import server_main as srv
            if hasattr(srv, 'srv') and srv.srv:
                srv.srv.shutdown()
        except Exception:
            pass
        try:
            import server_main as srv
            srv.state.kill_active_proc()
        except Exception:
            pass
        _cleanup_lock()

    try:
        import webview

        # Build URL to local server
        _html_url = "http://127.0.0.1:18765"
        log.info("Opening window with URL: %s", _html_url)

        window = webview.create_window(
            "Instrumentarium",
            url=_html_url,
            width=620,
            height=720,
            resizable=False,
        )

        if window:
            def _on_closing():
                # Hide window immediately so user sees instant response
                try:
                    window.hide()
                except Exception:
                    pass
                # Cleanup in background thread
                t = threading.Thread(target=_do_cleanup, daemon=True)
                t.start()
                # Exit after brief delay to let cleanup start
                os._exit(0)

            window.events.closing += _on_closing
            window.events.closed += lambda: os._exit(0)

        def _sigterm_handler(signum, frame):
            _do_cleanup()
            os._exit(0)
        signal.signal(signal.SIGTERM, _sigterm_handler)

        _renderers = ["cefpython3", "edgechromium"] if sys.platform == "win32" else []
        _started = False
        for _gui in _renderers:
            try:
                webview.start(gui=_gui)
                _started = True
                break
            except Exception:
                pass

        if not _started:
            webview.start()

    except Exception as e:
        log.error("Could not open native window: %s", e, exc_info=True)
        import webbrowser, threading as _t
        webbrowser.open("http://127.0.0.1:18765")
        try:
            _t.Event().wait()
        except KeyboardInterrupt:
            pass

    _cleanup_lock()


if __name__ == "__main__":
    main()
