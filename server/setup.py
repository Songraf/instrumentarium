#!/usr/bin/env python3
"""Instrumentarium — setup wizard.

Checks and installs dependencies: Python, yt-dlp, ffmpeg.
"""

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

from .state import state
from .utils import human_size

log = logging.getLogger("instrumentarium.server.setup")


# ── Dependency checks ──────────────────────────────────────────────────

def find_system_python():
    """Find any usable Python 3.7+ on the system.

    Returns:
        Tuple of (path, version_string) or (None, None).
    """
    candidates = ["python3", "python", "py"]
    if platform.system() == "Windows":
        candidates = ["py", "python", "python3"]

    # On macOS, expand PATH to include Homebrew paths
    if platform.system() == "Darwin" and not globals().get("_MACOS_PATH_EXTENDED"):
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
                out = subprocess.check_output(
                    [p, "--version"], stderr=subprocess.STDOUT, text=True,
                    creationflags=0x08000000 if platform.system() == "Windows" else 0
                ).strip()
                parts = out.split()
                if len(parts) >= 2:
                    ver = parts[1].split(".")
                    major, minor = int(ver[0]), int(ver[1])
                    if major >= 3 and (major > 3 or minor >= 7):
                        return p, out
            except Exception:
                continue

    # On macOS, also try running Homebrew python directly
    if platform.system() == "Darwin":
        for direct_path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]:
            if os.path.isfile(direct_path):
                try:
                    out = subprocess.check_output(
                        [direct_path, "--version"], stderr=subprocess.STDOUT, text=True
                    ).strip()
                    parts = out.split()
                    if len(parts) >= 2:
                        ver = parts[1].split(".")
                        major, minor = int(ver[0]), int(ver[1])
                        if major >= 3 and (major > 3 or minor >= 7):
                            return direct_path, out
                except Exception:
                    pass

    return None, None


def check_ytdlp(bin_candidates):
    """Check if yt-dlp exists in any known location.

    Args:
        bin_candidates: List of directories to search.

    Returns:
        Tuple of (found, version_string).
    """
    for d in bin_candidates:
        candidate = os.path.join(d, "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp")
        if os.path.isfile(candidate):
            try:
                out = subprocess.check_output(
                    [candidate, "--version"], stderr=subprocess.STDOUT, text=True,
                    creationflags=0x08000000 if platform.system() == "Windows" else 0
                ).strip()
                return True, out
            except Exception as e:
                log.info("check_ytdlp: %s exists but --version failed: %s", candidate, e)

    sys_yt = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if sys_yt:
        try:
            out = subprocess.check_output(
                [sys_yt, "--version"], stderr=subprocess.STDOUT, text=True,
                creationflags=0x08000000 if platform.system() == "Windows" else 0
            ).strip()
            return True, out
        except Exception:
            pass

    return False, None


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
        return None


# ── Installation ───────────────────────────────────────────────────────

def install_ytdlp(yt_dlp_dir, yt_dlp_path):
    """Download yt-dlp into .bin/.

    Args:
        yt_dlp_dir: Directory to install yt-dlp into.
        yt_dlp_path: Full path for the yt-dlp binary.

    Returns:
        True on success.
    """
    os.makedirs(yt_dlp_dir, exist_ok=True)
    is_win = platform.system() == "Windows"

    if is_win:
        urls = [
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        ]
    else:
        urls = [
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
            "https://github.yongqinget.cn/https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
        ]

    state.set_phase("installing_ytdlp")
    state.add_message("⬇️  Скачиваю yt-dlp…", "info")
    state.set_progress(50)

    last_err = None
    for url in urls:
        try:
            if shutil.which("curl"):
                cmd = ["curl", "-s", "-L", "-f", "--connect-timeout", "15", "--max-time", "120",
                       "-o", yt_dlp_path, url]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=130,
                    creationflags=0x08000000 if is_win else 0
                )
                if result.returncode != 0:
                    last_err = result.stderr[:300] if result.stderr else f"exit code {result.returncode}"
                    continue
            else:
                urllib.request.urlretrieve(url, yt_dlp_path)

            if not is_win:
                os.chmod(yt_dlp_path, 0o755)

            ver = subprocess.check_output(
                [yt_dlp_path, "--version"], stderr=subprocess.STDOUT, text=True,
                creationflags=0x08000000 if is_win else 0
            ).strip()
            state.add_message(f"✅ yt-dlp {ver} установлен", "ok")
            state.set_progress(70)
            return True
        except Exception as e:
            last_err = str(e)
            continue

    state.add_message(f"❌ Ошибка загрузки yt-dlp: {last_err or 'все источники недоступны'}", "err")
    state.set_setup_error(f"yt-dlp download failed: {last_err or 'all sources unavailable'}")
    state.set_phase("error")
    return False


