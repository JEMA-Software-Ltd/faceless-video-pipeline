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

import hashlib
import json
import math
import sys
import textwrap
from pathlib import Path

import common
from figures import choose_significant_figure


def _bounded_durations(
    total: float,
    weights: list[float],
    minimum: float = 4.0,
    maximum: float = 15.0,
) -> list[float]:
    """Normalise positive weights to `total`, respecting bounds when feasible."""
    if not weights:
        return []
    count = len(weights)
    if total < count * minimum or total > count * maximum:
        return [total / count] * count

    result: list[float | None] = [None] * count
    free = set(range(count))
    remaining = total
    while free:
        weight_total = sum(weights[i] for i in free)
        provisional = {
            i: remaining * weights[i] / weight_total
            for i in free
        }
        low = [i for i, value in provisional.items() if value < minimum]
        high = [i for i, value in provisional.items() if value > maximum]
        if not low and not high:
            for i, value in provisional.items():
                result[i] = value
            break
        for i in low:
            result[i] = minimum
            remaining -= minimum
            free.remove(i)
        for i in high:
            if i not in free:
                continue
            result[i] = maximum
            remaining -= maximum
            free.remove(i)
    return [float(value) for value in result]


def _pace_weights(chunk_index: int, count: int, variation: float) -> list[float]:
    variation = max(0.0, min(float(variation), 0.95))
    if variation == 0 or count <= 1:
        return [1.0] * count
    weights = []
    for image_index in range(count):
        digest = hashlib.sha256(
            f"pace-v1:{chunk_index}:{image_index}".encode("utf-8")
        ).digest()
        unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        signed = unit * 2.0 - 1.0
        weights.append(1.0 + signed * variation)
    return weights


def _rebalance_frame_bounds(
    segments: list[dict],
    fps: int,
    minimum: float,
    maximum: float,
) -> None:
    """Move adjacent cut points so enabled pacing stays within hard bounds.

    This is needed for a narration chunk shorter than the minimum shot length:
    its still extends briefly across the next audio boundary, and the next
    still starts correspondingly later. Total frames and audio sync do not
    change.
    """
    min_frames = math.ceil(minimum * fps)
    max_frames = math.floor(maximum * fps)

    for i, segment in enumerate(segments):
        missing = min_frames - int(segment["frames"])
        if missing <= 0:
            continue
        for neighbour in (i + 1, i - 1):
            if not 0 <= neighbour < len(segments):
                continue
            available = int(segments[neighbour]["frames"]) - min_frames
            take = min(missing, max(0, available))
            if take:
                segment["frames"] += take
                segments[neighbour]["frames"] -= take
                missing -= take
            if missing == 0:
                break
        if missing:
            raise RuntimeError(
                f"Cannot keep shot {i} at least {minimum:g}s without breaking timing"
            )

    for i, segment in enumerate(segments):
        excess = int(segment["frames"]) - max_frames
        if excess <= 0:
            continue
        for neighbour in (i + 1, i - 1):
            if not 0 <= neighbour < len(segments):
                continue
            capacity = max_frames - int(segments[neighbour]["frames"])
            give = min(excess, max(0, capacity))
            if give:
                segment["frames"] -= give
                segments[neighbour]["frames"] += give
                excess -= give
            if excess == 0:
                break
        if excess:
            raise RuntimeError(
                f"Cannot keep shot {i} at most {maximum:g}s without breaking timing"
            )

    global_cursor = 0
    for segment in segments:
        start = global_cursor
        global_cursor += int(segment["frames"])
        segment["chunk_offset_start"] = (
            start - int(segment["_chunk_start_frame"])
        ) / fps
        segment["chunk_offset_end"] = (
            global_cursor - int(segment["_chunk_start_frame"])
        ) / fps


