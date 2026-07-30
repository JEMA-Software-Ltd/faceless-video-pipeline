#!/usr/bin/env python3
"""Create deterministic 9:16 excerpts from an existing pipeline run.

The stage is entirely offline: it reuses chunk WAVs and downloaded stills,
selects promising self-contained chunks using transparent heuristics, and
burns captions timed by their character offsets in the original narration.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import common
from figures import choose_significant_figure, extract_figures
from render import grade_filter, write_concat_list, zoompan_filter


_CAVEAT_RE = re.compile(
    r"\b(source|sources|according to|alleged|allegedly|reportedly|"
    r"educational content|financial advice|where allegations|caveat)\b",
    re.IGNORECASE,
)
_SUBORDINATE_RE = re.compile(
    r"\b(which|that|although|because|while|whereas|despite|unless|however)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\S+")


def score_chunk(chunk: dict, total_chunks: int) -> tuple[float, list[str]]:
    text = str(chunk.get("text", ""))
    idx = int(chunk["index"])
    reasons: list[str] = []
    score = 0.0

    figure = choose_significant_figure(text)
    is_striking = (
        figure is not None
        and (
            figure.unit in {"GBP", "USD", "EUR", "percent"}
            or abs(figure.value) >= 100
        )
    )
    if is_striking:
        figure_points = min(48.0, figure.significance * 0.52)
        score += figure_points
        reasons.append(f"figure {figure.display} (+{figure_points:.1f})")

    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    avg_words = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
    subordinate = len(_SUBORDINATE_RE.findall(text))
    concise_points = max(0.0, 18.0 - max(0.0, avg_words - 12.0) * 0.7 - subordinate * 2.5)
    score += concise_points
    if concise_points >= 8:
        reasons.append(f"concise/declarative (+{concise_points:.1f})")

    if idx == 0:
        score += 32.0
        reasons.append("opening hook (+32.0)")
    elif idx <= 2:
        opening_points = 18.0 - idx * 3.0
        score += opening_points
        reasons.append(f"near opening (+{opening_points:.1f})")

    turn = (total_chunks - 1) * 0.60
    turn_distance = abs(idx - turn)
    if turn_distance <= 4:
        turn_points = 24.0 * (1.0 - turn_distance / 5.0)
        score += turn_points
        reasons.append(f"near 60% argument turn (+{turn_points:.1f})")

    caveats = len(_CAVEAT_RE.findall(text))
    if caveats:
        penalty = 22.0 + 8.0 * (caveats - 1)
        score -= penalty
        reasons.append(f"source/caveat language (-{penalty:.1f})")

    # Very long chunks tend to need context even when still within the cap.
    word_count = len(text.split())
    if word_count > 85:
        penalty = min(12.0, (word_count - 85) * 0.35)
        score -= penalty
        reasons.append(f"long setup (-{penalty:.1f})")

    return score, reasons


def select_chunks(chunks: list[dict], cfg: dict) -> list[dict]:
    minimum = float(cfg.get("min_seconds", 15))
    maximum = float(cfg.get("max_seconds", 55))
    count = int(cfg.get("count", 6))
    forced = [int(x) for x in (cfg.get("force_chunks") or [])]
    by_index = {int(c["index"]): c for c in chunks}

    eligible = [
        c for c in chunks
        if minimum <= float(c.get("duration", 0)) <= maximum
    ]
    eligible_indices = {int(c["index"]) for c in eligible}
    invalid_forced = [idx for idx in forced if idx not in eligible_indices]
    if invalid_forced:
        raise RuntimeError(
            "Forced clip chunk(s) are missing or outside the configured duration "
            f"window: {invalid_forced}"
        )

    ranked: list[dict] = []
    for chunk in eligible:
        score, reasons = score_chunk(chunk, len(chunks))
        ranked.append({**chunk, "_clip_score": score, "_clip_reasons": reasons})
    ranked.sort(key=lambda c: (-c["_clip_score"], int(c["index"])))

    selected: list[dict] = []
    for idx in forced:
        item = next(c for c in ranked if int(c["index"]) == idx)
        selected.append(item)

    # Greedy spread. A gap of 3 keeps selected ideas apart without replacing a
    # strong hook with a much weaker one merely to maximise distance.
    for min_gap in (3, 2):
        for candidate in ranked:
            idx = int(candidate["index"])
            if any(int(c["index"]) == idx for c in selected):
                continue
            if all(abs(idx - int(c["index"])) >= min_gap for c in selected):
                selected.append(candidate)
                if len(selected) >= count:
                    break
        if len(selected) >= count:
            break

    if len(selected) < count:
        raise RuntimeError(
            f"Only {len(selected)} spread-out chunks fit {minimum:g}..{maximum:g}s; "
            f"cannot create requested {count} clips."
        )
    return sorted(selected[:count], key=lambda c: int(c["index"]))


def caption_cues(text: str, duration: float, words_per_cue: int = 6) -> list[dict]:
    """Caption groups timed directly from source character offsets.

    Groups do not cross sentence boundaries or split an explicit number phrase.
    """
    cues: list[dict] = []
    text_len = max(len(text), 1)
    figures = extract_figures(text)
    sentences = list(re.finditer(r"[^.!?]+(?:[.!?]+|$)", text))
    for sentence in sentences:
        matches = list(_WORD_RE.finditer(text, sentence.start(), sentence.end()))
        pos = 0
        while pos < len(matches):
            end_pos = min(len(matches), pos + words_per_cue)
            # Avoid leaving a tiny orphan cue at the end of a sentence.
            if 0 < len(matches) - end_pos <= 3:
                end_pos = len(matches)
            if end_pos < len(matches):
                boundary = matches[end_pos].start()
                containing = next(
                    (figure for figure in figures
                     if figure.start < boundary < figure.end),
                    None,
                )
                if containing is not None:
                    while (
                        end_pos < len(matches)
                        and matches[end_pos].start() < containing.end
                    ):
                        end_pos += 1

            group = matches[pos:end_pos]
            start_char = group[0].start()
            end_char = group[-1].end()
            start = duration * start_char / text_len
            end = duration * end_char / text_len
            end = min(duration, max(end, start + 0.65))
            cue_text = " ".join(m.group() for m in group)
            cues.append({"start": start, "end": end, "text": cue_text})
            pos = end_pos
    if cues:
        cues[-1]["end"] = duration
    return cues


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def write_segment_ass(
    path: Path,
    cues: list[dict],
    segment_start: float,
    segment_end: float,
) -> None:
    events: list[str] = []
    for cue in cues:
        overlap_start = max(float(cue["start"]), segment_start)
        overlap_end = min(float(cue["end"]), segment_end)
        if overlap_end <= overlap_start:
            continue
        local_start = overlap_start - segment_start
        local_end = overlap_end - segment_start
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(local_start)},{_ass_time(local_end)},Caption,,0,0,0,,"
            f"{_ass_escape(str(cue['text']))}"
        )
    body = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,Arial,78,&H00F7F4EE,&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,6,2,5,85,85,100,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    path.write_text(body + "\n".join(events) + "\n", encoding="utf-8")


def _filter_path(path: Path) -> str:
    value = path.resolve().as_posix().replace(":", r"\:")
    return value.replace("'", r"\'")


def _chunk_segments(chunk: dict, fps: int) -> list[dict]:
    images = chunk.get("images") or []
    duration = float(chunk["duration"])
    total_frames = round(duration * fps)
    frame_cursor = 0
    segments: list[dict] = []
    for j, image in enumerate(images):
        end_frame = round(total_frames * (j + 1) / len(images))
        frames = max(1, end_frame - frame_cursor)
        segments.append({
            "image": image,
            "frames": frames,
            "start": frame_cursor / fps,
            "end": end_frame / fps,
        })
        frame_cursor = end_frame
    return segments


def _valid_existing(path: Path, expected_duration: float, fps: int) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        actual = common.media_duration(path)
    except (RuntimeError, ValueError):
        return False
    return abs(actual - expected_duration) <= max(0.16, 2.0 / fps)


def render_vertical_clip(
    chunk: dict,
    output: Path,
    work_dir: Path,
    cfg: dict,
    scratch: Path,
    force: bool,
    log,
) -> dict:
    fps = int(cfg.get("video", {}).get("fps", 25))
    duration = float(chunk["duration"])
    if not force and _valid_existing(output, duration, fps):
        log.info("%s already valid (%.2fs) — skipping", output.name, duration)
        return {"skipped": True}

    ffmpeg = common.require_binary("ffmpeg")
    preset = str(cfg.get("video", {}).get("preset", "medium"))
    crf = str(cfg.get("video", {}).get("crf", 20))
    grade = grade_filter(cfg)
    segments = _chunk_segments(chunk, fps)
    cues = caption_cues(str(chunk["text"]), duration)
    segment_paths: list[Path] = []

    for j, segment in enumerate(segments):
        image_path = (work_dir / segment["image"]).resolve()
        if not image_path.exists():
            raise RuntimeError(f"Missing clip source image: {image_path}")
        ass_path = scratch / f"chunk_{int(chunk['index']):03d}_{j:02d}.ass"
        write_segment_ass(ass_path, cues, segment["start"], segment["end"])
        segment_path = scratch / f"chunk_{int(chunk['index']):03d}_{j:02d}.mp4"

        vf = zoompan_filter(segment["frames"], "in" if j % 2 == 0 else "out",
                            1080, 1920, fps)
        if grade:
            vf += "," + grade
        vf += ",setsar=1,format=yuv420p"
        fonts_dir = _filter_path(Path(r"C:\Windows\Fonts"))
        vf += (
            f",subtitles=filename='{_filter_path(ass_path)}':"
            f"fontsdir='{fonts_dir}'"
        )
        log.info(
            "vertical chunk %03d image %d/%d: %s (%d frames)",
            int(chunk["index"]), j + 1, len(segments), image_path.name,
            int(segment["frames"]),
        )
        common.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-i", str(image_path),
            "-frames:v", str(segment["frames"]),
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p",
            "-g", str(fps), "-keyint_min", str(fps),
            "-an", str(segment_path),
        ], logger=log)
        segment_paths.append(segment_path)

    concat_file = scratch / f"chunk_{int(chunk['index']):03d}_list.txt"
    write_concat_list(segment_paths, concat_file)
    silent = scratch / f"chunk_{int(chunk['index']):03d}_silent.mp4"
    common.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", str(silent),
    ], logger=log)

    audio = (work_dir / chunk["audio_file"]).resolve()
    if not audio.exists():
        raise RuntimeError(f"Missing clip source audio: {audio}")
    common.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(silent), "-i", str(audio),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(output),
    ], logger=log)
    return {
        "skipped": False,
        "duration": common.media_duration(output),
        "size_bytes": output.stat().st_size,
    }


def crop_metrics(chunk: dict, work_dir: Path) -> list[dict]:
    try:
        from PIL import Image
    except ImportError:
        return []
    rows: list[dict] = []
    for relative in chunk.get("images") or []:
        path = work_dir / relative
        with Image.open(path) as image:
            width, height = image.size
        retained = min(1.0, (height * 9.0 / 16.0) / width)
        rows.append({
            "image": str(relative),
            "source_width": width,
            "source_height": height,
            "horizontal_fraction_retained": round(retained, 4),
        })
    return rows


def main() -> int:
    parser = common.build_arg_parser("Create standalone vertical excerpts.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild selected clips even when valid outputs exist.")
    args = parser.parse_args()
    ctx = common.StageContext(args, "clips")
    log = ctx.log
    ctx.ensure_dirs()

    clips_cfg = ctx.config.get("clips") or {}
    if not clips_cfg.get("enabled", False):
        log.info("Vertical clips are disabled (clips.enabled=false); nothing to do.")
        return 0

    data = common.read_chunks(ctx.work_dir)
    chunks = data.get("chunks") or []
    if not chunks:
        common.fail(log, "No chunks in chunks.json — run the earlier stages first.")
    selected = select_chunks(chunks, clips_cfg)

    output_dir = ctx.work_dir / "clips_vertical"
    scratch = output_dir / "_segments"
    output_dir.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for ordinal, chunk in enumerate(selected, 1):
        idx = int(chunk["index"])
        output = output_dir / f"clip_{ordinal:02d}_chunk_{idx:03d}.mp4"
        result = render_vertical_clip(
            chunk, output, ctx.work_dir, ctx.config, scratch, args.force, log,
        )
        figure = choose_significant_figure(str(chunk["text"]))
        records.append({
            "ordinal": ordinal,
            "chunk_index": idx,
            "duration": float(chunk["duration"]),
            "score": round(float(chunk["_clip_score"]), 3),
            "reasons": chunk["_clip_reasons"],
            "significant_figure": figure.display if figure else None,
            "text": chunk["text"],
            "images": list(chunk.get("images") or []),
            "crop_metrics": crop_metrics(chunk, ctx.work_dir),
            "output": str(output.relative_to(ctx.work_dir)),
            "render": result,
        })

    metadata = {
        "date": data.get("date", ctx.date),
        "selection_method": "deterministic figure/conciseness/structure scoring with spread",
        "duration_window": [
            float(clips_cfg.get("min_seconds", 15)),
            float(clips_cfg.get("max_seconds", 55)),
        ],
        "clips": records,
    }
    (output_dir / "selection.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Intermediate encodes have no value after the final copy-mux succeeds.
    if scratch.resolve().parent == output_dir.resolve():
        shutil.rmtree(scratch)
    log.info("Created %d vertical clips in %s", len(records), output_dir)
    for row in records:
        log.info(
            "selected chunk %03d (%.2fs, score %.1f): %s",
            row["chunk_index"], row["duration"], row["score"],
            "; ".join(row["reasons"]),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