def install_python(base_dir, script_dir):
    """Download and install Python. On Linux/Mac, show instructions.

    Args:
        base_dir: Base installation directory.
        script_dir: Directory containing the script.

    Returns:
        True on success.
    """
    system = platform.system()
    state.set_phase("installing_python")

    if system == "Linux":
        state.add_message("🐧 Установи Python через пакетный менеджер:", "info")
        state.add_message("   Ubuntu/Debian: sudo apt install python3", "info")
        state.add_message("   Fedora:        sudo dnf install python3", "info")
        state.add_message("   Arch:          sudo pacman -S python", "info")
        state.add_message("   Затем перезапусти start.sh", "info")
        state.set_phase("error")
        state.set_setup_error("Python not installed")
        return False

    if system == "Darwin":
        # Check if Homebrew Python is available
        for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]:
            if os.path.isfile(p):
                try:
                    out = subprocess.check_output([p, "--version"], stderr=subprocess.STDOUT, text=True).strip()
                    if "Python 3" in out:
                        state.add_message(f"✅ Python найден: {p}", "ok")
                        state.set_python_ok(True)
                        return True
                except Exception:
                    pass

        # Auto-download and install python.org .pkg
        arch = platform.machine()
        if arch == "arm64":
            pkg_url = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-macos11.pkg"
            pkg_name = "python-3.12.4-macos11.pkg"
        else:
            pkg_url = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-macosx10.9.pkg"
            pkg_name = "python-3.12.4-macosx10.9.pkg"

        state.add_message("⬇️  Скачиваю Python 3.12 (~45 MB)…", "info")
        state.set_progress(5)

        pkg_path = os.path.join(script_dir, pkg_name)
        try:
            def _reporthook(block_num, block_size, total_size):
                if total_size > 0:
                    pct = min(int(block_num * block_size * 60 / total_size), 60)
                    state.set_progress(5) + pct

            urllib.request.urlretrieve(pkg_url, pkg_path, _reporthook)
            state.add_message("✅ Python скачен. Запускаю установщик…", "ok")
            state.set_progress(70)

            subprocess.check_call(["/usr/bin/sudo", "/usr/bin/installer", "-pkg", pkg_path, "-target", "/"])
            state.add_message("✅ Python установлен!", "ok")
            state.set_progress(85)
            state.set_python_ok(True)
            state.set_phase("checking")
            clear_marker(base_dir)
            return True
        except Exception as e:
            state.add_message(f"❌ Ошибка установки Python: {e}", "err")
            state.add_message("   Скачай вручную: https://www.python.org/downloads/macos/", "info")
            state.set_phase("error")
            state.set_setup_error(str(e))
            return False

    # Windows — auto-install
    url = get_python_install_url()
    if not url:
        state.add_message("❌ Не удалось определить ссылку для скачивания Python", "err")
        state.set_phase("error")
        return False

    installer_path = os.path.join(script_dir, "python_installer.exe")
    state.add_message("⬇️  Скачиваю Python 3.12… (~25 MB)", "info")
    state.set_progress(10)

    try:
        def reporthook(block_num, block_size, total_size):
            if total_size > 0:
                pct = min(int(block_num * block_size * 40 / total_size), 40)
                state.set_progress(10) + pct

        urllib.request.urlretrieve(url, installer_path, reporthook)
        state.add_message("✅ Python скачен. Запускаю установщик…", "ok")
        state.set_progress(55)
        state.set_phase("installing_python")

        subprocess.check_call([
            installer_path,
            "/quiet", "InstallAllUsers=0",
            "PrependPath=1", "Include_pip=1",
        ])
        state.add_message("✅ Python установлен! Перезапусти приложение вручную.", "ok")
        state.set_progress(100)
        state.set_phase("done")
        state.set_python_ok(True)
        state.set_server_started(True)
    except Exception as e:
        state.add_message(f"❌ Ошибка установки Python: {e}", "err")
        state.add_message(f"   Скачай вручную: {url}", "info")
        state.set_phase("error")
        state.set_setup_error(str(e))
        return False


