# YouTube to Plex

Download YouTube videos straight into your Plex library. You get the sharpest picture the video has, up to 4K, with the best audio track, saved as MP4 with a poster and captions. It also suggests new videos from the channels and topics you download.

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
3. In Plex's agent settings for Personal Media, tick **Local Media Assets** and put it at the top. That is what lets Plex read the title, date and description stored inside each file.

Videos are saved as `Channel/2026-11-07 Title [id].mp4`, with a `.jpg` poster next to each one. Inside the file: the title, upload date, channel, description, chapters, captions, and a language tag on the audio and captions.

Plex works out 4K, HDR and the audio language from the file itself, so they show up without being in the file name.

## Quality

- Picture: the highest resolution the video has, up to 2160p (or the cap you pick for a playlist), 60 fps when offered. VP9 is picked over AV1 because more Plex clients play it without transcoding.
- Sound: the best audio track YouTube offers in the video's original language, usually Opus at about 130 to 160 kbps. AI-dubbed tracks are skipped.
- Captions: the video's own language plus English, embedded so Plex lists them as subtitles. Made-by-the-creator captions win over automatic ones.
- Saved as MP4, because Plex reads embedded titles and descriptions from MP4 but not from MKV.
- A video that only exists below 720p is saved at its best.
- A video you already downloaded is skipped. Paste its link and the app tells you that you have it.

## Playlists and channels

Paste a playlist or channel link (any channel, like `youtube.com/@CalebHammer`) and a panel opens before anything downloads. A channel link gets all its videos, without Shorts or live streams. Videos you already have are left out. Pick how many episodes, which ones (most viewed, random or playlist order) and the quality (up to 1080p, up to 720p or best up to 4K). It shows the size up front, and warns when that is more than the free space where **Saves to** points.

- Long runs wait a few seconds between episodes so YouTube does not block them.
- **Stop after this episode** ends a run cleanly. Paste the link again later and it skips what you already have.
- It stops by itself when the drive gets within 2 GB of full.
- Your PC does not go to sleep while something is downloading. The screen can still turn off.
- When some videos fail, the run says how many and why, and **Try again** downloads just those.

As an example, the 150 most viewed episodes of a 90-minute show come to about 125 GB at 1080p.

## Sign in to YouTube

**Sign in to YouTube** opens a separate Chrome or Edge window. You sign in to Google there yourself. The app never sees your password. It saves the YouTube cookies to `%LOCALAPPDATA%\YouTube to Plex\cookies.txt` on your PC and closes the window. **Sign out** deletes them again.

Signed in, you can download age-restricted videos, members-only videos from channels you are a member of, and private videos shared with you. Movies and shows bought on YouTube are copy-protected and cannot be downloaded.

YouTube can flag accounts that download a lot, so a spare Google account is the safer choice.

## Suggestions

Suggestions come from the newest uploads of the channels you download from most, plus a YouTube search built from the tags of your recent downloads. Videos you already have, Shorts and live streams are left out.

## When downloads start failing

YouTube changes often, and the downloader inside this app ([yt-dlp](https://github.com/yt-dlp/yt-dlp)) keeps up. Each time the app starts, it checks GitHub for a new yt-dlp release, fetches it when there is one (about 3 MB) and uses it instead of the copy inside the exe. So when downloads start failing, close the app and open it again.

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
