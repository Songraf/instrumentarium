#!/usr/bin/env python3
"""Instrumentarium — download job logger and probe-meta."""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

from .state import state
from .utils import detect_platform, human_size, parse_speed, popen_no_console as _popen, find_ffmpeg as _find_ffmpeg

log = logging.getLogger("instrumentarium.server.download")

# ── Download jobs ─────────────────────────────────────────────────────

download_jobs = {}
probe_meta_jobs = {}

# ── Probe-meta cache ─────────────────────────────────────────────────
# Cache results by (url, format_id) to avoid redundant downloads when
# switching modes or re-probing the same link. Format: {key: {"filesize": int, "duration": float}}
_probe_meta_cache: dict[str, dict] = {}

# ── Probe-meta runner ─────────────────────────────────────────────────

_PROBE_DURATION = 30  # seconds of video content to download before extrapolating


def _cache_key(url, format_id):
    """Build cache key for (url, format_id) pair."""
    return f"{url}\x00{format_id or ''}"


def _run_probe_meta(jid, url, yt_path, format_id, video_duration):
    """Run yt-dlp probe download in a background thread and store result.

    Strategy: download a fixed duration of video content (e.g. 30 seconds)
    and extrapolate to the total duration. This is more reliable than
    downloading for a fixed wall-clock time because it's based on video time,
    not network speed.
    """
    tmpdir = tempfile.mkdtemp(prefix="instr_probe_")
    try:
        tmpl = os.path.join(tmpdir, "probe.%(ext)s")
        if format_id == '__best_audio__':
            # For audio, use bestaudio with fallback to best (video+audio)
            # yt-dlp will extract audio during post-processing
            fmt = "bestaudio[ext=m4a]/bestaudio/best"
        elif format_id and "+" not in format_id:
            fmt = format_id + "+bestaudio/best"
        else:
            fmt = format_id if format_id else "best"
        # Use --download-sections to grab exactly PROBE_DURATION seconds of video content
        download_section = f"*0-{_PROBE_DURATION}"
        cmd = [yt_path, "-f", fmt,
               "--download-sections", download_section,
               "--no-playlist", "--no-check-certificates",
               "--retries", "1", "--newline", "--no-progress"]
        if state.cookies_path:
            cmd += ["--cookies", state.cookies_path]
        cmd.extend(["-o", tmpl, url])
        log.info("/probe-meta thread %s: starting format_id=%s fmt=%s section=%s", jid, format_id, fmt, download_section)
        proc = _popen(cmd)
        # Allow extra wall-clock time (video might download slower than real-time)
        wall_timeout = _PROBE_DURATION + 60
        try:
            proc.communicate(timeout=wall_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except Exception:
            pass
        probe_files = [f for f in os.listdir(tmpdir) if not f.startswith(".") and not f.endswith(".part")]
        total_size = 0
        if probe_files:
            for f in probe_files:
                total_size += os.path.getsize(os.path.join(tmpdir, f))
        result = {"filesize": total_size if total_size > 0 else None,
                  "probe_duration": _PROBE_DURATION}
        if video_duration and video_duration > _PROBE_DURATION and total_size > 0:
            if format_id == '__best_audio__':
                # For audio, the downloaded file is video+audio muxed.
                # Extrapolating total_size gives video+audio size, not pure audio.
                # Estimate audio-only size using typical audio bitrate (~128 kbps).
                audio_bitrate_kbps = 128
                audio_size = int(video_duration * audio_bitrate_kbps * 1024 / 8)
                result["filesize"] = audio_size
                log.info("/probe-meta thread %s: audio estimate %d MB (based on %dkbps)",
                         jid, audio_size / (1024*1024), audio_bitrate_kbps)
            else:
                result["filesize"] = int(total_size * (video_duration / _PROBE_DURATION))
        probe_meta_jobs[jid] = {"status": "done", **result}
        cache_key = _cache_key(url, format_id)
        _probe_meta_cache[cache_key] = {"filesize": result["filesize"] or 0, "duration": float(video_duration or 0)}
        log.info("/probe-meta thread %s: done raw_size=%d estimated_size=%d", jid, total_size, result["filesize"] or 0)
    except Exception as e:
        log.error("/probe-meta thread %s: error %s", jid, e)
        probe_meta_jobs[jid] = {"status": "error", "filesize": None, "error": str(e)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── JobLogger ──────────────────────────────────────────────────────────

class JobLogger(threading.Thread):
    """Download job logger — runs yt-dlp subprocess and streams output."""

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
        out_dir = state.output_base
        os.makedirs(out_dir, exist_ok=True)

        ffmpeg = _find_ffmpeg(state.bin_candidates)
        ffmpeg_ok = ffmpeg is not None

        if self.mode == "audio":
            if ffmpeg_ok:
                fmt = "bestaudio[ext=m4a]/bestaudio/best"
            else:
                fmt = "bestaudio[ext=m4a]/bestaudio"
            post = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
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
                fmt = "bestvideo+bestaudio/best"
                post = ["--merge-output-format", "mp4",
                        "--postprocessor-args", "ffmpeg:-c:a aac -b:a 128k"]
            else:
                fmt = "best[ext=mp4]/best"
                post = []
                j["log"].append(
                    "[warn] ffmpeg not found — video may have no sound. "
                    "Install ffmpeg and restart for full quality.")

        out_tmpl = os.path.join(out_dir, "%(title).120s [%(id)s].%(ext)s")
        cmd = [self.yt, "-f", fmt, *post, "-o", out_tmpl,
               "--no-playlist", "--retries", "3",
               "--newline", "--progress", "--force-overwrites"]
        if state.cookies_path:
            cmd += ["--cookies", state.cookies_path]
        cmd.append(self.url)
        if ffmpeg_ok:
            cmd += ["--embed-metadata", "--embed-thumbnail"]
        if ffmpeg_ok:
            cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]

        j["log"].append(f"[yt-dlp] {self.yt}")
        j["log"].append(f"[cmd] {' '.join(cmd)}")

        try:
            proc = _popen(cmd)
            state.active_proc = proc
            stdout_data = []
            filepath = None
            last_progress_time = time.time()
            STALL_INTERVAL = 15

            while True:
                line = proc.stdout.readline() if proc.stdout else None
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                    now = time.time()
                    if now - last_progress_time > STALL_INTERVAL:
                        last_progress_time = now
                        j["stall_warning"] = "Скорость загрузки 0 B/s — проверьте подключение к интернету"
                    continue

                line = line.rstrip('\n')
                stdout_data.append(line)
                j["log"].append(line)
                last_progress_time = time.time()
                j.pop("stall_warning", None)

                if "[download] Destination:" in line:
                    filepath = line.split("Destination:", 1)[1].strip()
                elif line.startswith("[Merger]") and "into" in line:
                    idx = line.rfind('"')
                    idx2 = line.rfind('"', 0, idx)
                    if idx > idx2:
                        filepath = line[idx2+1:idx]
                    j["log"].append("[info] Audio+Video merge ✓")

                if "[download]" in line and " at " in line and "%" in line and " of " in line:
                    try:
                        size_str = line.split(" of ")[1].split(" at ")[0].strip()
                        j["filesize"] = parse_speed(size_str)
                    except (IndexError, ValueError):
                        pass
                    try:
                        speed_str = line.split(" at ")[1].split(" ")[0]
                        j["speed"] = parse_speed(speed_str)
                    except (IndexError, ValueError):
                        pass

            proc.wait()
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
                j["log"].append(f"[done] {j.get('filename','?')} ({human_size(size)})")
            else:
                j["status"] = "error"
                j["speed"] = None
                j["log"].append(f"[error] exit code {proc.returncode}")
        except FileNotFoundError as e:
            j["status"] = "error"
            j["log"].append(f"[error] yt-dlp not found: {self.yt}")
            j["log"].append(f"[error] {e}")
        except Exception as e:
            j["status"] = "error"
            j["log"].append(f"[error] {e}")
        finally:
            if state.active_proc is proc:
                state.active_proc = None


# ── Job cleanup (TTL) ──────────────────────────────────────────────────

def cleanup_old_jobs(max_age_seconds=3600):
    """Remove completed download_jobs and probe_meta_jobs older than max_age_seconds."""
    now = time.time()
    for d in (download_jobs, probe_meta_jobs):
        stale = [k for k, v in d.items()
                 if v.get("status") in ("done", "error")
                 and now - v.get("_completed_at", now) > max_age_seconds]
        for k in stale:
            del d[k]


def mark_job_completed(jid, job_dict):
    """Mark a job as completed with timestamp for TTL cleanup."""
    job_dict[jid]["_completed_at"] = time.time()