def install_ffmpeg(yt_dlp_dir, bin_candidates):
    """Download ffmpeg essentials into .bin/. Returns True on success.

    Args:
        yt_dlp_dir: Directory to install ffmpeg into.
        bin_candidates: List of directories to search for existing ffmpeg.

    Returns:
        True on success.
    """
    system = platform.system()
    is_win = system == "Windows"
    os.makedirs(yt_dlp_dir, exist_ok=True)

    if is_win:
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        zip_path = os.path.join(yt_dlp_dir, "ffmpeg.zip")
        state.set_phase("installing_ffmpeg")
        state.add_message("⬇️  Скачиваю ffmpeg (~80 MB)… Это нужно для полного качества видео.", "info")
        state.set_progress(35)

        try:
            if shutil.which("curl"):
                cmd = ["curl", "-L", "-f", "--connect-timeout", "30", "--max-time", "600",
                       "-o", zip_path, url]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=610,
                    creationflags=0x08000000 if is_win else 0
                )
                if result.returncode != 0:
                    raise RuntimeError("curl download failed")
            else:
                urllib.request.urlretrieve(url, zip_path)

            state.set_progress(70)
            state.add_message("📦 Распаковываю ffmpeg…", "info")

            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    basename = os.path.basename(member)
                    if basename in ("ffmpeg.exe", "ffprobe.exe"):
                        target = os.path.join(yt_dlp_dir, basename)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            import shutil as _shutil
                            _shutil.copyfileobj(src, dst)

            os.remove(zip_path)
            ffmpeg_path = os.path.join(yt_dlp_dir, "ffmpeg.exe")
            if os.path.isfile(ffmpeg_path):
                state.add_message("✅ ffmpeg установлен — видео будет в полном качестве!", "ok")
                state.set_progress(80)
                return True
            else:
                raise FileNotFoundError("ffmpeg.exe not found after extraction")
        except Exception as e:
            log.error("ffmpeg installation failed: %s", e, exc_info=True)
            state.add_message("⚠️ Не удалось скачать ffmpeg. Качество видео может быть ограничено.", "info")
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
            return False

    elif system == "Darwin":
        from .utils import find_ffmpeg
        ffmpeg_path = find_ffmpeg(bin_candidates)
        if ffmpeg_path:
            state.add_message("✅ ffmpeg найден: " + ffmpeg_path, "ok")
            return True

        state.add_message("⬇️  Скачиваю ffmpeg…", "info")
        ffmpeg_url = "https://evermeet.cx/ffmpeg/getrelease/zip"
        ffprobe_url = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

        try:
            import ssl as _ssl
            _ctx = _ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = _ssl.CERT_NONE

            zip_path = os.path.join(yt_dlp_dir, "ffmpeg-macos.zip")
            urllib.request.urlretrieve(ffmpeg_url, zip_path)
            _extract_from_zip(zip_path, yt_dlp_dir, ["ffmpeg"])
            os.remove(zip_path)

            zip_path = os.path.join(yt_dlp_dir, "ffprobe-macos.zip")
            urllib.request.urlretrieve(ffprobe_url, zip_path)
            _extract_from_zip(zip_path, yt_dlp_dir, ["ffprobe"])
            os.remove(zip_path)

            for name in ("ffmpeg", "ffprobe"):
                p = os.path.join(yt_dlp_dir, name)
                if os.path.isfile(p):
                    os.chmod(p, 0o755)

            ffmpeg_path = find_ffmpeg(bin_candidates)
            if ffmpeg_path:
                state.add_message("✅ ffmpeg установлен!", "ok")
                return True
        except Exception as e:
            log.warning("ffmpeg auto-download failed: %s", e)
            state.add_message("⚠️ Не удалось скачать ffmpeg автоматически.", "info")

        state.add_message("🍎 Установи ffmpeg:", "info")
        state.add_message("   brew install ffmpeg", "info")
        return False

    else:
        # Linux
        from .utils import find_ffmpeg
        ffmpeg_path = find_ffmpeg(bin_candidates)
        if ffmpeg_path:
            state.add_message("✅ ffmpeg найден: " + ffmpeg_path, "ok")
            return True

        state.add_message("🐧 Установи ffmpeg:", "info")
        state.add_message("   Ubuntu/Debian: sudo apt install ffmpeg", "info")
        state.add_message("   Fedora:        sudo dnf install ffmpeg", "info")
        state.add_message("   Arch:          sudo pacman -S ffmpeg", "info")
        return False


