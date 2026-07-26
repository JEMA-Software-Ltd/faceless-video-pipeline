#!/usr/bin/env python3
"""
qc.py — stage 5 of 6. Automated quality control; exits non-zero on any failure
so run.sh halts before upload.

Checks (all thresholds in config.yaml -> qc:):
  1. Audio vs video duration differ by no more than audio_video_max_diff.
  2. blackdetect finds no black segment longer than black_max_duration.
  3. Of `sample_frames` evenly spaced frames, none is a near-single-colour
     frame (> single_colour_threshold of one colour) — catches dead renders.
  4. File bitrate is plausible for the duration (min_kbps..max_kbps) — catches
     a truncated or runaway file.

    python3 qc.py --date 2026-07-26
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import common


PASS, FAIL = "PASS", "FAIL"
_BLACK_DUR_RE = re.compile(r"black_duration:([0-9.]+)")


def check_durations(video: Path, cfg: dict, log) -> tuple[str, str]:
    max_diff = float(cfg.get("audio_video_max_diff", 0.5))
    v = common.media_duration(video, stream="video", logger=log)
    a = common.media_duration(video, stream="audio", logger=log)
    diff = abs(v - a)
    detail = f"video={v:.2f}s audio={a:.2f}s diff={diff:.2f}s (max {max_diff})"
    return (PASS if diff <= max_diff else FAIL), detail


def check_blackdetect(video: Path, cfg: dict, log) -> tuple[str, str]:
    ffmpeg = common.require_binary("ffmpeg")
    max_black = float(cfg.get("black_max_duration", 0.5))
    pix_th = float(cfg.get("black_pixel_threshold", 0.10))
    # blackdetect logs "black_duration:X" to stderr for each detected segment.
    result = common.run(
        [ffmpeg, "-i", str(video),
         "-vf", f"blackdetect=d={max_black}:pic_th=0.98:pix_th={pix_th}",
         "-an", "-f", "null", "-"],
        logger=log, capture=True, check=False,
    )
    durations = [float(x) for x in _BLACK_DUR_RE.findall(result.stderr or "")]
    offenders = [d for d in durations if d > max_black]
    if offenders:
        return FAIL, f"black segment(s) > {max_black}s: {offenders}"
    return PASS, f"no black segment > {max_black}s"


def check_single_colour(video: Path, duration: float, cfg: dict, log) -> tuple[str, str]:
    try:
        from PIL import Image
    except ImportError:
        return FAIL, "Pillow not installed (needed for frame analysis)"
    ffmpeg = common.require_binary("ffmpeg")
    n = int(cfg.get("sample_frames", 10))
    threshold = float(cfg.get("single_colour_threshold", 0.95))

    frames_dir = video.parent / "qc_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    worst = 0.0
    worst_t = 0.0
    for i in range(n):
        t = duration * (i + 0.5) / n
        frame_path = frames_dir / f"frame_{i:02d}.png"
        common.run(
            [ffmpeg, "-y", "-ss", f"{t:.3f}", "-i", str(video),
             "-frames:v", "1", str(frame_path)],
            logger=log, capture=True,
        )
        if not frame_path.exists():
            return FAIL, f"could not extract frame at {t:.1f}s"
        # Downscale + coarsely quantise so near-identical colours group; then
        # measure the single most common colour's share of the frame. tobytes()
        # (rather than getdata()) is stable across Pillow versions.
        img = Image.open(frame_path).convert("RGB").resize((160, 90))
        raw = img.tobytes()  # interleaved RGB, length 160*90*3
        counts = Counter(
            (raw[i] >> 4, raw[i + 1] >> 4, raw[i + 2] >> 4)
            for i in range(0, len(raw), 3)
        )
        frac = counts.most_common(1)[0][1] / (160 * 90)
        if frac > worst:
            worst, worst_t = frac, t

    detail = f"worst frame {worst * 100:.1f}% single colour at {worst_t:.1f}s (max {threshold * 100:.0f}%)"
    return (PASS if worst <= threshold else FAIL), detail


def check_filesize(video: Path, duration: float, cfg: dict, log) -> tuple[str, str]:
    min_kbps = float(cfg.get("min_kbps", 200))
    max_kbps = float(cfg.get("max_kbps", 12000))
    size_bytes = video.stat().st_size
    if duration <= 0:
        return FAIL, "duration is zero"
    kbps = (size_bytes * 8 / 1000.0) / duration
    detail = f"{size_bytes / (1024*1024):.1f} MB -> {kbps:.0f} kbps (allow {min_kbps:.0f}-{max_kbps:.0f})"
    return (PASS if min_kbps <= kbps <= max_kbps else FAIL), detail


def main() -> int:
    parser = common.build_arg_parser("Stage 5: automated QC of video.mp4.")
    args = parser.parse_args()
    ctx = common.StageContext(args, "qc")
    log = ctx.log

    video = ctx.work_dir / "video.mp4"
    if not video.exists():
        common.fail(log, f"{video} not found — run render.py first.")

    qc_cfg = ctx.config.get("qc", {})
    duration = common.media_duration(video, logger=log)

    rows = [
        ("duration match", *check_durations(video, qc_cfg, log)),
        ("blackdetect", *check_blackdetect(video, qc_cfg, log)),
        ("single-colour frames", *check_single_colour(video, duration, qc_cfg, log)),
        ("filesize/bitrate", *check_filesize(video, duration, qc_cfg, log)),
    ]

    print("\nQC report for " + str(video) + "\n" + "=" * 60)
    n_fail = 0
    label_w = max(len(r[0]) for r in rows) + 2
    for label, status, detail in rows:
        if status == FAIL:
            n_fail += 1
        mark = "✓" if status == PASS else "✗"
        print(f"  [{mark}] {status} {label:<{label_w}} {detail}")
    print("=" * 60)

    if n_fail:
        print(f"QC FAILED: {n_fail} check(s) failed. Halting before upload.\n")
        return 1
    print("QC PASSED.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
