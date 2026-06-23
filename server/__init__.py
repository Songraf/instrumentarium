#!/usr/bin/env python3
"""Instrumentarium — server package."""

# Re-import standard library modules so tests can patch them via @patch("server.xxx")
import os  # noqa: F401
import platform  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401

from .state import state
from .utils import (
    detect_platform,
    human_size,
    parse_speed,
    popen_no_console,
    find_ffmpeg,
    has_ffmpeg,
    PLATFORM_DOMAINS,
)
from .errors import map_ytdlp_error
from .setup import (
    find_system_python,
    check_ytdlp,
    get_python_install_url,
    install_ytdlp,
    install_python,
    install_ffmpeg,
    write_marker,
    clear_marker,
    ensure_deps,
    run_setup,
)
from .download import (
    download_jobs,
    probe_meta_jobs,
    _run_probe_meta,
    JobLogger,
    cleanup_old_jobs,
    mark_job_completed,
)

# ── Backward-compatible aliases ─────────────────────────────────────────
# Tests import these from server directly
_human = human_size
_map_ytdlp_error = map_ytdlp_error
setup_state = state
_popen = popen_no_console
_find_ffmpeg = find_ffmpeg
_has_ffmpeg = has_ffmpeg

__all__ = [
    "state",
    "setup_state",
    "detect_platform",
    "human_size",
    "_human",
    "parse_speed",
    "popen_no_console",
    "_popen",
    "find_ffmpeg",
    "_find_ffmpeg",
    "has_ffmpeg",
    "_has_ffmpeg",
    "PLATFORM_DOMAINS",
    "map_ytdlp_error",
    "_map_ytdlp_error",
    "find_system_python",
    "check_ytdlp",
    "get_python_install_url",
    "install_ytdlp",
    "install_python",
    "install_ffmpeg",
    "write_marker",
    "clear_marker",
    "ensure_deps",
    "run_setup",
    "download_jobs",
    "probe_meta_jobs",
    "_run_probe_meta",
    "JobLogger",
    "cleanup_old_jobs",
    "mark_job_completed",
]
