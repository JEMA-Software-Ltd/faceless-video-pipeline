#!/usr/bin/env python3
"""
doctor.py — preflight checks for the Clara pipeline.

Run this FIRST on a fresh machine. It verifies external tools, the Python
environment, secrets, and paths, then prints a pass/fail table. It exits
non-zero if anything is a hard FAIL, so it can gate a cron run.

    python3 doctor.py

Nothing here spends money or uploads anything.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import common


# A check returns (status, detail). status is one of PASS / FAIL / WARN.
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def check_binary(name: str) -> tuple[str, str]:
    path = shutil.which(name)
    if path:
        return PASS, path
    return FAIL, f"not found on PATH — install {name}"


def check_espeak() -> tuple[str, str]:
    # Kokoro uses espeak-ng for phonemisation. The binary is usually
    # 'espeak-ng', but some distros ship it as 'espeak'.
    for candidate in ("espeak-ng", "espeak"):
        path = shutil.which(candidate)
        if path:
            return PASS, path
    return FAIL, "espeak-ng not found — apt install espeak-ng (or brew install espeak-ng)"


def check_python_package(module: str, pip_name: str | None = None) -> tuple[str, str]:
    pip_name = pip_name or module
    try:
        # find_spec on a dotted name imports parent packages; if a parent (e.g.
        # 'google') is absent it RAISES ModuleNotFoundError rather than
        # returning None, so treat any import-time error as "missing".
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        spec = None
    if spec is not None:
        return PASS, f"import {module} OK"
    return FAIL, f"missing — pip install {pip_name}"


def check_kokoro_reachable(log) -> tuple[str, str]:
    """We do not download the full model here (it's large); we confirm the
    package imports and that Hugging Face is reachable, since the first real
    tts.py run pulls the weights from there."""
    if importlib.util.find_spec("kokoro") is None:
        return FAIL, "kokoro not installed — pip install -r requirements.txt"
    try:
        # HEAD the HF model repo. Reachable + 2xx/3xx == weights are fetchable.
        resp = common.http_request(
            "HEAD", "https://huggingface.co/hexgrad/Kokoro-82M",
            logger=log, max_retries=1, timeout=15,
        )
        if resp.status_code < 400:
            return PASS, "kokoro installed; Hugging Face reachable (weights download on first run)"
        return WARN, f"kokoro installed; HF returned {resp.status_code} (check on first run)"
    except Exception as exc:  # network blocked, etc.
        return WARN, f"kokoro installed; could not reach Hugging Face ({exc})"


def check_env_keys() -> list[tuple[str, str, str]]:
    """One row per required key. Returns (label, status, detail)."""
    rows = []
    common.load_env()
    for key in common.REQUIRED_ENV_KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            rows.append((f".env {key}", PASS, f"set ({len(val)} chars)"))
        else:
            rows.append((f".env {key}", FAIL, "missing or empty"))
    return rows


def check_work_dir_writable() -> tuple[str, str]:
    try:
        common.WORK_ROOT.mkdir(parents=True, exist_ok=True)
        probe = common.WORK_ROOT / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return PASS, f"writable: {common.WORK_ROOT}"
    except Exception as exc:
        return FAIL, f"cannot write to {common.WORK_ROOT}: {exc}"


def check_font(config: dict) -> tuple[str, str]:
    captions_on = bool(config.get("captions", {}).get("enabled", False))
    font_path = os.environ.get("FONT_PATH", "").strip()
    if not font_path:
        if captions_on:
            return FAIL, "captions enabled but FONT_PATH not set in .env"
        return WARN, "FONT_PATH not set (fine while captions are disabled)"
    if not os.path.isabs(font_path):
        return FAIL, f"FONT_PATH must be absolute (drawtext needs it): {font_path}"
    if Path(font_path).exists():
        return PASS, font_path
    return FAIL, f"FONT_PATH does not exist: {font_path}"


def check_config() -> tuple[str, str]:
    try:
        common.load_config()
        return PASS, str(common.DEFAULT_CONFIG)
    except Exception as exc:
        return FAIL, f"config.yaml problem: {exc}"


def main() -> int:
    log = common.get_logger("doctor")
    print("\nClara pipeline — preflight (doctor.py)\n" + "=" * 60)

    rows: list[tuple[str, str, str]] = []

    # Python version
    py_ok = sys.version_info >= (3, 11)
    rows.append(("Python >= 3.11", PASS if py_ok else FAIL,
                 sys.version.split()[0]))

    # External binaries
    for label, name in (("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe")):
        status, detail = check_binary(name)
        rows.append((label, status, detail))
    rows.append(("espeak-ng", *check_espeak()))

    # Python packages
    for module, pip_name in (
        ("yaml", "PyYAML"), ("requests", "requests"),
        ("dotenv", "python-dotenv"), ("PIL", "Pillow"),
        ("soundfile", "soundfile"), ("google.genai", "google-genai"),
        ("googleapiclient", "google-api-python-client"),
        ("google_auth_oauthlib", "google-auth-oauthlib"),
    ):
        rows.append((f"py: {pip_name}", *check_python_package(module, pip_name)))

    # config.yaml
    rows.append(("config.yaml", *check_config()))

    # Kokoro model reachability
    rows.append(("Kokoro model", *check_kokoro_reachable(log)))

    # .env keys
    rows.extend(check_env_keys())

    # Work dir + font (font needs config for the captions flag)
    rows.append(("work/ writable", *check_work_dir_writable()))
    try:
        cfg = common.load_config()
    except Exception:
        cfg = {}
    rows.append(("font file", *check_font(cfg)))

    # Render the table
    label_w = max(len(r[0]) for r in rows) + 2
    n_fail = n_warn = 0
    for label, status, detail in rows:
        if status == FAIL:
            n_fail += 1
        elif status == WARN:
            n_warn += 1
        mark = {PASS: "✓", FAIL: "✗", WARN: "!"}[status]
        print(f"  [{mark}] {status:<4} {label:<{label_w}} {detail}")

    print("=" * 60)
    if n_fail:
        print(f"RESULT: {n_fail} failure(s), {n_warn} warning(s). "
              f"Fix the ✗ items before running the pipeline.\n")
        return 1
    if n_warn:
        print(f"RESULT: all checks passed ({n_warn} warning(s) — review the ! items).\n")
        return 0
    print("RESULT: all checks passed. You're good to go.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
