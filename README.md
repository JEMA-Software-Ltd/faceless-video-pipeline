# Clara — faceless YouTube pipeline

Automated, cron-friendly pipeline that turns a hand-written markdown script into
a 15-minute narrated slideshow video and uploads it to YouTube as **private,
scheduled to publish** (never public immediately). Built for educational
finance/budgeting content aimed at a UK audience.

You write the scripts. The pipeline does everything else: narration (Kokoro
TTS), stock imagery (Pexels/Pixabay), Ken Burns render (ffmpeg), automated QC,
and upload (YouTube Data API v3).

## How it works

Six independent stages, each a standalone script, each reading/writing
`work/<YYYY-MM-DD>/`. State is passed between them via `chunks.json` in that
directory, so **any stage can be re-run on its own** without re-running the
others. `run.sh` just chains them in order and halts on the first failure.

| # | Stage | Reads | Writes |
|---|-------|-------|--------|
| 1 | `script.py` | `scripts/<date>.md` | `chunks.json` (paragraph chunks + Gemini keywords) |
| 2 | `tts.py`    | `chunks.json` | `audio/NNN.wav` + measured `duration` per chunk |
| 3 | `images.py` | `chunks.json` | `images/` (one image per 8-12s of narration) |
| 4 | `render.py` | audio + images | `video.mp4` (per-image zoompan clips → concat → mux) |
| 5 | `qc.py`     | `video.mp4` | pass/fail (exits non-zero on failure) |
| 6 | `upload.py` | `video.mp4` | private YouTube video, scheduled `publishAt` |

Plus `doctor.py` (preflight checks) and `config.yaml` (all tunables).

---

## Setup — exact order for a fresh machine

### 1. System dependencies

- **Python 3.11+**
- **ffmpeg** (provides `ffmpeg` and `ffprobe` on PATH)
- **espeak-ng** (Kokoro uses it for phonemisation)

```bash
# Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg espeak-ng
# macOS (Homebrew)
brew install ffmpeg espeak-ng
```

### 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> The first `tts.py` run downloads the ~82M Kokoro model from Hugging Face.
> `kokoro` pulls in `torch`; on a GPU box install the CUDA torch wheel yourself
> first if you want GPU acceleration (CPU works fine for this workload).

### 3. Secrets

```bash
cp .env.example .env
```

Fill in `.env` (see **Getting the API keys** below). Set `FONT_PATH` only if you
plan to burn in captions (off by default).

### 4. Config

Review `config.yaml`. Sensible defaults are set (British voice `bf_emma`,
1920x1080@25fps, publish 17:00 Europe/London, category 22). Nothing here is a
secret.

### 5. Run the doctor

```bash
python3 doctor.py
```

Fix every ✗ before going further. It checks ffmpeg/ffprobe/espeak-ng, the Python
packages, every `.env` key, Hugging Face reachability, that `work/` is writable,
and (if captions are on) the font file.

### 6. Dry-run the whole chain

```bash
./run.sh --dry-run
```

This builds a **~30-second** video from the first 3 chunks (not the full 15
minutes) and validates the entire chain — including credentials — in under two
minutes. **Do this before you ever trust a full encode.** In dry-run, `upload.py`
validates your YouTube credentials but does **not** upload anything.

### 7. Real run

Write today's script, then:

```bash
./run.sh
```

---

## Getting the API keys

All four services are free to start.

- **Gemini** (`GEMINI_API_KEY`): https://aistudio.google.com/apikey
- **Pexels** (`PEXELS_API_KEY`): https://www.pexels.com/api/
- **Pixabay** (`PIXABAY_API_KEY`): https://pixabay.com/api/docs/

### YouTube refresh token (one-off)

