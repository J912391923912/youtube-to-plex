# YouTube to Plex

Download YouTube videos straight into your Plex library. You get the sharpest picture the video has, up to 4K, with the best audio track, saved as MKV with a poster. It also suggests new videos from the channels and topics you download.

Windows only. It runs on your own PC in its own window.

## Get started

1. Download `YouTube-to-Plex.exe` from [the latest release](../../releases/latest).
2. Double-click it. It opens in its own window and adds itself to the Start menu.
3. Set **Saves to** to a folder your Plex server reads, paste a YouTube link and press the red button.

On first start it downloads the two tools it needs, ffmpeg (joins picture and sound, about 86 MB) and Deno (lets YouTube give full quality, about 40 MB), from their official GitHub releases into `%LOCALAPPDATA%\YouTube to Plex\bin`. If you already have ffmpeg, Deno or Node.js installed, it uses yours.

Close the window to quit. If something is still downloading, it asks first.

### "Unknown publisher" and "Windows protected your PC"

Windows shows these for any program that is not code-signed, and signing needs a paid certificate tied to a verified identity. The app is safe to run: the code is all here. Choose **More info**, then **Run anyway**. You only need to do this once. A copy you build yourself (see below) gets no warning.

## Set up Plex

1. Add a library of type **Other Videos** that points to the Saves to folder.
2. In the library's advanced settings, pick **Plex Personal Media** as the agent and turn on **Scan my library automatically**.

Videos are saved as `Channel/2026-11-07 Title [id].mkv`, with a `.jpg` poster next to each one and the title, date, description and chapters inside the file.

## Quality

- Picture: the highest resolution the video has, up to 2160p, 60 fps when offered. VP9 is picked over AV1 because more Plex clients play it without transcoding.
- Sound: the best audio track YouTube offers, usually Opus at about 130 to 160 kbps.
- A video that only exists below 720p is saved at its best.
- A video you already downloaded is skipped.

## Sign in to YouTube

**Sign in to YouTube** opens a separate Chrome or Edge window. You sign in to Google there yourself. The app never sees your password. It saves the YouTube cookies to `%LOCALAPPDATA%\YouTube to Plex\cookies.txt` on your PC and closes the window. **Sign out** deletes them again.

Signed in, you can download age-restricted videos, members-only videos from channels you are a member of, and private videos shared with you. Movies and shows bought on YouTube are copy-protected and cannot be downloaded.

YouTube can flag accounts that download a lot, so a spare Google account is the safer choice.

## Suggestions

Suggestions come from the newest uploads of the channels you download from most, plus a YouTube search built from the tags of your recent downloads. Videos you already have, Shorts and live streams are left out.

## When downloads start failing

YouTube changes often, and the downloader inside this app ([yt-dlp](https://github.com/yt-dlp/yt-dlp)) keeps up. Download the newest release. If you run from source, update with `python -m pip install -U "yt-dlp[default]"`.

Your history and the list of downloaded videos are in `%LOCALAPPDATA%\YouTube to Plex`.

## Run from source

```
python -m pip install "yt-dlp[default]" pyinstaller
python app.py
python test_app.py
python -m PyInstaller YouTube-to-Plex.spec
```

The last line builds `dist/YouTube-to-Plex.exe`.

## Fair use

Only download videos you have the right to keep, and respect the creators and YouTube's terms. The exe bundles Python, yt-dlp and their libraries, each under its own license. This project's own code is MIT licensed.
