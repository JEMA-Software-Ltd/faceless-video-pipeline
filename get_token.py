#!/usr/bin/env python3
"""
get_token.py — one-off helper to obtain a YouTube refresh token.

Run this ONCE during setup, after putting YOUTUBE_CLIENT_ID and
YOUTUBE_CLIENT_SECRET in .env:

    python get_token.py

It opens a browser for Google's consent screen, then prints a refresh token.
Paste that into YOUTUBE_REFRESH_TOKEN in .env and you never need to run this
again — upload.py exchanges the refresh token for access tokens on its own.

The Google account you consent with must already have a YouTube channel, or
uploads later fail with 401 "youtubeSignupRequired".

Requests the youtube.upload scope only: this can add videos to your channel,
but cannot read, modify, or delete anything already there.
"""

import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()
flow = InstalledAppFlow.from_client_config(
    {"installed": {
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}},
    scopes=["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0)
print("REFRESH TOKEN:", creds.refresh_token)