def build_timeline(
    chunks: list[dict],
    fps: int,
    pace_variation: float = 0.0,
    min_shot_seconds: float = 4.0,
    max_shot_seconds: float = 15.0,
) -> list[dict]:
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
        weights = _pace_weights(int(chunk["index"]), n, pace_variation)
        shot_durations = _bounded_durations(
            duration, weights, min_shot_seconds, max_shot_seconds,
        )
        chunk_frame_start = frame_cursor
        shot_cursor = 0.0
        for j, img in enumerate(imgs):
            shot_cursor += shot_durations[j]
            seg_end_t = chunk_end if j == n - 1 else t_cursor + shot_cursor
            end_frame = round(seg_end_t * fps)
            frames = max(1, end_frame - frame_cursor)
            segments.append({
                "image": img,
                "chunk_index": chunk["index"],
                "text": chunk["text"],
                "frames": frames,
                "chunk_duration": duration,
                "_chunk_start_frame": chunk_frame_start,
            })
            frame_cursor = end_frame
        # Guard against float drift accumulating across chunks.
        t_cursor = chunk_end
    if pace_variation:
        _rebalance_frame_bounds(
            segments, fps, min_shot_seconds, max_shot_seconds,
        )
    else:
        global_cursor = 0
        for segment in segments:
            start = global_cursor
            global_cursor += int(segment["frames"])
            segment["chunk_offset_start"] = (
                start - int(segment["_chunk_start_frame"])
            ) / fps
            segment["chunk_offset_end"] = (
                global_cursor - int(segment["_chunk_start_frame"])
            ) / fps
    return segments


def zoompan_filter(frames: int, direction: str, w: int, h: int, fps: int,
                   zmax: float = 1.15) -> str:
    """Ken Burns filter for a single still. scale=8000:-1 upscales first so the
    zoom moves in sub-pixel steps (no jitter), then zoompan emits `frames`
    frames from that one image and crops to WxH.

    Deliberately stops short of setsar/format so the colour grade can run while
    the frames are still full-chroma; render_clip appends those two.
    """
    rate = (zmax - 1.0) / max(frames - 1, 1)
    if direction == "in":
        zexpr = f"min(1+{rate:.8f}*on,{zmax})"
    else:  # "out": start zoomed in, ease back to 1.0
        zexpr = f"max({zmax}-{rate:.8f}*on,1)"
    # x/y keep the crop centred as zoom changes.
    return (
        f"scale=8000:-1,"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={w}x{h}:fps={fps}"
    )


def grade_filter(cfg: dict) -> str:
    """Colour grade for one clip, or "" when grade.enabled is false.

      eq           -> saturation / contrast / gamma (1.0 is neutral for each)
      colorbalance -> shadow tint only; midtones and highlights untouched
      vignette     -> optional soft corner falloff

    Returning "" (rather than a no-op filter) keeps a disabled grade completely
    out of the chain, so `grade.enabled: false` renders exactly what the
    pre-grade pipeline rendered.
    """
    g = cfg.get("grade") or {}
    if not g.get("enabled", False):
        return ""

    parts = [
        "eq="
        f"saturation={float(g.get('saturation', 1.0)):.4f}:"
        f"contrast={float(g.get('contrast', 1.0)):.4f}:"
        f"gamma={float(g.get('gamma', 1.0)):.4f}"
    ]

    tint = g.get("shadow_tint") or {}
    rs = float(tint.get("r", 0.0))
    gs = float(tint.get("g", 0.0))
    bs = float(tint.get("b", 0.0))
    if rs or gs or bs:
        # rs/gs/bs are the SHADOW knobs; rm/gm/bm and rh/gh/bh stay at 0.
        parts.append(f"colorbalance=rs={rs:.4f}:gs={gs:.4f}:bs={bs:.4f}")

    if g.get("vignette", False):
        parts.append(f"vignette=angle={float(g.get('vignette_angle', 0.45)):.4f}")

    return ",".join(parts)


def wrap_caption(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width)) or text


def _filter_path(path: Path | str) -> str:
    return Path(path).resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def _overlay_font() -> Path:
    configured = common.get_env("FONT_PATH", required=False)
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_absolute() and candidate.exists():
            return candidate
    raise RuntimeError(
        "overlays.enabled but no usable FONT_PATH or Windows Arial font was found"
    )


