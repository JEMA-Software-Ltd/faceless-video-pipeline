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

import asyncio
import sys
import tempfile
from pathlib import Path

import common


# --- edge backend tuning ---------------------------------------------------
# edge-tts talks to an undocumented Microsoft endpoint. It is not a local model:
# it can time out, rate-limit, or return a truncated stream. These bound how
# hard we try before failing the stage outright.
EDGE_ATTEMPTS = 4
EDGE_TIMEOUT_S = 120.0
EDGE_BACKOFF_BASE = 2.0
# A truncation guard. No English speech exceeds ~25 characters/second; 45 is a
# generous ceiling, so audio shorter than len(text)/45 seconds means the stream
# was cut short even though it "succeeded". Writing that wav would silently
# desync every downstream image cue, so treat it as a failure and retry.
EDGE_MAX_CHARS_PER_SECOND = 45.0


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
    # The generator yields one item per internal segment. Depending on the
    # kokoro version that item is either a (graphemes, phonemes, audio) tuple
    # or a Result dataclass with an `.audio` attribute — handle both.
    for result in pipeline(text, voice=voice, speed=speed):
        audio = getattr(result, "audio", None)
        if audio is None:
            audio = result[-1]
        segments.append(_to_numpy(audio))
    if not segments:
        raise RuntimeError("Kokoro returned no audio for a chunk (empty text?).")
    return np.concatenate(segments) if len(segments) > 1 else segments[0]


