# Faceless video pipeline

Takes a script **you wrote** and turns it into a narrated 1080p video — text-to-speech narration, stock photos with a slow Ken Burns pan, a colour grade, automated quality checks — then optionally uploads it to YouTube as a private, scheduled video. It is six small Python programs chained together by one command.

**It does not write scripts.** There is no content generation anywhere in this tool. You write the words; the pipeline reads them aloud and finds pictures. If you are looking for something that invents videos for you, this is not it.

You can see the end product at [youtube.com/@Clara-Studios](https://www.youtube.com/@Clara-Studios) — UK finance stories and documentaries. Everything on that channel was rendered with this.

### A few notes from actually using it

- **We do not upload via the API.** We take the finished file out of `work/` and upload it by hand, so there is a full human check before anything reaches the channel. `upload.py` exists and works, but we would recommend the manual route for anything you care about.
- **ElevenLabs is the default TTS** and it is a paid service — roughly one credit per character, so about £2–4 per video. Two free backends remain available. Read [Choosing a TTS backend](#choosing-a-tts-backend) before committing either way, because the free ones carry a licensing catch.
- **This was built with AI assistance.** Bear in mind an AI wrote much of the implementation and can make mistakes, particularly around security. Read [Security notes](#security-notes) and satisfy yourself before pointing it at credentials that matter.
- **Renders are long.** Handing the terminal commands to an AI agent and coming back later works well, and is how most of these videos got made.

---

## ⚠️ Please read this before you use it

YouTube's [inauthentic content policy](https://support.google.com/youtube/answer/16354902) targets mass-produced and repetitive material. Templated slideshows over synthetic narration, published at volume with no editorial work, are close to the centre of what that policy describes. Channels doing it risk demonetisation or removal.

This tool removes the *mechanical* work — recording, sourcing images, timing cuts, encoding — from publishing a video you actually researched and wrote. It does not remove the requirement that the video be worth watching. **Pointing it at generated filler and running it on a schedule is a good way to lose a channel.** That is not a use I can stop you from attempting, but it is not what this is for and it will not end well.

---

## What it is not

- **Not a script writer.** You write every word.
- **Not a spam tool.** See above.
- **Not free by default.** The default TTS backend bills per character. Free alternatives are documented below.
- **Not neutral about accents.** Defaults are British English narration and UK-leaning stock imagery and publish times. All configurable, none neutral out of the box.
- **Not tested outside Windows.** It is plain Python and ffmpeg and should work anywhere, but only Windows has actually been exercised. See [Known limitations](#known-limitations).

---

## How it works

Six stages. Each is a standalone program you can run on its own. All state for one video lives in a single `chunks.json`, which is what lets any stage be re-run without redoing the others.

```
scripts/2026-07-28.md          ← you write this
        │
        ▼
 1. script.py    split into paragraphs ("chunks"), pick image search keywords
 2. tts.py       narrate each chunk → one .wav each, measure its real duration
 3. images.py    search Pexels/Pixabay per chunk → download stills
 4. render.py    Ken Burns pan + colour grade → encode each clip → join → add audio
 5. qc.py        check the result isn't broken; exit non-zero if it is
 6. upload.py    upload to YouTube as private, scheduled to publish later
        │
        ▼
 work/2026-07-28/video.mp4
```

| # | Stage | What it actually does |
|---|-------|----------------------|
| 1 | `script.py` | Reads your markdown. The `# Heading` becomes the title, HTML comments become description and tags, and each blank-line-separated paragraph becomes a **chunk**. Picks stock-photo search terms using a deterministic heuristic table, or a local model when enabled. |
| 2 | `tts.py` | Narrates each chunk to `audio/000.wav`, `001.wav`… Appends a configurable pause, then measures the real duration off disk and records it. Every later stage times itself from that measurement. |
| 3 | `images.py` | Works out how many images each chunk needs (one per 8–12 seconds of its measured audio), searches Pexels then Pixabay, downloads them. |
| 4 | `render.py` | Renders each still to a checkpointed clip with deterministic pace variation, applies the colour grade and optional number overlays, then joins the clips and polishes the narration at the final mux. |
| 5 | `qc.py` | Checks audio and video lengths match, no long black segments, no dead single-colour frames, plausible bitrate. Exits non-zero on failure, which stops the pipeline before upload. |
| 6 | `upload.py` | Uploads via the YouTube Data API. **Always private**, with a scheduled publish time. |

Two optional standalone stages operate on an existing work directory. `clips.py` produces six selected 1080×1920 vertical excerpts from audio and imagery already on disk. `charts.py` generates source-verified chart-card PNG candidates for review — deliberately *not* inserted into a render.

Plus `doctor.py` (checks your setup before you waste an hour) and `get_token.py` (one-off YouTube sign-in).

---

## Prerequisites

| What | Why | Where |
|---|---|---|
| **Python 3.11 or newer** | runs everything | [python.org/downloads](https://www.python.org/downloads/) |
| **ffmpeg** (includes ffprobe) | all video and audio work | [ffmpeg.org/download.html](https://ffmpeg.org/download.html) |
| **git** | to download this repo | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **espeak-ng** | *only* for the Kokoro TTS backend | [github.com/espeak-ng/espeak-ng/releases](https://github.com/espeak-ng/espeak-ng/releases) |
| **Ollama** | *only* for the experimental local-model keyword backend, disabled by default | [ollama.com/download](https://ollama.com/download) |

You need neither espeak-ng nor Ollama for a default run.

---

## Install

Pick your operating system. These are complete — you do not need to adapt anything.

### Windows (PowerShell)

Open **PowerShell** from the Start menu. Not Command Prompt.

```powershell
# 1. Install the tools. This uses winget, which ships with Windows 11.
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install Git.Git

# 2. CLOSE PowerShell COMPLETELY AND OPEN A NEW ONE.
#    PATH changes do not apply to already-open windows. This is the single
#    most common reason "python is not recognised" persists after installing.

# 3. Check the tools are visible. Each should print a version.
python --version
ffmpeg -version
git --version

# 4. Download and enter the project
git clone https://github.com/JamesEdwardMcGee/faceless-video-pipeline.git
cd faceless-video-pipeline

# 5. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

# 6. Install Python dependencies
pip install -r requirements.txt
```

You know step 5 worked when your prompt gains a `(.venv)` prefix:

```
(.venv) PS C:\Users\you\projects\faceless-video-pipeline>
```

### macOS (Terminal)

```bash
# 1. Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Install the tools
brew install python@3.12 ffmpeg git espeak-ng

# 3. Download and enter the project
git clone https://github.com/JamesEdwardMcGee/faceless-video-pipeline.git
cd faceless-video-pipeline

# 4. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 5. Install Python dependencies
pip install -r requirements.txt
```

### Linux (Debian / Ubuntu)

```bash
# 1. Install the tools
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg git espeak-ng

# 2. Download and enter the project
git clone https://github.com/JamesEdwardMcGee/faceless-video-pipeline.git
cd faceless-video-pipeline

# 3. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install Python dependencies
pip install -r requirements.txt
```

---

## Windows: problems this project actually hit

These are not hypothetical. Every one of them cost time during development.

**"Python was not found" and the Microsoft Store opens.**
Windows ships a fake `python.exe` that does nothing but advertise the Store. If typing `python` opens the Store, that stub is shadowing your real install. Fix it: Start menu → search "Manage app execution aliases" → turn **off** both `python.exe` and `python3.exe`. Then open a new terminal.

**PATH does not refresh in an open terminal.**
After installing Python, ffmpeg or git, the terminal you installed from still has the *old* PATH. `ffmpeg -version` will keep saying "not recognised" no matter how many times you retry. **Close the window entirely and open a new one.** Re-running the installer will not help.

**`source .venv/bin/activate` does not work on Windows.**
That is the macOS/Linux form. On Windows use:
```powershell
.venv\Scripts\activate
```
If PowerShell refuses with an execution-policy error:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Every stage suddenly says "PyYAML not installed".**
Almost always means the virtual environment is not active in that terminal — check your prompt for the `(.venv)` prefix. A new terminal window does not inherit it.

**`./run.sh` does not run in PowerShell.**
`run.sh` is a bash script. Either run the stages individually as `python script.py …`, or use **Git Bash**, which Git for Windows installs — right-click in the project folder → "Git Bash Here". Note that typing `bash run.sh` in PowerShell may invoke WSL instead, which will not have your dependencies.

**`ZoneInfoNotFoundError: 'Europe/London'`.**
Windows has no system timezone database. `upload.py` needs one to schedule the publish time, so without it the pipeline dies **at the very last step, after a full render has already completed**. The `tzdata` package supplies it and is pinned in `requirements.txt`.

**`UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'`.**
Windows consoles default to cp1252, which cannot print the ✓/✗ in the QC report. Fixed in `common.py`, which forces UTF-8 on the output streams at import.

**`PermissionError: [WinError 32]` when render starts.**
A leftover file handle on a clip from a previous run, usually because a render was killed part-way. Close any player or Explorer preview showing the file, or reboot.

**Renders look like they have hung.** They have not. See [What to expect](#what-to-expect).

---

## API keys

```bash
cp .env.example .env      # Windows PowerShell: copy .env.example .env
```

`.env` is gitignored and must never be committed.

### ElevenLabs — narration (paid, and the default)

Sign up at [elevenlabs.io](https://elevenlabs.io/), then **profile icon → API Keys → Create**. The key is shown once. Paste into `ELEVENLABS_API_KEY`.

A paid plan is required for commercial use — the free tier grants no commercial rights and requires attribution. Pick a voice from the Voice Library, add it to your own collection, and put its **voice ID** (not its name) into `tts.elevenlabs_voice_id` in `config.yaml`. Shared Voice Library voices must be added to your account before the API will resolve them.

Some community voices carry a credit multiplier set by whoever shared them, so a library voice can cost more per character than a built-in one. Check before committing to one.

### Pexels — stock images (free)
Sign up at [pexels.com/api](https://www.pexels.com/api/), paste into `PEXELS_API_KEY`. Generous limits (25,000 requests/hour).

### Pixabay — stock images fallback (free)
Sign up at [pixabay.com/api/docs](https://pixabay.com/api/docs/), paste into `PIXABAY_API_KEY`. Used only when Pexels returns too few results.

### YouTube — OAuth, for uploading

The fiddly one. In order:

1. Create a project in the [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **YouTube Data API v3** ([direct link](https://console.cloud.google.com/apis/library/youtube.googleapis.com)).
3. Configure the **OAuth consent screen**. Choose **External**.
4. **Publish the consent screen.** Do not leave it in "Testing".
   > ⚠️ **This matters more than anything else on this page.** In Testing, only accounts on the test-user list can authorise — everyone else gets `Error 403: access_denied` — and Google expires refresh tokens after **7 days**. Uploads work for a week, then fail with `invalid_grant`, and the cause is not obvious. Click **Publish App**. You do not need Google verification for personal use with the `youtube.upload` scope; the "unverified app" warning at sign-in is expected.
5. Credentials → **Create Credentials** → **OAuth client ID** → **Desktop app**. Copy the client ID and secret into `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.
   > You can also download `client_secret.json`, but this pipeline does not read it. It is gitignored so it cannot end up in a commit.
6. Run the one-off sign-in:
   ```bash
   python get_token.py
   ```
   Paste the printed value into `YOUTUBE_REFRESH_TOKEN`. Once only.

> ⚠️ **The Google account must already have a YouTube channel.** If it does not, upload fails with HTTP 401 `youtubeSignupRequired` — after the entire video has rendered. Create the channel at [youtube.com](https://www.youtube.com/) first. If your channel is a **Brand Account**, pick it explicitly at the account-chooser step of `get_token.py`; choosing the personal account produces the same error.

### Fonts — only for burned-in captions
Captions are off by default. If enabled, set `FONT_PATH` to an **absolute** path to a `.ttf`/`.otf`. ffmpeg's text filter rejects relative paths. On Windows use forward slashes: `C:/Windows/Fonts/arial.ttf`.

---

## Choosing a TTS backend

`tts.backend` selects one of three. All three write one wav per chunk, apply the same inter-chunk pause, and measure the real duration off disk, so timing behaviour is identical whichever you use.

| | `elevenlabs` (default) | `edge` | `kokoro` |
|---|---|---|---|
| Cost | ~1 credit per character | free | free |
| Runs | ElevenLabs REST API | undocumented Microsoft endpoint | locally on your machine |
| Needs | paid account + API key | nothing | espeak-ng + ~82 MB model |
| Commercial use | yes, on a paid plan | **not licensed** | yes, permissive licence |
| Voice setting | `tts.elevenlabs_voice_id` | `tts.edge_voice` | `tts.voice` |
| Speed control | not on `eleven_v3` | `tts.edge_rate` | `tts.speed` |
| Audio tags | yes, on `eleven_v3` | no | no |
| Works offline | no | no | yes |

**The trade-off, stated plainly.**

`elevenlabs` sounds materially better than the other two and is properly licensed for commercial use, but it bills per character.

`edge` is free and sounds good, but it drives the undocumented endpoint behind Microsoft Edge's "Read Aloud" feature. That is not a published API — no version guarantee, no support, no uptime commitment, and **it is not licensed for commercial use**. If you are monetising the output, that is a real liability rather than a technicality.

`kokoro` is free, local, offline and permissively licensed. It is the weakest of the three on quality and the only one that keeps working with no network.

**Model choice within ElevenLabs.** `eleven_v3` supports audio tags but does **not** accept a numeric `speed` parameter, so pacing has to be controlled through punctuation and tags. `eleven_multilingual_v2` accepts `speed` and is the more stable long-form option, but ignores audio tags. Pick based on which matters more to you.

Relevant config keys: `elevenlabs_voice_id`, `elevenlabs_model`, `elevenlabs_stability`, `elevenlabs_similarity`, `elevenlabs_output_format`.

**No mid-video fallback.** If a chunk fails after its retries, the stage stops rather than switching backend — swapping synthesiser part-way would change the narrator's voice inside the finished video. Re-run `tts.py` to resume; completed chunks are skipped and cost nothing.

---

## Audio tags

On `eleven_v3` you can put ElevenLabs audio tags in square brackets directly in your script to direct the delivery:

```markdown
That is the ending of this story.

[pause]

To understand why, you have to go back to a Saturday morning in August.
```

Tags reach the ElevenLabs API and nothing else. `script.py` stores two versions of every chunk:

- `chunk["tts_text"]` — raw, tags intact. Only the ElevenLabs backend reads it.
- `chunk["text"]` — stripped. Everything else uses this: keyword extraction, number overlays, vertical-clip captions, and the `edge` and `kokoro` backends.

Without that split, tags would pollute your image search terms and appear burned into your vertical clip captions.

**Use them sparingly.** ElevenLabs' own guidance is that heavy tag use causes instability, and for scripted narration most pacing is better handled with sentence length, ellipses and dashes. Tags bill as characters like anything else, though the overhead is trivial — a handful adds a few dozen characters to a whole video.

---

## Cost control

ElevenLabs bills when a generation **runs**, not when a file is downloaded, so a bug that re-synthesises is expensive rather than merely slow. Several guards exist.

**Price a script before committing to it:**

```bash
python tts.py --date 2026-08-10 --dry-run-cost
```

Reports the character count and estimated credits without calling the API at all.

**A hard ceiling.** `tts.max_characters_per_run` (default 40,000) stops the run if a single invocation would exceed it. Pass `--confirm-cost` to proceed deliberately.

**Completed chunks are never re-synthesised** unless you pass `--force`. A failed run resumes for free, and a second run of a finished date submits zero characters.

**A truncation guard.** Audio much shorter than the text implies means the stream was cut short despite a successful response. That chunk is retried rather than written.

Rate limits are honoured via `Retry-After`, and retries use exponential backoff.

---

## Running it

### 1. Check your setup first

```bash
python doctor.py
```

Verifies ffmpeg, ffprobe, espeak-ng, every Python package, every `.env` key, all TTS backends — including whether your ElevenLabs voice and model actually resolve — Ollama readiness, and that `work/` is writable.

```
Pipeline preflight (doctor.py)
============================================================
  [✓] PASS  ffmpeg                    C:\...\ffmpeg.exe
  [✓] PASS  .env ELEVENLABS_API_KEY   set (51 chars)
  [✓] PASS  ElevenLabs API            voice and model resolve
  [✓] PASS  .env PEXELS_API_KEY       set (56 chars)
  ...
```

**Fix every ✗ before going further.** A typo in a voice ID caught here costs seconds; caught during a run it costs an hour of rendering and a wasted synthesis.

### 2. Write a script

Save as `scripts/YYYY-MM-DD.md`. See [`scripts/example.md`](scripts/example.md):

- the first `# Heading` becomes the video title
- `<!-- description: ... -->` becomes the YouTube description
- `<!-- tags: a, b, c -->` becomes the tags
- every blank-line-separated paragraph becomes one narration chunk

Everything in `scripts/` except `example.md` is gitignored.

**Write for the ear, not the page.** The narration is synthesised once and never edited, so anything ambiguous on the page becomes ambiguous in the audio. Spell numbers and money out in words rather than using digits or symbols. Avoid brackets and slashes — they vanish audibly and the sentence collapses. Keep sentences under about 25 words. Make each paragraph self-contained, since the pipeline splits on paragraphs.

### 3. Do a dry run

```bash
./run.sh --dry-run --skip-upload
```

Builds a ~30 second video from the first few chunks and exercises the whole chain in about two minutes.

> ⚠️ A dry run **overwrites** `chunks.json` and `video.mp4` for that date with the shortened version. Never dry-run against a date whose full render you want to keep. A full render started against dry-run state silently produces a 30-second video that **passes QC**, because QC compares the video against its own audio and both are truncated.

### 4. Do the real run

```bash
./run.sh --date 2026-07-28
```

Stops at the first failing stage, so a QC failure halts before upload. Logged to `logs/run-<date>.log`, including the Studio URL on success — search for `UPLOAD OK`.

```bash
./run.sh --skip-upload      # render and QC, but do not upload
./run.sh --verbose          # debug logging
```

### Running without run.sh (or on PowerShell)

```bash
python script.py  --date 2026-07-28
python tts.py     --date 2026-07-28
python images.py  --date 2026-07-28
python render.py  --date 2026-07-28
python qc.py      --date 2026-07-28
python upload.py  --date 2026-07-28
```

Optional offline outputs, once the main assets exist. Set the corresponding `enabled` flag in `config.yaml` first:

```bash
python clips.py  --date 2026-07-28
python charts.py --date 2026-07-28
```

### Re-running a single stage

**Stages skip work already done.** `tts.py` skips chunks with audio and a measured duration; `images.py` skips chunks with enough images; `render.py` reuses non-empty clips whose duration and config fingerprint match. To force:

```bash
python tts.py    --date 2026-07-28 --force
python images.py --date 2026-07-28 --force
python render.py --date 2026-07-28 --force
```

> ⚠️ **`script.py` is the exception.** It rewrites `chunks.json` from scratch, discarding measured durations and image paths — so re-running the whole pipeline after a late failure redoes all narration, which now costs money. If only render, QC or upload failed, run those directly.

> **Changing TTS backend or voice requires `tts.py --force`.** Without it, existing wavs are kept and you get a video that changes narrator part-way through.

### Upload behaviour

Uploads are **always** private, with the publish time set to the next occurrence of `upload.publish_time` in `upload.timezone`. Nothing is ever public the moment it uploads. If that time has passed today, it rolls to tomorrow.

---

## What to expect

Rough numbers from real runs on an ordinary desktop CPU, no GPU.

| | shorter video | longer video |
|---|---|---|
| Script length (words) | ~1,900 | ~3,000 |
| Chunks (paragraphs) | ~70 | ~78 |
| Characters billed | ~9,300 | ~13,000 |
| Images downloaded | ~85–120 | ~150 |
| Narration (ElevenLabs) | a few minutes | a few minutes |
| **Encoding** | **~40 min** | **~65 min+** |
| Output file | ~450 MB | ~590 MB |

**Sizing a script — measure your own.** Pace varies substantially between backends, voices and models, by enough to throw a target length out by several minutes. A short sample is not a reliable guide: measure across a full render, dividing total words by narration seconds excluding inter-chunk pauses. As a historical data point, `edge` with `en-GB-RyanNeural` at `rate: "-8%"` measured about 145 effective words per minute, and Kokoro at `speed: 0.9` ran considerably quicker. Do not assume either applies to your setup.

**Encoding dominates, and it is slow by design.** Each still is upscaled hugely before the zoom is applied — that is what stops the pan juddering — so budget **25–30 seconds per clip**. A progress bar that has not moved for half a minute is normal. Encoding scales linearly, so a 50-minute video is a 3–4 hour render. Start those when the machine is otherwise free.

**Cost.** Not zero any more. Pexels, Pixabay and the YouTube API are free at this volume, and Kokoro and Ollama are local. ElevenLabs is the paid component at roughly one credit per character, so **£2–4 per video** depending on plan and length. Switch `tts.backend` to `kokoro` for a genuinely free pipeline, accepting the quality drop.

**Disk.** Each run keeps clips, images, audio and video under `work/<date>/`. Budget 1–2 GB per video and clear out old dates yourself.

---

## Configuration

Everything tunable is in [`config.yaml`](config.yaml), commented inline:

- **video** — resolution, frame rate, x264 quality, Ken Burns pacing, deterministic per-image pace variation
- **tts** — backend selection, per-backend voice and model settings, inter-chunk pause, cost ceiling
- **llm** — the experimental local-model keyword backend: enable/disable, Ollama host and model, timeout, context, retries
- **images** — results per query, orientation, minimum size, and `query_suffix` (appended to every search to bias the whole video toward one look)
- **grade** — colour grade: saturation, contrast, gamma, shadow tint, vignette. `enabled: false` skips it entirely
- **captions** — burned-in subtitles, off by default
- **audio** — narration normalisation, compression and EQ at the final mux, plus an optional user-supplied music path
- **overlays** — one late-biased spoken figure per chunk, parsed from the narration
- **clips** — vertical-clip count, duration window, optional forced chunk indices
- **charts** — scratch chart-card limit and minimum comparison ratio. Never inserts into a render
- **upload** — privacy, publish time, timezone, category, tags, default description
- **qc** — thresholds for each check
- **dry_run** — how short the validation video is

Two settings do more than their size suggests. `images.query_suffix` and the `grade` block together are most of what makes disparate stock photography look like one film — a consistent search bias plus a consistent colour grade beats better keywords.

Secrets never go in `config.yaml`. They live in `.env`.

---

## Known limitations

Stated honestly, because they will affect you.

**Image relevance is bounded by stock search.** Even with perfect keywords you are getting generic stock photography, and it looks like it. Stock libraries are also global, so a query for "cash" or "documents" will happily return imagery from the wrong country — bias `images.query_suffix` toward your region and audit the keyword list before a long render.

**The heuristic keyword fallback is crude.** It maps trigger words onto a fixed table of photographable phrases ("regulator" → "government office building"), topping up from a small rotating pool. Hand-written, English-only, and biased toward the finance and current-affairs vocabulary it was built against. On other subject matter it drifts to the generic pool and imagery becomes repetitive. Extend the table in `script.py` for your own domain, and check repeat counts as well as fall-through rate on anything over about 40 chunks.

**The local-model keyword backend is unfinished.** `llm.enabled` defaults to false and the path has never been validated against a live model. Leave it off unless you intend to do that work; Stage 1 then takes the deterministic heuristic path.

**It assumes British English and UK stock imagery.** Default voices are British, the publish schedule is `Europe/London`, image searches lean UK, and the keyword table is full of British institutions. Configurable, but you will be editing more than one line.

**Only tested on Windows.** Developed and exercised on Windows 11. The code is plain Python and ffmpeg with nothing knowingly platform-specific, and the macOS/Linux instructions are correct as far as they go — but they have not been run end to end. Treat non-Windows as unverified and open an issue if you hit something.

**Vertical crops are blunt.** `clips.py` centre-crops landscape stills to 9:16. That avoids reach-killing black bars, but retains only about a third of a 16:9 image's width and cannot know whether the subject was off-centre. Expect one or two of six to be badly composed.

**Automatic figures are deliberately conservative.** Overlays and charts parse explicit English number phrases only. Chart candidates additionally require clear comparison language, matching units, a large enough ratio, and verification that every figure appears in the source text. Ambiguous candidates are dropped, because a fabricated chart would look entirely convincing.

**Music is bring-your-own.** The final mux can loop, mix and side-chain a local track, but nothing is bundled, chosen, downloaded or licensed by this project. The empty default path leaves it off. If you add one, make sure it is cleared for commercial use.

**`edge` TTS depends on an undocumented endpoint** not licensed for commercial use. See [Choosing a TTS backend](#choosing-a-tts-backend).

**Only official APIs are used** — ElevenLabs, Pexels, Pixabay, YouTube, and Ollama's local API. No scraping. Remote calls retry with exponential backoff and honour `Retry-After`.

### Planned, not implemented

- Finish and live-test the local-model keyword backend.
- Evaluate stock video as an alternative to stills. Would substantially increase download volume and storage.
- Automated, licence-filtered music sourcing. The code accepts a local path; the project does not pick or download tracks.
- Saliency-aware vertical cropping, rather than centre-crop.

---

## Security notes

Most of what this tool touches is a credential.

| File | Contains | If leaked |
|---|---|---|
| `.env` | all API keys plus the YouTube refresh token | the refresh token allows uploading to your channel until revoked; the ElevenLabs key allows spending your credits |
| `client_secret.json` | OAuth client ID and secret | identifies your app; not sufficient alone to reach your channel, but should not be public |
| `token.json` | cached access token | short-lived, but treat as sensitive |

All three are gitignored, along with `work/`, `logs/`, and everything in `scripts/` except `example.md`.

**Scope is deliberately minimal.** `get_token.py` requests only `youtube.upload`. It cannot read your videos, analytics, comments or account details, and cannot delete anything. If you extend it, keep the scope as narrow as the job allows.

**Uploads are always private.** `upload.py` sets `privacyStatus: private` with a scheduled `publishAt`. There is no code path that publishes immediately.

**Rotate anything you paste anywhere.** Keys and tokens end up in chat windows, logs and screenshots more often than anyone admits. Revoke a YouTube token at [myaccount.google.com/permissions](https://myaccount.google.com/permissions); ElevenLabs, Pexels and Pixabay keys can be regenerated from their dashboards. Two minutes each.

**`.gitignore` protects the future, not the past.** If a secret was ever *committed*, rewriting history does not unpublish it — GitHub serves unreachable objects by SHA indefinitely, forks keep their own copies, and caches are beyond your reach. Rotation is the only real fix. Note also that merely *staging* a file leaves a loose object in `.git/objects`; it is never pushed, but `git reflog expire --expire-unreachable=now --all && git gc --prune=now` clears it locally.

**Everything runs on your machine.** No telemetry, no analytics, no phone-home. The only outbound calls are to ElevenLabs, Pexels, Pixabay, Google, and — on the `edge` backend — Microsoft.

---

## How this was built

Parts of this codebase were written with AI assistance, and some commits carry a `Co-Authored-By` trailer to that effect. **It is not an AI-generated project.** The design, the architecture, the decisions about what the tool should and shouldn't do, and every script ever run through it are human work. The AI contribution is implementation and iteration against that direction, reviewed and tested by a person before it landed.

Worth saying plainly because the distinction matters twice over. It matters for the code: nothing here is unreviewed generated output, and the limitations section above is an honest account of what has and hasn't been exercised rather than generated boilerplate. And it matters for what the tool produces — a pipeline that narrates and illustrates scripts *you* wrote would be a strange place to pretend authorship doesn't matter.

If you use this, the same applies to you. The pipeline handles the mechanical work. It doesn't make the video yours; writing it does.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Useful places to start:

- **Run the macOS or Linux instructions end to end** and report what broke. Nothing outside Windows has been verified.
- **A keyword table for a non-finance subject area.** The existing one is narrow and it shows.
- **Saliency-aware vertical cropping**, rather than centre-crop.
- **Finish the local-model keyword backend** and validate it against a real model.

## Licence

MIT — see [LICENSE](LICENSE).

Stock imagery comes from Pexels and Pixabay under their respective licences. Narration generated through a paid ElevenLabs plan is yours to use commercially; the free tier is not. You are responsible for the scripts you write and the videos you publish.
