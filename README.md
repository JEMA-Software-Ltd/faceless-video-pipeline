# Faceless video pipeline

Takes a script **you wrote** and turns it into a narrated 1080p video — text-to-speech narration, stock photos with a slow Ken Burns pan, a colour grade, automated quality checks — then optionally uploads it to YouTube as a private, scheduled video. It is six small Python programs chained together by one command.

**It does not write scripts.** There is no content generation anywhere in this tool. You write the words; the pipeline reads them aloud and finds pictures. If you are looking for something that invents videos for you, this is not it.

You can see the end product at [youtube.com/@Clara-Studios](https://www.youtube.com/@Clara-Studios) — UK finance stories and documentaries. Everything on that channel was rendered with this.

### A few notes from actually using it

- **We do not upload via the API.** We take the finished file out of `work/` and upload it by hand, so there is a full human check before anything reaches the channel. `upload.py` exists and works, but we would recommend the manual route for anything you care about.
- **Edge is the default TTS** because we think it sounds better than the local option. Read [Choosing a TTS backend](#choosing-a-tts-backend) before you commit to it — there is a licensing catch.
- **This was built with Claude Code.** Bear in mind an AI wrote most of it and can make mistakes, particularly around security. Read [Security notes](#security-notes) and satisfy yourself before you point it at credentials that matter.
- **Renders are long.** Handing the terminal commands to an AI agent and coming back later works well, and is how most of these videos got made.

---

## ⚠️ Please read this before you use it

YouTube's [inauthentic content policy](https://support.google.com/youtube/answer/16354902) targets mass-produced and repetitive material. Templated slideshows over synthetic narration, published at volume with no editorial work, are close to the centre of what that policy describes. Channels doing it risk demonetisation or removal.

This tool removes the *mechanical* work — recording, sourcing images, timing cuts, encoding — from publishing a video you actually researched and wrote. It does not remove the requirement that the video be worth watching. **Pointing it at generated filler and running it on a schedule is a good way to lose a channel.** That is not a use I can stop you from attempting, but it is not what this is for and it will not end well.

---

## What it is not

- **Not a script writer.** You write every word.
- **Not a spam tool.** See above.
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
| 1 | `script.py` | Reads your markdown. The `# Heading` becomes the title, HTML comments become description and tags, and each blank-line-separated paragraph becomes a **chunk**. Asks Gemini for a few stock-photo search terms per chunk. |
| 2 | `tts.py` | Narrates each chunk to `audio/000.wav`, `001.wav`… Appends a configurable pause, then measures the real duration off disk and records it. Every later stage times itself from that measurement. |
| 3 | `images.py` | Works out how many images each chunk needs (one per 8–12 seconds of its measured audio), searches Pexels then Pixabay, downloads them. |
| 4 | `render.py` | Renders each still to its own short clip with a slow zoom, applies the colour grade, then joins the clips and muxes the narration. The video is encoded exactly once. |
| 5 | `qc.py` | Checks audio and video lengths match, no long black segments, no dead single-colour frames, plausible bitrate. Exits non-zero on failure, which stops the pipeline before upload. |
| 6 | `upload.py` | Uploads via the YouTube Data API. **Always private**, with a scheduled publish time. |

Plus `doctor.py` (checks your setup before you waste an hour) and `get_token.py` (one-off YouTube sign-in).

---

## Prerequisites

Install these first. Links go to the official downloads.

| What | Why | Where |
|---|---|---|
| **Python 3.11 or newer** | runs everything | [python.org/downloads](https://www.python.org/downloads/) |
| **ffmpeg** (includes ffprobe) | all video and audio work | [ffmpeg.org/download.html](https://ffmpeg.org/download.html) |
| **git** | to download this repo | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **espeak-ng** | *only* if you use the Kokoro TTS backend | [github.com/espeak-ng/espeak-ng/releases](https://github.com/espeak-ng/espeak-ng/releases) |

You do not need espeak-ng if you use the default `edge` TTS backend.

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

Open **Terminal** (Applications → Utilities → Terminal).

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

These are not hypothetical. Every one of them cost time during development, and they will happen to you too.

**"Python was not found" and the Microsoft Store opens.**
Windows ships a fake `python.exe` that does nothing but advertise the Store. If typing `python` opens the Store, that stub is shadowing your real install. Fix it: Start menu → search "Manage app execution aliases" → turn **off** both `python.exe` and `python3.exe`. Then open a new terminal.

**PATH does not refresh in an open terminal.**
After installing Python, ffmpeg or git, the terminal you installed from still has the *old* PATH. `ffmpeg -version` will keep saying "not recognised" no matter how many times you retry. **Close the window entirely and open a new one.** This is not optional and re-running the installer will not help.

**`source .venv/bin/activate` does not work on Windows.**
That is the macOS/Linux form. On Windows use:
```powershell
.venv\Scripts\activate
```
If PowerShell refuses with an execution-policy error, run:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Every stage suddenly says "PyYAML not installed".**
Almost always means the virtual environment is not active in that terminal — check your prompt for the `(.venv)` prefix. A new terminal window does not inherit it; you must activate again each time.

**`./run.sh` does not run in PowerShell.**
`run.sh` is a bash script. PowerShell cannot execute it. Either run the six stages individually as `python script.py …` (see [Running it](#running-it)), or use **Git Bash**, which Git for Windows installs — right-click in the project folder → "Git Bash Here" → then `./run.sh` works. Note that typing `bash run.sh` in PowerShell may invoke WSL instead, which will not have your dependencies.

**`ZoneInfoNotFoundError: 'Europe/London'`.**
Windows has no system timezone database. `upload.py` needs one to schedule the publish time, so without it the pipeline dies **at the very last step, after a full render has already completed**. The `tzdata` package supplies it and is pinned in `requirements.txt` — if you see this error, you skipped `pip install -r requirements.txt` or are running the wrong Python.

**`UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'`.**
Windows consoles default to the legacy cp1252 encoding, which cannot print the ✓/✗ in the QC report or the em-dashes in log messages. This used to kill `qc.py` mid-report. It is fixed in `common.py`, which forces UTF-8 on the output streams at import — but if you see it in your own additions, that is the cause.

**`PermissionError: [WinError 32]` when render starts.**
A leftover file handle on a clip from a previous run, usually because a render was killed part-way. Close any player or Explorer preview showing the file, or reboot. Do not kill renders mid-run if you can avoid it.

**Renders look like they have hung.** They have not. See [What to expect](#what-to-expect).

---

## API keys

Copy the template, then fill it in:

```bash
cp .env.example .env      # Windows PowerShell: copy .env.example .env
```

`.env` is gitignored and must never be committed. Every key below is documented in `.env.example` too.

### Pexels — stock images (free)
Sign up at [pexels.com/api](https://www.pexels.com/api/), create a key, paste into `PEXELS_API_KEY`. Generous limits (25,000 requests/hour), so ordinary use will not hit them.

### Pixabay — stock images fallback (free)
Sign up at [pixabay.com/api/docs](https://pixabay.com/api/docs/), paste into `PIXABAY_API_KEY`. Used only when Pexels returns too few results.

### Google AI Studio — Gemini, for image keywords (free tier)
Get a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), paste into `GEMINI_API_KEY`. The pipeline makes **one** batched call per video, so free-tier limits are ample.

> Check which models still carry a free tier before you set `gemini.model` — the free/paid split has moved more than once, and a model name that is merely *visible* to your key is not necessarily one you can call. A `429` with `limit: 0`, or a `403 PERMISSION_DENIED`, means that model is not free on your project. Flash and Flash-Lite tier models are the usual safe choice; keyword extraction is trivial work and does not need anything larger.

### YouTube — OAuth, for uploading

This is the fiddly one. In order:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable the **YouTube Data API v3** for it ([direct link](https://console.cloud.google.com/apis/library/youtube.googleapis.com)).
3. Configure the **OAuth consent screen**. Choose **External**.
4. **Publish the consent screen.** Do not leave it in "Testing".
   > ⚠️ **This matters more than anything else on this page.** While the consent screen is in Testing, only accounts on the test-user list can authorise at all — everyone else gets `Error 403: access_denied` — and Google expires refresh tokens after **7 days**. Your uploads will work for a week and then start failing with `invalid_grant`, and the cause is not obvious. Click **Publish App** on the consent screen. You do not need Google verification for your own personal use with the `youtube.upload` scope; you will see an "unverified app" warning at sign-in, and clicking through it is expected.
5. Credentials → **Create Credentials** → **OAuth client ID** → application type **Desktop app**. Copy the client ID and secret into `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.
   > You can also download `client_secret.json`, but this pipeline does not read it. It is gitignored specifically so it cannot end up in a commit.
6. Run the one-off sign-in. It opens a browser, you approve, and it prints a refresh token:
   ```bash
   python get_token.py
   ```
   Paste the printed value into `YOUTUBE_REFRESH_TOKEN`. You only do this once.

> ⚠️ **The Google account you sign in with must already have a YouTube channel.** If it does not, the upload fails with HTTP 401 `youtubeSignupRequired` — after the entire video has rendered. The OAuth is fine in that case; there is simply no channel to upload to. Go to [youtube.com](https://www.youtube.com/), sign in, and create the channel first. If your channel is a **Brand Account**, pick it explicitly at the account-chooser step of `get_token.py` — choosing the personal account instead produces the same error.

### Fonts — only for burned-in captions
Captions are off by default. If you turn them on (`captions.enabled: true`), set `FONT_PATH` to an **absolute** path to a `.ttf`/`.otf`. ffmpeg's text filter rejects relative paths. On Windows, use forward slashes: `C:/Windows/Fonts/arial.ttf`.

---

## Security notes

Most of what this tool touches is a credential. Worth knowing what each one can do.

**What lives on your machine, and what it grants**

| File | Contains | If leaked |
|---|---|---|
| `.env` | all API keys plus the YouTube refresh token | the refresh token allows uploading to your channel until you revoke it |
| `client_secret.json` | OAuth client ID and secret | identifies your app; not sufficient alone to reach your channel, but should not be public |
| `token.json` | cached access token | short-lived, but treat as sensitive |

All three are gitignored. So is `work/`, `logs/`, and everything in `scripts/` except `example.md`.

**Scope is deliberately minimal.** `get_token.py` requests only `youtube.upload`. It cannot read your videos, your analytics, your comments or your account details, and it cannot delete anything. If you extend it, keep the scope as narrow as the job allows.

**Uploads are always private.** `upload.py` sets `privacyStatus: private` with a scheduled `publishAt`. There is no code path that publishes immediately. Nothing reaches an audience without you or the schedule allowing it.

**Rotate anything you paste anywhere.** Refresh tokens and API keys get pasted into chat windows, logs and screenshots more often than anyone admits. Revoke a YouTube token at [myaccount.google.com/permissions](https://myaccount.google.com/permissions); Pexels, Pixabay and Gemini keys can be regenerated from their dashboards. It costs two minutes.

**`.gitignore` protects the future, not the past.** If a secret was ever *committed*, rewriting history does not unpublish it — GitHub serves unreachable objects by SHA indefinitely, forks keep their own copies, and caches are beyond your reach. Rotation is the only real fix. Note also that merely *staging* a file leaves a loose object in `.git/objects`; it is never pushed, but `git reflog expire --expire-unreachable=now --all && git gc --prune=now` will clear it locally.

**Everything here runs on your machine.** No telemetry, no analytics, no phone-home. The only outbound calls are to Pexels, Pixabay, Google and — if you use the `edge` backend — Microsoft.

---

## Choosing a TTS backend

`tts.backend` in `config.yaml` selects one of two. Both produce one wav per chunk with identical timing behaviour; only the voice differs.

| | `edge` (default) | `kokoro` |
|---|---|---|
| Runs | network call to Microsoft | locally on your machine |
| Needs | nothing extra | espeak-ng + ~82 MB model download |
| Speed | fast | slower (local inference) |
| Voice | `tts.edge_voice`, e.g. `en-GB-RyanNeural` | `tts.voice`, e.g. `bm_daniel` |
| Rate | `tts.edge_rate`, e.g. `"-8%"` | `tts.speed`, e.g. `0.9` |
| Works offline | no | yes |

**The trade-off, stated plainly.** `edge` is faster, needs no local model and sounds good. But it drives the undocumented endpoint behind Microsoft Edge's "Read Aloud" feature. That is not a published API. It has no version guarantee, no support, no uptime commitment, and **it is not licensed for commercial use** — Microsoft's terms cover it as a browser feature, not as a service you may build a product on. If you are monetising the output, or you need this to keep working unattended, use `kokoro`, which runs locally under a permissive licence with none of those questions.

`kokoro` is kept fully working for exactly this reason. Switching is one line in `config.yaml`.

If a chunk fails on `edge` after its retries, the stage **stops** rather than falling back to Kokoro — swapping synthesiser mid-run would change the narrator's voice partway through the finished video. Re-run `tts.py` to resume; completed chunks are skipped.

`python doctor.py` checks both backends every run, and verifies `tts.edge_voice` against the live voice list.

---

## Running it

### 1. Check your setup first

```bash
python doctor.py
```

It verifies ffmpeg, ffprobe, espeak-ng, every Python package, every `.env` key, both TTS backends, and that `work/` is writable. Output looks like this:

```
Pipeline preflight (doctor.py)
============================================================
  [✓] PASS  ffmpeg          C:\...\ffmpeg.exe
  [✓] PASS  edge-tts        edge-tts ok; voice 'en-GB-RyanNeural' present (322 voices live)
  [✓] PASS  .env PEXELS_API_KEY   set (56 chars)
  ...
```

**Fix every ✗ before going further.** A failure here costs you seconds; the same failure during a real run costs you an hour of rendering.

### 2. Write a script

Save it as `scripts/YYYY-MM-DD.md`. See [`scripts/example.md`](scripts/example.md) for the format:

- the first `# Heading` becomes the video title
- `<!-- description: ... -->` becomes the YouTube description
- `<!-- tags: a, b, c -->` becomes the tags
- every blank-line-separated paragraph becomes one narration chunk

Your scripts stay on your machine — everything in `scripts/` except `example.md` is gitignored.

**Write for the ear, not the page.** The narration is synthesised once and never edited, so anything ambiguous on the page becomes ambiguous in the audio. In practice that means: spell numbers and money out in words rather than using digits or symbols, avoid brackets and slashes (they vanish audibly and the sentence collapses), keep sentences under about 25 words, and make each paragraph self-contained — the pipeline splits on paragraphs, so one that trails into the next will sound broken.

### 3. Do a dry run

```bash
./run.sh --dry-run --skip-upload
```

This builds a ~30 second video from the first few chunks and exercises the entire chain in about two minutes. **Do this before trusting a full render.**

> ⚠️ A dry run **overwrites** `chunks.json` and `video.mp4` for that date with the shortened version. Never dry-run against a date whose full render you want to keep. A full render started against dry-run state will silently produce a 30-second video that passes QC, because QC compares the video against its own audio and both are the truncated version.

### 4. Do the real run

```bash
./run.sh --date 2026-07-28
```

`run.sh` stops at the first failing stage, so a QC failure halts before upload. Everything is logged to `logs/run-<date>.log`, including the Studio URL on success — search the log for `UPLOAD OK`.

Useful variants:

```bash
./run.sh --skip-upload      # render and QC, but do not upload
./run.sh --verbose          # debug logging
```

### Running without run.sh (or on PowerShell)

`run.sh` is only a convenience wrapper. The six stages are just programs, run in order:

```bash
python script.py  --date 2026-07-28
python tts.py     --date 2026-07-28
python images.py  --date 2026-07-28
python render.py  --date 2026-07-28
python qc.py      --date 2026-07-28
python upload.py  --date 2026-07-28
```

### Re-running a single stage

Any stage can be re-run on its own with the same flags. This is how you recover from a failure without redoing everything:

```bash
python render.py --date 2026-07-28
python qc.py     --date 2026-07-28
```

**Stages skip work that is already done.** `tts.py` skips chunks that already have audio and a measured duration; `images.py` skips chunks that already have enough images on disk. To force them to redo it:

```bash
python tts.py    --date 2026-07-28 --force
python images.py --date 2026-07-28 --force
```

> ⚠️ **`script.py` is the exception.** It rewrites `chunks.json` from scratch, discarding measured durations and image paths. Re-running the whole pipeline after a late failure therefore redoes all the narration and re-downloads every image. If only render/qc/upload failed, run those three directly instead.

> If you change TTS backend or voice, re-run `tts.py --force`. Without it, existing wavs are kept and you get a video that changes narrator part-way through.

### Upload behaviour

Uploads are **always** private, with the publish time set to the next occurrence of `upload.publish_time` in `upload.timezone`. Nothing is ever public the moment it uploads. If that time has already passed today, it rolls to tomorrow.

---

## What to expect

Rough numbers from real runs on an ordinary desktop CPU, no GPU.

| | 13-minute video | 18-minute video |
|---|---|---|
| Script length (words) | ~1,900 | ~2,700 |
| Chunks (paragraphs) | ~45 | ~67 |
| Images downloaded | ~85 | ~117 |
| Narration (TTS) | ~4 min (edge) | ~7 min (edge) |
| Image downloads | ~4 min | ~7 min |
| **Encoding** | **~40 min** | **~65 min** |
| Output file | ~450 MB | ~590 MB |

**Sizing a script.** Measured on `en-GB-RyanNeural` at `rate: "-8%"` with a 0.5s inter-chunk pause: **149 words per minute of speech, 145 effective**. So multiply your target minutes by about 145 to get the word count you need. Kokoro at `speed: 0.9` runs considerably faster — closer to 215 wpm — so the same script produces a much shorter video. Measure your own once and use that.

**Encoding dominates, and it is slow by design.** Each still is upscaled hugely before the zoom is applied — that is what stops the pan juddering — so budget **25–30 seconds per clip**. A progress bar that has not moved for half a minute is normal, not a hang. Encoding scales linearly with length, so a 50-minute video is a 3–4 hour render; start those when the machine is otherwise free.

**Cost.** Effectively zero. Pexels, Pixabay and the YouTube API are free at this volume, and one batched Gemini call per video sits inside the free tier. The real costs are your electricity and your time. `edge` TTS is free but see the licensing note above; `kokoro` is free and local.

**Disk.** Each run keeps its clips, images, audio and video under `work/<date>/`. Budget 1–2 GB per video and clear out old dates yourself.

---

## Configuration

Everything tunable is in [`config.yaml`](config.yaml), commented inline:

- **video** — resolution, frame rate, x264 quality, Ken Burns pacing
- **tts** — backend, voice, rate, and the pause inserted after each chunk
- **gemini** — model and keywords per chunk
- **images** — results per query, orientation, minimum size, and `query_suffix` (appended to every search to bias the whole video toward one look)
- **grade** — colour grade: saturation/contrast/gamma, shadow tint, vignette. Set `enabled: false` to skip it entirely
- **captions** — burned-in subtitles, off by default
- **upload** — privacy, publish time, timezone, category, tags, default description
- **qc** — thresholds for each check
- **dry_run** — how short the validation video is

Two settings do more than their size suggests. `images.query_suffix` and the `grade` block together are most of what makes disparate stock photography look like one film — a consistent search bias plus a consistent colour grade will do more for the finished look than better keywords will.

Secrets never go in `config.yaml`. They live in `.env`.

---

## Known limitations

Stated honestly, because they will affect you.

**Gemini keyword extraction needs a working API key.** Stage 1 makes one batched call to turn your paragraphs into stock-photo search terms. If the key is missing, out of quota, or the project is blocked, that call fails and the pipeline continues on the fallback below.

**The heuristic fallback is crude.** It maps trigger words in your narration onto a fixed table of photographable phrases ("regulator" → "government office building"), topping up from a small rotating pool of generic imagery. It is hand-written, English-only, and biased toward the finance and current-affairs vocabulary it was built against. **On other subject matter it will drift to the generic pool and your imagery will be repetitive and only loosely related.** It exists so an API outage degrades to a watchable video instead of failing the run — it is not a substitute for the real thing. If you work in a different subject area, extend the table in `script.py`; the repetition problem gets worse with length, so check repeat counts as well as fall-through rate on anything over about 40 chunks.

**It assumes British English and UK stock imagery.** The default voices are British, the publish schedule is `Europe/London`, image searches lean UK, and the keyword table is full of British institutions. Configurable, but you will be editing more than one line.

**Only tested on Windows.** Developed and exercised on Windows 11. The code is plain Python and ffmpeg with nothing knowingly platform-specific, and the macOS/Linux instructions above are correct as far as they go — but they have not been run end to end. Treat non-Windows as unverified, and please open an issue if you hit something.

**Image relevance is bounded by stock search.** Even with perfect keywords you are getting generic stock photography, and it looks like generic stock photography. For anything argument-led, expect to make your own chart cards and drop them in.

**No resume within a stage.** `render.py` clears and rebuilds every clip on each run, so a render interrupted at clip 60 of 70 restarts from clip 1. On a long video that is hours. This is the most worthwhile thing anyone could contribute.

**`edge` TTS depends on an undocumented endpoint** that is not licensed for commercial use. See [Choosing a TTS backend](#choosing-a-tts-backend).

**Only official APIs are used** — Pexels and Pixabay search endpoints, no scraping. All HTTP calls retry with exponential backoff and honour `Retry-After`.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). If you are looking for somewhere useful to start: render checkpointing, a keyword table for a non-finance subject area, or simply running the macOS or Linux instructions end to end and reporting what broke.

## Licence

MIT — see [LICENSE](LICENSE).

Stock imagery comes from Pexels and Pixabay under their respective licences. You are responsible for the scripts you write and the videos you publish.
