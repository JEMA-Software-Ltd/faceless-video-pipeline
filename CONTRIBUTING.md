# Contributing

Thanks for taking a look. This is a small, single-purpose project, so the bar is
"does it work and is it honest about what it doesn't do" rather than anything
ceremonial.

## Before you open an issue

Run `python doctor.py` and include its output. It checks ffmpeg, the Python
packages, both TTS backends and every `.env` key, and it catches most setup
problems on the spot.

Also include:

- your OS and Python version
- which TTS backend (`tts.backend`)
- the relevant chunk of `logs/run-<date>.log`

Please don't paste `.env` contents, `client_secret.json`, `token.json`, or an
access or refresh token into an issue. If a log line contains a key, redact it.

## Especially wanted

**macOS and Linux reports.** The pipeline has only been exercised end to end on
Windows. Nothing in it is knowingly platform-specific and the install steps in
the README should be correct, but "should be" is doing real work in that
sentence. If you run it on another OS, an issue saying either "this worked" or
"this broke here" is genuinely useful.

**Keyword table entries.** The offline fallback in `script.py` maps words found
in your narration onto stock-photo phrases. It is heavily biased toward
finance and current-affairs vocabulary because that is what it was built
against. If you work in another subject area, a PR extending `_TOPIC_IMAGERY`
with your domain's terms is welcome. Two rules: map to something a stock library
will actually return good landscape photos for, and skip words so common they'd
fire in every paragraph — those just create a new kind of repetition.

## Pull requests

- One change per PR.
- Match the surrounding style. The code is plain, comments explain *why* rather
  than restating the line, and stage scripts are independently runnable.
- If you change timing behaviour anywhere in `tts.py`, `images.py` or
  `render.py`, say how you verified the audio and video still line up.
  `qc.py`'s duration check is the quickest evidence.
- Run `python doctor.py` and a `./run.sh --dry-run --skip-upload` before
  submitting. The dry run exercises the whole chain in about two minutes.

## Things that will be declined

- **Script generation.** This tool deliberately does not write content. Keeping
  a human author in the loop is the point, not an omission — see the policy note
  at the top of the README.
- **Scraping image sites.** Only the official Pexels and Pixabay search APIs.
- **Removing the Kokoro backend.** It is the offline, commercially-usable
  alternative to `edge`, which depends on an undocumented endpoint. It stays.
- **Silently falling back between TTS backends mid-run.** That changes the
  narrator's voice partway through a finished video. Failing loudly is correct.

## Security

If you find something that could leak a credential, please open an issue
describing the mechanism without including any real key.

## Licence

Contributions are accepted under the MIT licence in [LICENSE](LICENSE).