1. In [Google Cloud Console](https://console.cloud.google.com/): create a
   project, **enable the "YouTube Data API v3"**, and configure the OAuth
   consent screen (External; add your own Google account as a test user).
2. Create an **OAuth client ID** of type **Desktop app**. Note the client ID and
   client secret → put them in `.env` as `YOUTUBE_CLIENT_ID` /
   `YOUTUBE_CLIENT_SECRET`.
3. Run this one-off snippet (needs `google-auth-oauthlib`, already installed) to
   complete consent in a browser and print your refresh token:

   ```python
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_config(
       {"installed": {
           "client_id": "YOUR_CLIENT_ID",
           "client_secret": "YOUR_CLIENT_SECRET",
           "auth_uri": "https://accounts.google.com/o/oauth2/auth",
           "token_uri": "https://oauth2.googleapis.com/token",
           "redirect_uris": ["http://localhost"]}},
       scopes=["https://www.googleapis.com/auth/youtube.upload"])
   creds = flow.run_local_server(port=0)
   print("REFRESH TOKEN:", creds.refresh_token)
   ```

4. Put the printed value in `.env` as `YOUTUBE_REFRESH_TOKEN`. `upload.py`
   exchanges it for access tokens automatically and caches them to `token.json`
   (gitignored). You only do this once.

---

## Daily usage

1. Write `scripts/YYYY-MM-DD.md` by hand (see `scripts/example.md` for the
   format). Structure:
   - First `# Heading` → the video title.
   - Optional `<!-- description: ... -->` and `<!-- tags: a, b -->` comments →
     upload metadata.
   - Blank-line-separated paragraphs → one narration chunk each.
2. `./run.sh` (or `./run.sh --dry-run` first if you changed anything).

### Cron (nightly)

```cron
# 02:15 every night — build today's video, logging to a dated file.
15 2 * * *  cd /path/to/Clara---Youtube && ./run.sh >> "work/cron-$(date +\%F).log" 2>&1
```

Make sure the venv's Python is used — either activate it in a wrapper script or
set `PYTHON=/path/to/.venv/bin/python3` in the environment (run.sh honours
`$PYTHON`). Because upload always sets `publishAt` to the next 17:00
Europe/London, a video built overnight goes live at 5pm the same day. Nothing is
ever published immediately.

### Re-running a single stage

Every stage takes `--date` and `--dry-run`. Example: re-fetch images for a day
without redoing TTS, then re-render:

```bash
python3 images.py --date 2026-07-26 --force
python3 render.py --date 2026-07-26
python3 qc.py     --date 2026-07-26
```

`--force` on `tts.py` / `images.py` regenerates work that already exists;
otherwise they skip completed chunks so a re-run only fills gaps. `./run.sh
--skip-upload` runs everything through QC but stops before upload.

---

## QC checks (`qc.py`)

Exits non-zero (halting `run.sh` before upload) if any of these fail:

- **Duration** — audio vs video differ by more than `audio_video_max_diff` (0.5s).
- **Black frames** — any black segment longer than `black_max_duration` (0.5s).
- **Dead frames** — any of 10 evenly-spaced sampled frames is >95% a single colour.
- **Filesize** — bitrate implausible for the duration (too small = truncated,
  too large = runaway).

All thresholds live under `qc:` in `config.yaml`.

---

## Configuration (`config.yaml`)

Key knobs: `video.seconds_per_image` and its min/max, `video.fps`/resolution,
`tts.voice`/`lang_code`, `gemini.model`, `upload.publish_time`/`timezone`/`tags`,
`captions.enabled` (+ styling), and the `dry_run` / `qc` blocks. Secrets never
go here — they live in `.env`.

Burned-in captions are **off by default**. To enable: set `captions.enabled:
true` and point `FONT_PATH` in `.env` at an **absolute** path to a `.ttf`/`.otf`
(drawtext requires an absolute path).

---

## Notes / caveats

- Only the official Pexels and Pixabay **search APIs** are used — no scraping.
- All HTTP calls retry with exponential backoff and honour `Retry-After`.
- A few external API response shapes (Gemini text field, Kokoro's per-segment
  return type) are marked `# NOTE (verify tomorrow)` in the code — they follow
  the documented shapes but are worth confirming against your installed versions.
- If Gemini is briefly unavailable during a nightly run, `script.py` logs a loud
  warning and falls back to crude heuristic keywords so the run still completes;
  image relevance will be lower for that video.