def overlay_for_segment(seg: dict, cfg: dict) -> dict | None:
    overlay_cfg = cfg.get("overlays") or {}
    if not overlay_cfg.get("enabled", False):
        return None
    if int(overlay_cfg.get("max_per_chunk", 1)) < 1:
        return None
    text = str(seg["text"])
    figure = choose_significant_figure(text)
    if figure is None:
        return None

    # Weight toward the end of the spoken phrase, then add a small late bias.
    char_point = figure.start * 0.25 + figure.end * 0.75
    spoken_at = float(seg["chunk_duration"]) * char_point / max(len(text), 1) + 0.18
    display_for = float(overlay_cfg.get("duration", 2.5))
    overlay_start = min(spoken_at, max(0.0, float(seg["chunk_duration"]) - 0.2))
    overlay_end = min(float(seg["chunk_duration"]), overlay_start + display_for)

    segment_start = float(seg["chunk_offset_start"])
    segment_end = float(seg["chunk_offset_end"])
    start = max(overlay_start, segment_start)
    end = min(overlay_end, segment_end)
    if end - start < 0.08:
        return None
    return {
        "display": figure.display,
        "start": start - segment_start,
        "end": end - segment_start,
        "source_start": figure.start,
        "source_end": figure.end,
    }


def render_clip(seg: dict, out_path: Path, cfg: dict, direction: str,
                caption_file: Path | None, overlay_file: Path | None,
                overlay: dict | None, log) -> None:
    ffmpeg = common.require_binary("ffmpeg")
    vid = cfg.get("video", {})
    w = int(vid.get("width", 1920))
    h = int(vid.get("height", 1080))
    fps = int(vid.get("fps", 25))
    preset = cfg.get("_preset", vid.get("preset", "medium"))
    crf = str(cfg.get("_crf", vid.get("crf", 20)))

    # zoompan -> grade -> setsar/format -> captions. The grade runs before the
    # yuv420p conversion (full chroma), and captions go on top of it so the
    # text keeps its configured colour instead of being desaturated too.
    vf = zoompan_filter(seg["frames"], direction, w, h, fps)
    grade = grade_filter(cfg)
    if grade:
        vf = f"{vf},{grade}"
    vf = f"{vf},setsar=1,format=yuv420p"

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

    if overlay_file is not None and overlay is not None:
        overlay_cfg = cfg.get("overlays") or {}
        start = float(overlay["start"])
        end = float(overlay["end"])
        fade = min(0.35, max(0.04, (end - start) / 3.0))
        alpha = (
            f"if(lt(t,{start + fade:.4f}),(t-{start:.4f})/{fade:.4f},"
            f"if(lt(t,{end - fade:.4f}),1,(t-{end:.4f})/-{fade:.4f}))"
        )
        draw_number = (
            f"drawtext=fontfile='{_filter_path(_overlay_font())}':"
            f"textfile='{_filter_path(overlay_file)}':"
            "fontcolor=0xF2EBDD:"
            f"fontsize={int(overlay_cfg.get('font_size', 96))}:"
            "shadowcolor=black@0.65:shadowx=4:shadowy=4:"
            "x=110:y=h-text_h-120:"
            f"alpha='{alpha}':enable='between(t,{start:.4f},{end:.4f})'"
        )
        vf = f"{vf},{draw_number}"

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


def render_fingerprint(cfg: dict, chunks: list[dict]) -> dict:
    relevant = {
        "schema": 1,
        "video": {
            key: (cfg.get("video") or {}).get(key)
            for key in (
                "width", "height", "fps", "crf", "preset",
                "pace_variation",
            )
        },
        "grade": cfg.get("grade") or {},
        "captions": cfg.get("captions") or {},
        "overlays": cfg.get("overlays") or {},
        "sources": [
            {
                "index": c.get("index"),
                "duration": c.get("duration"),
                "text": c.get("text"),
                "images": c.get("images") or [],
            }
            for c in chunks
        ],
    }
    encoded = json.dumps(
        relevant, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "relevant": relevant,
    }


