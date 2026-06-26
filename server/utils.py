#!/usr/bin/env python3
"""Instrumentarium — shared utilities."""

import logging
import platform
import shutil
import subprocess

log = logging.getLogger("instrumentarium.server")

# ── Platform detection ─────────────────────────────────────────────────

PLATFORM_DOMAINS = [
    ("youtube", ["youtube.com", "youtu.be"]),
    ("twitter", ["twitter.com", "x.com"]),
    ("tiktok", ["tiktok.com"]),
    ("instagram", ["instagram.com"]),
    ("facebook", ["facebook.com", "fb.com", "fb.watch"]),
    ("linkedin", ["linkedin.com"]),
    ("yandex", ["disk.yandex.ru", "yandex.ru"]),
]


def detect_platform(url):
    """Detect video platform from URL.

    Args:
        url: Video URL string.

    Returns:
        Platform name string (youtube, twitter, tiktok, etc., or domain name).
    """
    from urllib.parse import urlparse
    u = url.lower()
    for name, domains in PLATFORM_DOMAINS:
        for d in domains:
            if d in u:
                return name
    # Fallback: extract domain name as platform
    try:
        host = urlparse(url).netloc.lower()
        # Remove 'www.' prefix and return first part of domain
        if host.startswith("www."):
            host = host[4:]
        return host.split(".")[0] if host else "other"
    except Exception:
        return "other"


# ── File size formatting ───────────────────────────────────────────────

def human_size(n):
    """Format bytes as human-readable string.

    Args:
        n: Size in bytes.

    Returns:
        Formatted string like '15.0 MB'.
    """
    for u in ['B', 'KB', 'MB', 'GB']:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Speed parsing ──────────────────────────────────────────────────────

_SPEED_MULT = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3,
               'KB': 1024, 'MB': 1024**2, 'GB': 1024**3,
               'KIB': 1024, 'MIB': 1024**2, 'GIB': 1024**3}


def parse_size(s):
    """Parse size string like '~ 979.50MiB' or '979.50MiB' into bytes.

    Args:
        s: Size string from yt-dlp progress output (may include '~' prefix).

    Returns:
        Size in bytes (int), or 0 if parsing fails.
    """
    s = s.strip().lstrip('~').strip()
    s_upper = s.upper()
    # Sort suffixes by length descending so 'MIB' matches before 'MB' before 'B'
    for suffix in sorted(_SPEED_MULT.keys(), key=len, reverse=True):
        if s_upper.endswith(suffix):
            try:
                num_part = s_upper[:-len(suffix)]
                return int(float(num_part) * _SPEED_MULT[suffix])
            except ValueError:
                pass
    # Fallback: try to extract leading number
    num = ''
    for ch in s:
        if ch.isdigit() or ch == '.':
            num += ch
        else:
            break
    return int(float(num)) if num else 0


def parse_speed(s):
    """Parse speed string like '1.23MiB/s' into bytes/sec.

    Args:
        s: Speed string from yt-dlp progress output.

    Returns:
        Speed in bytes per second (float).
    """
    s = s.strip()
    s_upper = s.upper()
    # Sort suffixes by length descending so 'MIB/S' matches before 'MB/S' before 'B/S'
    for suffix in sorted(_SPEED_MULT.keys(), key=len, reverse=True):
        for speed_suf in [suffix + '/S', suffix + 'B/S']:
            if s_upper.endswith(speed_suf):
                try:
                    num_part = s_upper[:-len(speed_suf)]
                    return float(num_part) * _SPEED_MULT[suffix]
                except ValueError:
                    pass
    # Fallback: try to extract leading number
    num = ''
    for ch in s:
        if ch.isdigit() or ch == '.':
            num += ch
        else:
            break
    return float(num) if num else 0


# ── Subprocess helper ──────────────────────────────────────────────────

def popen_no_console(cmd, **kwargs):
    """subprocess.Popen that never flashes a console window on Windows."""
    if platform.system() == "Windows":
        # CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP (needed for TerminateProcessGroup)
        kwargs.setdefault("creationflags", 0x08000000 | 0x00000200)
    else:
        kwargs.setdefault("start_new_session", True)  # new process group for killpg
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)
    kwargs.setdefault("text", True)
    return subprocess.Popen(cmd, **kwargs)


# ── Binary discovery ───────────────────────────────────────────────────

def find_ffmpeg(bin_candidates):
    """Look for ffmpeg binary near the exe or in PATH.

    Args:
        bin_candidates: List of directories to search.

    Returns:
        Path to ffmpeg binary or None.
    """
    if platform.system() == "Windows":
        names = ["ffmpeg.exe", "ffmpeg"]
    else:
        names = ["ffmpeg"]

    for d in bin_candidates:
        for name in names:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate):
                return candidate
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def has_ffmpeg(bin_candidates):
    """Return True if ffmpeg is available."""
    return find_ffmpeg(bin_candidates) is not None


import os  # noqa: E402 — needed for find_ffmpeg
