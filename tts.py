#!/usr/bin/env python3
"""
tts.py — stage 2 of 6.

Synthesises each chunk's narration with Kokoro TTS to audio/NNN.wav, then writes
the REAL measured duration of each wav back into chunks.json. Downstream stages
(images, render) rely on those measured durations for timing.

    python3 tts.py --date 2026-07-26
    python3 tts.py --force        # re-synthesise even chunks already done

Re-runnable: a chunk is skipped if its wav already exists and it already has a
measured duration, so re-running after a partial crash only fills the gaps
(use --force to redo everything).

Kokoro downloads its ~82M model from Hugging Face on first use. The 'b' lang
code / bf_*/bm_* voices are British English, matching the UK audience.
"""

from __future__ import annotations

import sys
from pathlib import Path

import common


def _load_pipeline(lang_code: str, log):
    """Construct the Kokoro pipeline once. Imported lazily because it pulls in
    torch, which no other stage needs."""
    try:
        from kokoro import KPipeline
    except ImportError as exc:
        raise RuntimeError(
            "kokoro not installed. pip install -r requirements.txt "
            "(and ensure espeak-ng is on PATH)."
        ) from exc
    log.info("Loading Kokoro pipeline (lang_code=%r) — first run downloads the model...",
             lang_code)
    # NOTE (verify tomorrow): KPipeline(lang_code=...) is the documented ctor for
    # kokoro 0.9.x. It yields (graphemes, phonemes, audio) tuples per call.
    return KPipeline(lang_code=lang_code)


def _to_numpy(audio):
    """Kokoro yields either a torch tensor or a numpy array depending on version.
    Normalise to a 1-D float32 numpy array."""
    import numpy as np
    if hasattr(audio, "detach"):  # torch.Tensor
        audio = audio.detach().cpu().numpy()
    arr = np.asarray(audio, dtype="float32")
    return arr.reshape(-1)


def synthesize_chunk(pipeline, text: str, voice: str, speed: float):
    """Run Kokoro over one chunk's text and return a single concatenated
    float32 numpy array. Kokoro may internally split long text into several
    segments; we stitch them together in order."""
    import numpy as np
    segments = []
    # The generator yields one tuple per internal segment.
    for result in pipeline(text, voice=voice, speed=speed):
        # result is (graphemes, phonemes, audio); we only need the audio.
        audio = result[-1]
        segments.append(_to_numpy(audio))
    if not segments:
        raise RuntimeError("Kokoro returned no audio for a chunk (empty text?).")
    return np.concatenate(segments) if len(segments) > 1 else segments[0]


def main() -> int:
    parser = common.build_arg_parser("Stage 2: Kokoro TTS per chunk.")
    parser.add_argument("--force", action="store_true",
                        help="Re-synthesise chunks that already have audio.")
    args = parser.parse_args()
    ctx = common.StageContext(args, "tts")
    log = ctx.log
    ctx.ensure_dirs()

    try:
        import soundfile as sf
    except ImportError:
        common.fail(log, "soundfile not installed. pip install -r requirements.txt")

    data = common.read_chunks(ctx.work_dir)
    chunks = data.get("chunks", [])
    if not chunks:
        common.fail(log, "No chunks in chunks.json — run script.py first.")

    tts_cfg = ctx.config.get("tts", {})
    voice = tts_cfg.get("voice", "bf_emma")
    lang_code = tts_cfg.get("lang_code", "b")
    speed = float(tts_cfg.get("speed", 1.0))
    sample_rate = int(tts_cfg.get("sample_rate", 24000))

    audio_dir = ctx.work_dir / "audio"

    pipeline = None  # lazily created only if there's real work to do
    done = 0
    for chunk in chunks:
        idx = chunk["index"]
        wav_path = audio_dir / f"{idx:03d}.wav"
        rel = str(wav_path.relative_to(ctx.work_dir))

        if not args.force and wav_path.exists() and chunk.get("duration"):
            log.info("chunk %03d: audio exists (%.2fs) — skipping", idx, chunk["duration"])
            chunk["audio_file"] = rel
            continue

        if pipeline is None:
            pipeline = _load_pipeline(lang_code, log)

        log.info("chunk %03d: synthesising %d chars with voice %s...",
                 idx, len(chunk["text"]), voice)
        audio = synthesize_chunk(pipeline, chunk["text"], voice, speed)
        sf.write(str(wav_path), audio, sample_rate)

        # "Real measured duration" — read it back from the written file so it
        # matches exactly what render/qc will see.
        duration = common.media_duration(wav_path, stream="audio", logger=log)
        chunk["audio_file"] = rel
        chunk["duration"] = round(duration, 3)
        done += 1
        log.info("chunk %03d: wrote %s (%.2fs)", idx, rel, duration)

        # Persist after each chunk so a crash never loses completed work.
        common.write_chunks(ctx.work_dir, data)

    total = sum(c.get("duration", 0.0) for c in chunks)
    common.write_chunks(ctx.work_dir, data)
    log.info("TTS done: %d synthesised, %d skipped. Total narration %.1fs (%.1f min).",
             done, len(chunks) - done, total, total / 60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