def clip_is_valid(path: Path, expected_frames: int, fps: int, log=None) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        duration = common.media_duration(path, stream="video", logger=log)
    except (RuntimeError, ValueError):
        return False
    expected = expected_frames / fps
    return abs(duration - expected) <= max(2.0 / fps, 0.08)


def prepare_clip_cache(
    clips_dir: Path,
    fingerprint: dict,
    force: bool,
    log,
) -> bool:
    """Invalidate incompatible clips and write the resume fingerprint.

    Returns True when existing clips were invalidated.
    """
    fingerprint_path = clips_dir / "render_fingerprint.json"
    prior = None
    if fingerprint_path.exists():
        try:
            prior = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior = None
    changed = prior is None or prior.get("sha256") != fingerprint["sha256"]
    invalidate = bool(force or changed)
    if invalidate:
        reason = "--force" if force else "render inputs/config changed"
        stale = list(clips_dir.glob("clip_*.mp4"))
        for path in stale:
            path.unlink()
        log.info("Clip cache invalidated (%s): removed %d clip(s).", reason, len(stale))
    else:
        log.info("Clip cache fingerprint matches; valid clips will be reused.")
    fingerprint_path.write_text(
        json.dumps(fingerprint, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return invalidate


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


def narration_filter(audio_cfg: dict, duration: float) -> str:
    parts = [
        f"highpass=f={float(audio_cfg.get('highpass_hz', 80)):.2f}",
        "equalizer="
        f"f={float(audio_cfg.get('presence_hz', 3000)):.2f}:"
        "t=q:w=1:"
        f"g={float(audio_cfg.get('presence_gain_db', 2)):.2f}",
    ]
    if audio_cfg.get("compress", True):
        parts.append(
            "acompressor=threshold=0.125:ratio=2:attack=20:"
            "release=250:makeup=1.35:knee=2.5"
        )
    if audio_cfg.get("normalise", True):
        parts.append(
            f"loudnorm=I={float(audio_cfg.get('target_lufs', -16)):.1f}:"
            "TP=-1.5:LRA=11"
        )
    parts.extend([
        "aresample=48000:async=1:first_pts=0",
        f"apad=whole_dur={duration:.6f}",
        f"atrim=duration={duration:.6f}",
        "asetpts=N/SR/TB",
    ])
    return ",".join(parts)


def build_mux_command(
    ffmpeg: str,
    silent_video: Path,
    full_audio: Path,
    out_path: Path,
    duration: float,
    cfg: dict,
) -> tuple[list[str], str]:
    audio_cfg = cfg.get("audio") or {}
    if not audio_cfg.get("enabled", False):
        return ([
            ffmpeg, "-y",
            "-i", str(silent_video),
            "-i", str(full_audio),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(out_path),
        ], "disabled")

    inputs = [
        ffmpeg, "-y",
        "-i", str(silent_video),
        "-i", str(full_audio),
    ]
    narr = narration_filter(audio_cfg, duration)
    music_value = str(audio_cfg.get("music_path") or "").strip()
    if not music_value:
        graph = f"[1:a]{narr}[aout]"
        description = "narration polish (-16 LUFS target, compression/EQ, exact pad)"
    else:
        music_path = Path(music_value)
        if not music_path.is_absolute():
            music_path = common.REPO_ROOT / music_path
        if not music_path.exists():
            raise RuntimeError(f"Configured audio.music_path does not exist: {music_path}")
        inputs.extend(["-stream_loop", "-1", "-i", str(music_path)])
        gain = float(audio_cfg.get("music_gain_db", -26))
        duck_db = float(audio_cfg.get("duck_db", -3))
        # sidechaincompress does not expose a dB reduction ceiling. The user
        # knob maps conservatively to its ratio; this branch remains inactive
        # while music_path is empty.
        duck_ratio = max(1.1, 1.0 + abs(duck_db) / 3.0)
        fade_out_start = max(0.0, duration - 2.0)
        graph = (
            f"[1:a]{narr}[narr];"
            f"[2:a]volume={gain:.2f}dB,aresample=48000,"
            f"atrim=duration={duration:.6f},asetpts=N/SR/TB,"
            f"afade=t=in:st=0:d=1,"
            f"afade=t=out:st={fade_out_start:.6f}:d=2[music];"
            f"[music][narr]sidechaincompress=threshold=0.02:"
            f"ratio={duck_ratio:.3f}:attack=20:release=400[ducked];"
            "[narr][ducked]amix=inputs=2:duration=first:"
            "dropout_transition=0:normalize=0[aout]"
        )
        description = "narration polish plus user-supplied ducked music"
    command = inputs + [
        "-filter_complex", graph,
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.6f}",
        "-movflags", "+faststart",
        str(out_path),
    ]
    return command, description


def main() -> int:
    parser = common.build_arg_parser("Stage 4: render video.mp4.")
    parser.add_argument(
        "--force", action="store_true",
        help="Rebuild every image clip even when the checkpoint cache is valid.",
    )
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

    grade = grade_filter(ctx.config)
    if grade:
        log.info("Grade ON: %s", grade)
    else:
        log.info("Grade OFF (grade.enabled is false) — no colour filters applied.")

    captions_on = bool(ctx.config.get("captions", {}).get("enabled", False))
    if captions_on:
        # Fail early if captions are on but the font is missing/relative.
        font = common.get_env("FONT_PATH", required=True)
        if not Path(font).is_absolute() or not Path(font).exists():
            common.fail(log, f"captions.enabled but FONT_PATH invalid: {font!r}")
        log.info("Captions ON, font=%s", font)

    pace_variation = float(vid.get("pace_variation", 0.0))
    segments = build_timeline(chunks, fps, pace_variation=pace_variation)
    total_frames = sum(s["frames"] for s in segments)
    log.info("Timeline: %d image(s) -> %d frames (%.1fs @ %dfps).",
             len(segments), total_frames, total_frames / fps, fps)
    if pace_variation:
        shot_seconds = [s["frames"] / fps for s in segments]
        log.info(
            "Pacing variation %.0f%% (deterministic); shot range %.2f..%.2fs.",
            pace_variation * 100, min(shot_seconds), max(shot_seconds),
        )

    clips_dir = ctx.work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = render_fingerprint(ctx.config, chunks)
    prepare_clip_cache(clips_dir, fingerprint, args.force, log)

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

        overlay = overlay_for_segment(seg, ctx.config)
        overlay_file = None
        if overlay is not None:
            overlay_file = clips_dir / f"overlay_{i:04d}.txt"
            overlay_file.write_text(str(overlay["display"]), encoding="utf-8")

        direction = "in" if i % 2 == 0 else "out"
        if not args.force and clip_is_valid(clip_path, int(seg["frames"]), fps, log):
            log.info(
                "clip %04d/%d: valid checkpoint (%d frames) — skipping",
                i + 1, len(segments), seg["frames"],
            )
            clip_paths.append(clip_path)
            continue
        log.info("clip %04d/%d: %s (%d frames, zoom-%s)",
                 i + 1, len(segments), Path(seg["image"]).name, seg["frames"], direction)
        render_clip(
            seg, clip_path, ctx.config, direction,
            caption_file, overlay_file, overlay, log,
        )
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
    command, audio_description = build_mux_command(
        ffmpeg, silent_video, full_audio, out_path,
        total_frames / fps, ctx.config,
    )
    log.info("Muxing final video (-c:v copy); audio: %s.", audio_description)
    common.run(command, logger=log)

    dur = common.media_duration(out_path, logger=log)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    log.info("Rendered %s — %.1fs, %.1f MB.", out_path, dur, size_mb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
