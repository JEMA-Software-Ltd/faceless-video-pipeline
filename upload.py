#!/usr/bin/env python3
"""
upload.py — stage 6 of 6. Uploads video.mp4 via the YouTube Data API v3.

Policy (non-negotiable):
  * Always privacyStatus=private with status.publishAt set to the next
    configured wall-clock time (default 17:00 Europe/London). The video NEVER
    goes public immediately — it stays private until YouTube publishes it at
    publishAt.
  * Category 22 (People & Blogs), tags from config.
  * On success, print the YouTube Studio URL.

Auth: OAuth refresh-token flow. Client id/secret + a pre-obtained refresh token
come from .env; access tokens are cached to token.json (gitignored) and
refreshed automatically.

    python3 upload.py --date 2026-07-26
    python3 upload.py --dry-run   # validates creds + prints the request, no upload

See the README for the one-off consent step that produces YOUTUBE_REFRESH_TOKEN.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import common


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
TOKEN_FILE = common.REPO_ROOT / "token.json"


def get_credentials(log):
    """Build valid OAuth credentials, preferring the cached token.json and
    falling back to the refresh token in .env. Always writes the (refreshed)
    token back to token.json."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    client_id = common.get_env("YOUTUBE_CLIENT_ID")
    client_secret = common.get_env("YOUTUBE_CLIENT_SECRET")
    refresh_token = common.get_env("YOUTUBE_REFRESH_TOKEN")

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as exc:
            log.warning("Ignoring unreadable token.json (%s); rebuilding from .env", exc)
            creds = None

    if creds is None:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

    if not creds.valid:
        log.info("Refreshing access token...")
        creds.refresh(Request())

    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def next_publish_at(cfg: dict, log) -> str:
    """RFC3339 UTC timestamp for the next occurrence of the configured local
    time. If that time already passed today, roll to tomorrow — guaranteeing a
    future publishAt (YouTube rejects past times)."""
    up = cfg.get("upload", {})
    tz = ZoneInfo(up.get("timezone", "Europe/London"))
    hh, mm = (int(x) for x in str(up.get("publish_time", "17:00")).split(":"))

    now = datetime.now(tz)
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    utc = candidate.astimezone(timezone.utc)
    iso = utc.isoformat().replace("+00:00", "Z")
    log.info("publishAt = %s (%s local %s)", iso, candidate.isoformat(), tz.key)
    return iso


def build_body(data: dict, cfg: dict, publish_at: str) -> dict:
    up = cfg.get("upload", {})
    title = (data.get("title") or "Personal finance").strip()[:100]  # YT title cap
    description = (data.get("description") or up.get("default_description", "")).strip()[:5000]

    # Config tags first, then any per-script tags, de-duplicated in order.
    tags: list[str] = []
    for t in list(up.get("tags", [])) + list(data.get("tags", [])):
        t = str(t).strip()
        if t and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(up.get("category_id", 22)),
        },
        "status": {
            "privacyStatus": "private",          # always private...
            "publishAt": publish_at,             # ...until this time.
            "selfDeclaredMadeForKids": bool(up.get("made_for_kids", False)),
        },
    }


def resumable_upload(youtube, body: dict, video_path: Path, log) -> dict:
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    attempt = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log.info("  uploading... %d%%", int(status.progress() * 100))
        except HttpError as exc:
            # Retry transient server-side errors with backoff; re-raise the rest.
            if getattr(exc, "resp", None) is not None and exc.resp.status in (500, 502, 503, 504, 429):
                attempt += 1
                if attempt > 5:
                    raise
                delay = 2 ** attempt
                log.warning("Upload chunk error %s — retry %d in %ds", exc.resp.status, attempt, delay)
                time.sleep(delay)
                continue
            raise
    return response


def main() -> int:
    parser = common.build_arg_parser("Stage 6: upload to YouTube (private + publishAt).")
    args = parser.parse_args()
    ctx = common.StageContext(args, "upload")
    log = ctx.log

    video = ctx.work_dir / "video.mp4"
    if not video.exists():
        common.fail(log, f"{video} not found — run render.py (and qc.py) first.")

    data = common.read_chunks(ctx.work_dir)
    publish_at = next_publish_at(ctx.config, log)
    body = build_body(data, ctx.config, publish_at)

    log.info("Title:       %s", body["snippet"]["title"])
    log.info("Tags:        %s", ", ".join(body["snippet"]["tags"]))
    log.info("Category:    %s | privacy: private | publishAt: %s",
             body["snippet"]["categoryId"], publish_at)

    if ctx.dry_run:
        # Validate credentials (proves the whole auth chain works) but do NOT
        # upload — a dry-run must never put a test video on the channel.
        log.info("DRY-RUN: validating YouTube credentials (no upload will happen)...")
        try:
            get_credentials(log)
            log.info("DRY-RUN: credentials OK. Would upload %s (%.1f MB).",
                     video, video.stat().st_size / (1024 * 1024))
        except Exception as exc:
            common.fail(log, f"DRY-RUN: credential check failed: {exc}")
        return 0

    creds = get_credentials(log)
    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    log.info("Uploading %s (%.1f MB)...", video, video.stat().st_size / (1024 * 1024))
    response = resumable_upload(youtube, body, video, log)

    video_id = response.get("id")
    if not video_id:
        common.fail(log, f"Upload returned no video id: {response}")

    # Record the result alongside the run for traceability.
    data["youtube"] = {"video_id": video_id, "publish_at": publish_at,
                       "privacy": "private"}
    common.write_chunks(ctx.work_dir, data)

    studio_url = f"https://studio.youtube.com/video/{video_id}/edit"
    watch_url = f"https://youtu.be/{video_id}"
    print("\n" + "=" * 60)
    print("UPLOAD OK — video is PRIVATE, scheduled to publish at " + publish_at)
    print(f"  Studio: {studio_url}")
    print(f"  Watch:  {watch_url}  (private until publishAt)")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
