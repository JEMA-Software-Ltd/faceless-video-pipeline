"""
common.py — shared helpers for the faceless video pipeline.

Every stage (script/tts/images/render/qc/upload) and doctor.py import from
here. Nothing in this module talks to a paid API or spends money on import;
it only wires up config, .env, the work directory, chunks.json I/O, an
HTTP-with-backoff helper, and small ffmpeg/ffprobe wrappers.

The pipeline state lives entirely in <work-dir>/chunks.json. Each stage reads
it, adds its own fields, and writes it back atomically, which is what makes
every stage independently re-runnable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import date as _date
from pathlib import Path
from typing import Any, Optional

# Third-party. Kept to the minimum; see requirements.txt for pins.
try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover - surfaced clearly by doctor.py
    yaml = None

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


# ---------------------------------------------------------------------------
# Console encoding
# ---------------------------------------------------------------------------

# Windows consoles default to a legacy codepage (cp1252) that cannot encode the
# em-dashes in our log messages or qc.py's ✓/✗ marks; printing one raises
# UnicodeEncodeError and kills the stage mid-report. Force UTF-8 on the standard
# streams at import time — every stage imports common, and this is a no-op where
# the streams are already UTF-8 (Linux/macOS, or a redirect to a file).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a TextIOWrapper / already detached
        pass


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

# Repo root == directory this file lives in. All default paths hang off it so
# the scripts work the same whether invoked by cron or by hand from elsewhere.
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"
DEFAULT_ENV = REPO_ROOT / ".env"
SCRIPTS_DIR = REPO_ROOT / "scripts"
WORK_ROOT = REPO_ROOT / "work"
CHUNKS_FILENAME = "chunks.json"

# The .env keys the pipeline needs. doctor.py checks every one is present and
# non-empty. FONT_PATH is only strictly required when burned-in captions are
# enabled, so it is tracked separately.
REQUIRED_ENV_KEYS = [
    "GEMINI_API_KEY",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
]
OPTIONAL_ENV_KEYS = [
    "FONT_PATH",  # required only if captions.enabled is true
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(name)-8s %(levelname)-7s %(message)s",
                              datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger


# ---------------------------------------------------------------------------
# Config + env
# ---------------------------------------------------------------------------

def load_config(path: Optional[str | Path] = None) -> dict:
    """Load config.yaml. Fails loudly if PyYAML is missing or the file is absent."""
    if yaml is None:
        raise RuntimeError("PyYAML not installed. Run: pip install -r requirements.txt")
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_env(env_path: Optional[str | Path] = None) -> None:
    """Load .env into os.environ. No-op (with a warning) if the file is absent,
    so that a caller which only needs, say, ffmpeg does not hard-fail."""
    if load_dotenv is None:
        raise RuntimeError("python-dotenv not installed. Run: pip install -r requirements.txt")
    path = Path(env_path) if env_path else DEFAULT_ENV
    if path.exists():
        load_dotenv(path, override=False)


def get_env(key: str, required: bool = True, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if not val and required:
        raise RuntimeError(
            f"Missing required env var {key!r}. Add it to .env (see .env.example)."
        )
    return val or default


# ---------------------------------------------------------------------------
# Work directory + chunks.json
# ---------------------------------------------------------------------------

def today_str() -> str:
    """Local date as YYYY-MM-DD. Cron runs nightly, so 'today' is the run date."""
    return _date.today().isoformat()


def work_dir_for(date_str: str, override: Optional[str | Path] = None) -> Path:
    return Path(override) if override else (WORK_ROOT / date_str)


def ensure_work_dir(date_str: str, override: Optional[str | Path] = None) -> Path:
    wd = work_dir_for(date_str, override)
    for sub in ("", "audio", "images", "clips"):
        (wd / sub).mkdir(parents=True, exist_ok=True)
    return wd


def chunks_path(work_dir: Path) -> Path:
    return work_dir / CHUNKS_FILENAME


def read_chunks(work_dir: Path) -> dict:
    path = chunks_path(work_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run script.py first (it creates chunks.json)."
        )
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_chunks(work_dir: Path, data: dict) -> None:
    """Atomic write: dump to a temp file in the same dir, then os.replace().
    A crash mid-write can never leave a half-written chunks.json behind, which
    matters because stages re-run against it."""
    path = chunks_path(work_dir)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Argument parsing shared across stages
# ---------------------------------------------------------------------------

def build_arg_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--date", default=None,
                   help="Work date YYYY-MM-DD (default: today, local time).")
    p.add_argument("--work-dir", default=None,
                   help="Override the work directory path (default: work/<date>).")
    p.add_argument("--config", default=None,
                   help="Path to config.yaml (default: repo config.yaml).")
    p.add_argument("--dry-run", action="store_true",
                   help="Fast end-to-end validation: ~30s video from a few chunks.")
    p.add_argument("--verbose", "-v", action="store_true", help="Debug logging.")
    return p


class StageContext:
    """Bundle of the things every stage needs, resolved once from args."""

    def __init__(self, args: argparse.Namespace, stage_name: str):
        self.args = args
        self.stage_name = stage_name
        self.log = get_logger(stage_name, verbose=getattr(args, "verbose", False))
        self.date = args.date or today_str()
        self.config = load_config(args.config)
        self.dry_run = bool(getattr(args, "dry_run", False))
        # Stages that download/upload need .env; loading it here is cheap and
        # harmless for the ones that don't.
        load_env()
        self.work_dir = work_dir_for(self.date, args.work_dir)

    def ensure_dirs(self) -> Path:
        self.work_dir = ensure_work_dir(self.date, self.args.work_dir)
        return self.work_dir


# ---------------------------------------------------------------------------
# HTTP with backoff (used by images.py / gemini in script.py)
# ---------------------------------------------------------------------------

class HTTPError(RuntimeError):
    pass


def http_request(method: str, url: str, *, logger: Optional[logging.Logger] = None,
                 max_retries: int = 5, rate_limit_retries: int = 8,
                 backoff_base: float = 2.0, max_delay: float = 60.0,
                 max_retry_after: float = 120.0,
                 timeout: int = 30, **kwargs) -> "requests.Response":
    """requests wrapper with exponential backoff.

    Retries on connection errors, HTTP 429, and 5xx. Honours a Retry-After
    header when the server sends one. Raises HTTPError after the last attempt.

    Rate limiting (429) gets its own, larger allowance: a long image stage can
    fetch ~100 stills and will hit the Pexels hourly cap, and one 429 should
    cost a wait, not the stage. The wait is bounded on both ends —
    `max_delay` caps our own backoff, and a Retry-After longer than
    `max_retry_after` makes us give up NOW rather than stall the pipeline for
    the rest of the hour. Callers (query_pexels/query_pixabay) catch HTTPError
    and fall back to the other provider, so giving up is a degradation, not a
    failure. Jitter keeps parallel-ish retries from re-colliding.
    """
    if requests is None:
        raise RuntimeError("requests not installed. Run: pip install -r requirements.txt")
    log = logger or logging.getLogger("http")

    def backoff(attempt: int) -> float:
        # Exponential, capped, +0-25% jitter.
        return min(backoff_base ** attempt, max_delay) * (1.0 + random.random() * 0.25)

    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if attempt > max_retries:
                raise HTTPError(f"{method} {url} failed after {max_retries} retries: {exc}")
            delay = backoff(attempt)
            log.warning("HTTP %s error (attempt %d/%d): %s — retrying in %.0fs",
                        method, attempt, max_retries, exc, delay)
            time.sleep(delay)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            limit = rate_limit_retries if resp.status_code == 429 else max_retries
            if attempt > limit:
                raise HTTPError(
                    f"{method} {url} -> {resp.status_code} after {limit} retries")

            retry_after = resp.headers.get("Retry-After")
            delay = None
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = None  # HTTP-date form; fall back to our own backoff.
            if delay is not None and delay > max_retry_after:
                raise HTTPError(
                    f"{method} {url} -> {resp.status_code}, Retry-After={delay:.0f}s "
                    f"exceeds max_retry_after={max_retry_after:.0f}s — giving up early")
            if delay is None:
                delay = backoff(attempt)

            log.warning("HTTP %s -> %d (attempt %d/%d) — retrying in %.0fs",
                        method, resp.status_code, attempt, limit, delay)
            time.sleep(delay)
            continue

        return resp


def download_file(url: str, dest: Path, *, logger: Optional[logging.Logger] = None,
                  **kwargs) -> Path:
    """Stream a URL to disk with the same retry policy as http_request."""
    resp = http_request("GET", url, logger=logger, stream=True, **kwargs)
    if resp.status_code != 200:
        raise HTTPError(f"GET {url} -> {resp.status_code}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                fh.write(chunk)
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ---------------------------------------------------------------------------

def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name!r} not found on PATH. See README / run doctor.py.")
    return path


def run(cmd: list[str], *, logger: Optional[logging.Logger] = None,
        capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    """Thin subprocess wrapper with consistent logging."""
    log = logger or logging.getLogger("run")
    log.debug("exec: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr}")
    return result


def ffprobe_json(path: Path, *, logger: Optional[logging.Logger] = None) -> dict:
    """Return ffprobe's full JSON for a media file (format + streams)."""
    ffprobe = require_binary("ffprobe")
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    result = run(cmd, logger=logger, capture=True)
    return json.loads(result.stdout)


def media_duration(path: Path, *, stream: Optional[str] = None,
                   logger: Optional[logging.Logger] = None) -> float:
    """Duration in seconds. stream=None uses container duration; stream='audio'
    or 'video' reads the first matching stream's duration."""
    info = ffprobe_json(path, logger=logger)
    if stream in ("audio", "video"):
        for s in info.get("streams", []):
            if s.get("codec_type") == stream and s.get("duration"):
                return float(s["duration"])
    fmt = info.get("format", {})
    if fmt.get("duration"):
        return float(fmt["duration"])
    # Fall back to any stream duration we can find.
    for s in info.get("streams", []):
        if s.get("duration"):
            return float(s["duration"])
    raise RuntimeError(f"Could not determine duration of {path}")


def fail(logger: logging.Logger, msg: str, code: int = 1) -> None:
    """Log an error and exit non-zero. Used so run.sh halts the pipeline."""
    logger.error(msg)
    sys.exit(code)