def _extract_from_zip(zip_path, dest_dir, names):
    """Extract specific files from a zip archive into dest_dir."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            basename = os.path.basename(member)
            if basename in names:
                target = os.path.join(dest_dir, basename)
                with zf.open(member) as src, open(target, "wb") as dst:
                    import shutil as _shutil
                    _shutil.copyfileobj(src, dst)


# ── Setup marker ───────────────────────────────────────────────────────

def write_marker(setup_marker_path):
    """Write .setup_done marker file."""
    try:
        with open(setup_marker_path, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        log.error("Could not write setup marker: %s", e)


def clear_marker(setup_marker_path):
    """Remove .setup_done marker file."""
    try:
        if os.path.exists(setup_marker_path):
            os.remove(setup_marker_path)
    except Exception as e:
        log.error("Could not remove setup marker: %s", e)


# ── Setup orchestration ────────────────────────────────────────────────

def ensure_deps(base_dir, yt_dlp_dir, yt_dlp_path, bin_candidates, output_base):
    """Silent dependency check — no messages, no UI.

    Args:
        base_dir: Base installation directory.
        yt_dlp_dir: Directory for yt-dlp binary.
        yt_dlp_path: Full path to yt-dlp binary.
        bin_candidates: List of directories to search for binaries.
        output_base: Output directory for downloads.

    Returns:
        True on success.
    """
    try:
        py_path, _ = find_system_python()
        if not py_path:
            return False

        ok, ver = check_ytdlp(bin_candidates)
        if not ok:
            if not install_ytdlp(yt_dlp_dir, yt_dlp_path):
                return False

        from .utils import has_ffmpeg
        if not has_ffmpeg(bin_candidates):
            install_ffmpeg(yt_dlp_dir, bin_candidates)

        os.makedirs(output_base, exist_ok=True)
        state.set_python_ok(True)
        state.set_ytdlp_ok(True)
        state.set_progress(100)
        state.set_phase("done")
        state.set_server_started(True)
        write_marker(os.path.join(base_dir, ".setup_done"))
        return True
    except Exception as e:
        log.error("_ensure_deps: exception: %s", e, exc_info=True)
        return False


def run_setup(base_dir, yt_dlp_dir, yt_dlp_path, bin_candidates, output_base, setup_marker_path):
    """Full visible setup: check Python → check/install yt-dlp → start server.

    Args:
        base_dir: Base installation directory.
        yt_dlp_dir: Directory for yt-dlp binary.
        yt_dlp_path: Full path to yt-dlp binary.
        bin_candidates: List of directories to search for binaries.
        output_base: Output directory for downloads.
        setup_marker_path: Path to .setup_done marker file.
    """
    state.reset_setup()
    clear_marker(setup_marker_path)

    # Clear old log
    log_path = os.path.join(base_dir, "instrumentarium.log")
    try:
        if os.path.isfile(log_path):
            os.remove(log_path)
    except Exception:
        pass

    state.add_message("🔍 Проверяю зависимости…", "info")
    state.set_progress(5)

    # Step 1: Python
    py_path, py_ver = find_system_python()
    if py_path:
        state.add_message(f"✅ {py_ver} найден: {py_path}", "ok")
        state.set_python_ok(True)
        state.setup_progress = 30
    else:
        state.add_message("❌ Python 3.7+ не найден", "err")
        state.set_python_ok(False)
        if not install_python(base_dir, os.path.dirname(os.path.abspath(__file__))):
            return

    # Step 2: yt-dlp
    ok, ver = check_ytdlp(bin_candidates)
    if ok:
        state.add_message(f"✅ yt-dlp {ver} найден", "ok")
        state.set_ytdlp_ok(True)
        state.set_progress(70)
    else:
        state.add_message("⚠️  yt-dlp не найден, скачиваю…", "info")
        if not install_ytdlp(yt_dlp_dir, yt_dlp_path):
            return

    # Step 3: ffmpeg
    from .utils import has_ffmpeg, find_ffmpeg
    if has_ffmpeg(bin_candidates):
        state.add_message("✅ ffmpeg найден", "ok")
        state.set_progress(85)
    else:
        install_ffmpeg(yt_dlp_dir, bin_candidates)
        state.set_progress(90)

    # Step 4: Ready
    state.set_progress(95)
    os.makedirs(output_base, exist_ok=True)
    state.add_message("✅ Готово! Загрузки сохраняются в папку «Загрузки»", "ok")
    state.set_progress(100)
    state.set_phase("done")
    state.set_server_started(True)
    write_marker(setup_marker_path)