async def _edge_stream_mp3(text: str, voice: str, rate: str, pitch: str) -> bytes:
    """Collect one chunk's audio from the edge endpoint as mp3 bytes."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buf = bytearray()

    async def pump():
        async for item in communicate.stream():
            if item["type"] == "audio" and item.get("data"):
                buf.extend(item["data"])

    await asyncio.wait_for(pump(), timeout=EDGE_TIMEOUT_S)
    return bytes(buf)


def _mp3_to_array(mp3_bytes: bytes, sample_rate: int, log):
    """Decode edge's mp3 to a mono float32 array AT sample_rate.

    The resample happens here, before anything is written or measured, so the
    wav on disk is already at the configured rate and the duration ffprobe
    reports back is the duration render.py will actually see.
    """
    import numpy as np
    import soundfile as sf
    ffmpeg = common.require_binary("ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        mp3_path = Path(td) / "chunk.mp3"
        wav_path = Path(td) / "chunk.wav"
        mp3_path.write_bytes(mp3_bytes)
        common.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(mp3_path),
             "-ar", str(sample_rate), "-ac", "1", str(wav_path)],
            logger=log,
        )
        data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if sr != sample_rate:
        raise RuntimeError(f"resample failed: got {sr}Hz, wanted {sample_rate}Hz")
    arr = np.asarray(data, dtype="float32").reshape(-1)
    if arr.size == 0:
        raise RuntimeError("decoded audio is empty")
    return arr


def synthesize_chunk_edge(text: str, voice: str, rate: str, pitch: str,
                          sample_rate: int, log):
    """One chunk via edge-tts, with retries. Raises on definitive failure.

    Deliberately does NOT fall back to Kokoro: swapping synthesiser mid-run
    would change the narrator's voice partway through the video, which is worse
    than stopping. The caller fails the stage instead.
    """
    last_exc = None
    for attempt in range(1, EDGE_ATTEMPTS + 1):
        try:
            mp3 = asyncio.run(_edge_stream_mp3(text, voice, rate, pitch))
            if not mp3:
                raise RuntimeError("endpoint returned no audio data")
            arr = _mp3_to_array(mp3, sample_rate, log)
            seconds = len(arr) / float(sample_rate)
            floor = len(text) / EDGE_MAX_CHARS_PER_SECOND
            if seconds < floor:
                raise RuntimeError(
                    f"audio truncated: {seconds:.2f}s for {len(text)} chars "
                    f"(expected at least {floor:.2f}s)")
            return arr
        except Exception as exc:                      # network / timeout / decode
            last_exc = exc
            if attempt < EDGE_ATTEMPTS:
                delay = EDGE_BACKOFF_BASE ** attempt
                log.warning("edge-tts attempt %d/%d failed (%s) — retrying in %.0fs",
                            attempt, EDGE_ATTEMPTS, exc, delay)
                import time
                time.sleep(delay)
    raise RuntimeError(f"edge-tts failed after {EDGE_ATTEMPTS} attempts: {last_exc}")


def pad_with_silence(audio, seconds: float, sample_rate: int):
    """Append `seconds` of digital silence to a chunk's audio.

    The padding is baked into the wav BEFORE it is written, so the duration
    tts.py measures back off disk already includes it. That is what keeps the
    render timeline honest — images.py and render.py both budget from the
    measured duration, so a pause that existed only in config (and not in the
    wav) would push the images out of sync with the narration.
    """
    import numpy as np
    if seconds <= 0:
        return audio
    silence = np.zeros(int(round(seconds * sample_rate)), dtype="float32")
    return np.concatenate([audio, silence])


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
    backend = str(tts_cfg.get("backend", "kokoro")).strip().lower()
    if backend not in ("kokoro", "edge"):
        common.fail(log, f"tts.backend must be 'kokoro' or 'edge', got {backend!r}")
    voice = tts_cfg.get("voice", "bf_emma")
    lang_code = tts_cfg.get("lang_code", "b")
    speed = float(tts_cfg.get("speed", 1.0))
    sample_rate = int(tts_cfg.get("sample_rate", 24000))
    edge_voice = tts_cfg.get("edge_voice", "en-GB-RyanNeural")
    edge_rate = str(tts_cfg.get("edge_rate", "+0%"))
    edge_pitch = str(tts_cfg.get("edge_pitch", "+0Hz"))

    if backend == "edge":
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            common.fail(log, "tts.backend is 'edge' but edge-tts is not installed. "
                             "pip install -r requirements.txt")
        log.info("Backend: edge-tts (voice=%s rate=%s pitch=%s -> %dHz wav)",
                 edge_voice, edge_rate, edge_pitch, sample_rate)
    else:
        log.info("Backend: kokoro (voice=%s lang=%s speed=%.2f)",
                 voice, lang_code, speed)
    pause_after = float(tts_cfg.get("pause_after_chunk", 0.0))
    if pause_after < 0:
        common.fail(log, f"tts.pause_after_chunk must be >= 0, got {pause_after}")
    if pause_after:
        log.info("Appending %.2fs of silence to each chunk (counted in its duration).",
                 pause_after)

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

        if backend == "edge":
            log.info("chunk %03d: synthesising %d chars with edge voice %s...",
                     idx, len(chunk["text"]), edge_voice)
            try:
                audio = synthesize_chunk_edge(chunk["text"], edge_voice, edge_rate,
                                              edge_pitch, sample_rate, log)
            except Exception as exc:
                # Hard stop. Falling back to Kokoro here would change the
                # narrator's voice partway through the finished video.
                common.fail(log, f"chunk {idx:03d}: {exc}\n"
                                 f"Stopping rather than switching backend mid-video. "
                                 f"Re-run tts.py to resume — completed chunks are skipped.")
        else:
            if pipeline is None:
                pipeline = _load_pipeline(lang_code, log)
            log.info("chunk %03d: synthesising %d chars with voice %s...",
                     idx, len(chunk["text"]), voice)
            audio = synthesize_chunk(pipeline, chunk["text"], voice, speed)

        audio = pad_with_silence(audio, pause_after, sample_rate)
        sf.write(str(wav_path), audio, sample_rate)

        # "Real measured duration" — read it back from the written file so it
        # matches exactly what render/qc will see. Because the pause is already
        # in the wav, this figure includes it.
        duration = common.media_duration(wav_path, stream="audio", logger=log)
        chunk["audio_file"] = rel
        chunk["duration"] = round(duration, 3)
        done += 1
        log.info("chunk %03d: wrote %s (%.2fs incl. %.2fs pause)",
                 idx, rel, duration, pause_after)

        # Persist after each chunk so a crash never loses completed work.
        common.write_chunks(ctx.work_dir, data)

    total = sum(c.get("duration", 0.0) for c in chunks)
    common.write_chunks(ctx.work_dir, data)
    log.info("TTS done: %d synthesised, %d skipped. Total narration %.1fs (%.1f min).",
             done, len(chunks) - done, total, total / 60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
