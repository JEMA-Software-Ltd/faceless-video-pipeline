#!/usr/bin/env python3
"""
render.py — stage 4 of 6.

Builds video.mp4 from the per-chunk audio and images:

  1. Lay every image on a shared timeline that tracks the concatenated audio,
     so image cuts stay in sync with the narration at each chunk boundary.
  2. Render each image to its OWN short clip with a Ken Burns zoompan move
     (scale=8000:-1 first, per the spec, to stop zoompan jitter), 1920x1080@25.
  3. Concatenate the clips with the concat demuxer (stream copy — no re-encode).
  4. Concatenate the chunk wavs into one track.
  5. Mux video + audio with -c:v copy (video encoded exactly once, in step 2).

Optional burned-in captions live behind config `captions.enabled` (off by
default); the font comes from FONT_PATH in .env because drawtext needs an
absolute path.

    python3 render.py --date 2026-07-26
    python3 render.py --dry-run     # short video, ultrafast preset

Depends on tts.py (audio + durations) and images.py (image paths).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import common


def build_timeline(chunks: list[dict], fps: int) -> list[dict]:
    """Return one segment per image: {image, chunk_index, text, frames}.

    Image cuts land on the audio timeline: a chunk occupies [t_start, t_end]
    (its measured duration), split evenly among its images, and each segment's
    frame count is the difference of rounded frame positions. This keeps total
    video frames == round(total_audio * fps), so audio and video stay aligned.
    """
    segments: list[dict] = []
    t_cursor = 0.0
    frame_cursor = 0
    for chunk in chunks:
        imgs = chunk.get("images", [])
        if not imgs:
            raise RuntimeError(f"chunk {chunk['index']:03d} has no images — run images.py")
        if not chunk.get("duration"):
            raise RuntimeError(f"chunk {chunk['index']:03d} has no measured duration — run tts.py")
        duration = float(chunk["duration"])
        chunk_end = t_cursor + duration
        n = len(imgs)
        for j, img in enumerate(imgs):
            seg_end_t = t_cursor + duration * (j + 1) / n
            end_frame = round(seg_end_t * fps)
            frames = max(1, end_frame - frame_cursor)
            segments.append({
                "image": img,
                "chunk_index": chunk["index"],
                "text": chunk["text"],
                "frames": frames,
            })
            frame_cursor = end_frame
        # Guard against float drift accumulating across chunks.
        t_cursor = chunk_end
    return segments


def zoompan_filter(frames: int, direction: str, w: int, h: int, fps: int,
                   zmax: float = 1.15) -> str:
    """Ken Burns filter for a single still. scale=8000:-1 upscales first so the
    zoom moves in sub-pixel steps (no jitter), then zoompan emits `frames`
    frames from that one image and crops to WxH."""
    rate = (zmax - 1.0) / max(frames - 1, 1)
    if direction == "in":
        zexpr = f"min(1+{rate:.8f}*on,{zmax})"
    else:  # "out": start zoomed in, ease back to 1.0
        zexpr = f"max({zmax}-{rate:.8f}*on,1)"
    # x/y keep the crop centred as zoom changes.
    return (
        f"scale=8000:-1,"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={w}x{h}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )


def wrap_caption(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width)) or text


def render_clip(seg: dict, out_path: Path, cfg: dict, direction: str,
                caption_file: Path | None, log) -> None:
    ffmpeg = common.require_binary("ffmpeg")
    vid = cfg.get("video", {})
    w = int(vid.get("width", 1920))
    h = int(vid.get("height", 1080))
    fps = int(vid.get("fps", 25))
    preset = cfg.get("_preset", vid.get("preset", "medium"))
    crf = str(cfg.get("_crf", vid.get("crf", 20)))

    vf = zoompan_filter(seg["frames"], direction, w, h, fps)

    if caption_file is not None:
        cap_cfg = cfg.get("captions", {})
        font = common.get_env("FONT_PATH", required=True)
        # textfile= avoids drawtext's brutal escaping rules for the caption body.
        drawtext = (
            f"drawtext=fontfile='{font}':textfile='{caption_file}':"
            f"fontcolor={cap_cfg.get('font_colour', 'white')}:"
            f"fontsize={int(cap_cfg.get('font_size', 44))}:"
            f"borderw={int(cap_cfg.get('border_width', 3))}:"
            f"bordercolor={cap_cfg.get('border_colour', 'black')}:"
            f"x=(w-text_w)/2:y=h-{int(cap_cfg.get('bottom_margin', 90))}-text_h:"
            f"line_spacing=8"
        )
        vf = f"{vf},{drawtext}"

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-i", seg["_image_abs"],
        "-frames:v", str(seg["frames"]),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-g", str(fps), "-keyint_min", str(fps),
        "-an",
        str(out_path),
    ]
    common.run(cmd, logger=log)


def concat_demuxer(list_file: Path, out_path: Path, log, copy: bool = True) -> None:
    ffmpeg = common.require_binary("ffmpeg")
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    cmd += (["-c", "copy"] if copy else [])
    cmd += [str(out_path)]
    common.run(cmd, logger=log)


def write_concat_list(paths: list[Path], list_file: Path) -> None:
    # Absolute paths + -safe 0 so the demuxer doesn't choke on the location.
    lines = []
    for p in paths:
        # ffconcat requires single quotes in path escaped as '\''.
        safe = str(p).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = common.build_arg_parser("Stage 4: render video.mp4.")
    args = parser.parse_args()
    ctx = common.StageContext(args, "render")
    log = ctx.log
    ctx.ensure_dirs()

    data = common.read_chunks(ctx.work_dir)
    chunks = data.get("chunks", [])
    if not chunks:
        common.fail(log, "No chunks in chunks.json — run earlier stages first.")

    vid = ctx.config.get("video", {})
    fps = int(vid.get("fps", 25))

    # Dry-run: trade quality for speed so the whole chain finishes in ~2 min.
    if ctx.dry_run:
        ctx.config["_preset"] = "ultrafast"
        ctx.config["_crf"] = 28
        log.info("DRY-RUN: using ultrafast preset for a fast validation encode.")

    captions_on = bool(ctx.config.get("captions", {}).get("enabled", False))
    if captions_on:
        # Fail early if captions are on but the font is missing/relative.
        font = common.get_env("FONT_PATH", required=True)
        if not Path(font).is_absolute() or not Path(font).exists():
            common.fail(log, f"captions.enabled but FONT_PATH invalid: {font!r}")
        log.info("Captions ON, font=%s", font)

    segments = build_timeline(chunks, fps)
    total_frames = sum(s["frames"] for s in segments)
    log.info("Timeline: %d image(s) -> %d frames (%.1fs @ %dfps).",
             len(segments), total_frames, total_frames / fps, fps)

    clips_dir = ctx.work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale clips so a re-render never concatenates leftovers.
    for old in clips_dir.glob("*.mp4"):
        old.unlink()
    for old in clips_dir.glob("cap_*.txt"):
        old.unlink()

    # Render each image to its own clip.
    clip_paths: list[Path] = []
    for i, seg in enumerate(segments):
        seg["_image_abs"] = str((ctx.work_dir / seg["image"]).resolve())
        clip_path = clips_dir / f"clip_{i:04d}.mp4"

        caption_file = None
        if captions_on:
            cap_cfg = ctx.config.get("captions", {})
            wrapped = wrap_caption(seg["text"], int(cap_cfg.get("max_chars_per_line", 48)))
            caption_file = clips_dir / f"cap_{i:04d}.txt"
            caption_file.write_text(wrapped, encoding="utf-8")

        direction = "in" if i % 2 == 0 else "out"
        log.info("clip %04d/%d: %s (%d frames, zoom-%s)",
                 i + 1, len(segments), Path(seg["image"]).name, seg["frames"], direction)
        render_clip(seg, clip_path, ctx.config, direction, caption_file, log)
        clip_paths.append(clip_path)

    # Concat clips (stream copy — this is why per-clip params must match).
    video_list = clips_dir / "video_list.txt"
    write_concat_list(clip_paths, video_list)
    silent_video = ctx.work_dir / "video_silent.mp4"
    log.info("Concatenating %d clips (copy)...", len(clip_paths))
    concat_demuxer(video_list, silent_video, log, copy=True)

    # Concat the chunk wavs into one narration track.
    audio_paths = [ctx.work_dir / c["audio_file"] for c in chunks]
    for p in audio_paths:
        if not p.exists():
            common.fail(log, f"Missing audio {p} — run tts.py first.")
    audio_list = clips_dir / "audio_list.txt"
    write_concat_list(audio_paths, audio_list)
    full_audio = ctx.work_dir / "audio" / "full.wav"
    log.info("Concatenating %d audio chunk(s) (copy)...", len(audio_paths))
    concat_demuxer(audio_list, full_audio, log, copy=True)

    # Final mux: copy the video, encode audio once. No 15-min video re-encode.
    ffmpeg = common.require_binary("ffmpeg")
    out_path = ctx.work_dir / "video.mp4"
    log.info("Muxing final video (-c:v copy)...")
    common.run([
        ffmpeg, "-y",
        "-i", str(silent_video),
        "-i", str(full_audio),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ], logger=log)

    dur = common.media_duration(out_path, logger=log)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    log.info("Rendered %s — %.1fs, %.1f MB.", out_path, dur, size_mb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
