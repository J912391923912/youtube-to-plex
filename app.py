"""YouTube to Plex: a small local page that downloads YouTube videos for Plex and suggests more."""
import collections
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from websockets.sync.client import connect

# yt_dlp is imported in main(), after update_ytdlp() has put the newest copy first in line

NAME = "YouTube to Plex"
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # inside the .exe or next to app.py
DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / NAME
DATA.mkdir(parents=True, exist_ok=True)
if sys.stderr is None:  # the .exe has no console window, so keep yt-dlp's output in a file
    sys.stdout = sys.stderr = open(DATA / "log.txt", "w", encoding="utf-8", buffering=1)
HISTORY = DATA / "history.json"
COOKIES = DATA / "cookies.txt"   # your YouTube sign-in, never leaves this PC
PROFILE = DATA / "browser"       # the browser window you sign in with
DEFAULT_DIR = str(Path.home() / "Videos" / "YouTube")
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
SIGN_IN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/"
BROWSERS = [Path(os.environ.get(root, "-")) / exe
            for exe in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe")
            for root in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]

# Best picture up to the chosen height (4K unless capped), 60 fps when offered, VP9 before AV1 so more Plex clients play it
# without transcoding, and the original-language audio track (Opus). Saved as MP4, because that is the container Plex
# reads embedded title, date and description from (it ignores them in MKV). Captions, chapters and stream languages
# are embedded too, and a .jpg next to the file is the poster.
ARCHIVE = DATA / "archive.txt"
YTDLP = DATA / "yt-dlp.zip"  # yt-dlp's newest release, used instead of the copy inside the exe
YTDLP_REPO = "https://github.com/yt-dlp/yt-dlp"
YTDLP_ARGS = [
    "-f", "bv*+ba/b",
    "--merge-output-format", "mp4",
    "--embed-metadata", "--embed-chapters",
    "--write-subs", "--write-auto-subs", "--embed-subs", "--sleep-subtitles", "1",
    "--write-thumbnail", "--convert-thumbnails", "jpg",
    "--download-archive", str(ARCHIVE),
    "--ignore-errors",  # a caption that fails to download is a warning, the video still saves
    "-o", "%(channel,uploader)s/%(upload_date>%Y-%m-%d)s %(title)s [%(id)s].%(ext)s",
    "--no-playlist",  # a watch link inside a playlist gets that one video
    "--js-runtimes", "node",  # Deno is used by default, Node when that is what is installed
    "--no-progress",
]
CODECS = {"vp09": "VP9", "vp9": "VP9", "av01": "AV1", "avc1": "H.264", "hev1": "HEVC",
          "opus": "Opus", "mp4a": "AAC"}

# (name, download, programs that already do the job, manual install command)
TOOLS = [
    ("ffmpeg", "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
     ("ffmpeg",), "winget install Gyan.FFmpeg"),
    ("Deno", "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
     ("deno", "node"), "winget install DenoLand.Deno"),
]
BIN = DATA / "bin"
BIN.mkdir(exist_ok=True)
os.environ["PATH"] += os.pathsep + str(BIN)  # your own installs win, ours fill the gap

jobs = []
work = queue.Queue()
account = {"state": "in" if COOKIES.exists() else "out"}
setup = {"state": "installing", "tool": None, "percent": 0}
last_seen = [time.time()]
idle_limit = [float("inf")]


def load_history():
    try:
        return json.loads(HISTORY.read_text("utf-8"))
    except FileNotFoundError:
        return []


def install_missing():
    """First run: fetch ffmpeg and Deno from their official GitHub releases into DATA/bin."""
    for label, url, have, cmd in TOOLS:
        if any(shutil.which(exe) for exe in have):
            continue
        setup.update(state="installing", tool=label, percent=0)
        zip_path = BIN / f"{label}.zip"
        try:
            with urlopen(url, timeout=30) as r, open(zip_path, "wb") as f:
                total, done = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    setup["percent"] = round(100 * done / total) if total else None
            with zipfile.ZipFile(zip_path) as z:
                # programs last, so an interrupted unpack never leaves an exe without its DLLs
                for m in sorted(z.infolist(), key=lambda m: m.filename.endswith(".exe")):
                    name = Path(m.filename).name  # flat into BIN, so no path from the zip is ever used
                    if name.endswith((".exe", ".dll")) and name != "ffplay.exe":
                        with z.open(m) as src, open(BIN / f"{name}.part", "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.replace(BIN / f"{name}.part", BIN / name)
        except Exception:
            setup.update(state="error", error=f"Could not download {label}. Check the internet connection and start "
                                              f"YouTube to Plex again, or install it yourself in PowerShell: {cmd}")
            return
        finally:
            zip_path.unlink(missing_ok=True)
    setup.update(state="ready", tool=None)


def update_ytdlp():
    """YouTube changes often and yt-dlp keeps up. Each start fetches yt-dlp's newest release (about 3 MB, only when
    there is a new one) and loads it instead of the copy inside the exe, so downloads keep working without a new
    version of this app. Offline, it keeps the last one it got."""
    try:
        with zipfile.ZipFile(YTDLP) as z:
            have = re.search(r"__version__ = '([^']+)'", z.read("yt_dlp/version.py").decode())[1]
    except Exception:
        have = None
    try:
        with urlopen(Request(f"{YTDLP_REPO}/releases/latest", method="HEAD"), timeout=5) as r:
            latest = r.url.rsplit("/", 1)[1]  # it redirects to .../releases/tag/2026.10.26
        if latest != have:
            part = YTDLP.with_suffix(".part")
            with urlopen(f"{YTDLP_REPO}/releases/download/{latest}/yt-dlp", timeout=60) as r:
                part.write_bytes(r.read())
            with zipfile.ZipFile(part) as z:  # whole and the version it claims, or it is not used
                assert z.testzip() is None and f"'{latest}'" in z.read("yt_dlp/version.py").decode()
            os.replace(part, YTDLP)
    except Exception:
        pass
    if YTDLP.is_file():  # ponytail: assumes yt-dlp still runs on the exe's Python (3.13, until about 2029); rebuild before
        sys.path.insert(0, str(YTDLP))


def quality(info):
    fmts = info.get("requested_formats") or [info]
    v = next((f for f in fmts if f.get("vcodec") not in (None, "none")), {})
    a = next((f for f in fmts if f.get("acodec") not in (None, "none")), {})
    name = lambda c: CODECS.get((c or "").split(".")[0], c)
    fps = v.get("fps") or 0
    label = f"{v.get('height')}p{round(fps) if fps > 30 else ''} {name(v.get('vcodec'))}"
    if a:
        label += f", {name(a.get('acodec'))}" + (f" {round(a['abr'])} kbps" if a.get("abr") else "")
    return label


class Log:
    """yt-dlp reports errors here instead of raising, so a playlist keeps going past a broken video."""
    def __init__(self):
        self.errors = []

    def debug(self, msg):
        pass
    info = warning = debug

    def error(self, msg):
        self.errors.append(self.clean(msg))

    @staticmethod
    def clean(msg):  # "ERROR: [youtube] abc123: Video unavailable" -> "Video unavailable"
        return re.sub(r"^(ERROR: )?(\[\w+\] [\w-]+: )?", "", msg)


def free_bytes(folder):
    p = Path(folder)
    while not p.exists() and p != p.parent:  # the Saves to folder may not exist yet
        p = p.parent
    return shutil.disk_usage(p).free


def have_ids():
    """Every video ever downloaded, from yt-dlp's archive (lines like "youtube dQw4w9WgXcQ")."""
    try:
        return {line.split()[1] for line in ARCHIVE.read_text("utf-8").splitlines() if line.startswith("youtube ")}
    except FileNotFoundError:
        return set()


def sub_langs(info):
    """Captions in the video's own language plus English. yt-dlp takes manual captions over automatic ones."""
    own = (info.get("language") or "en").split("-")[0]
    return list(dict.fromkeys([own, "en"]))


def remember(e):
    history = load_history()
    history.append({
        "id": e["id"], "title": e.get("title"),
        "channel": e.get("channel") or e.get("uploader"),
        "channel_url": e.get("channel_url") or e.get("uploader_url"),
        "tags": (e.get("tags") or [])[:15], "quality": quality(e),
        "when": time.strftime("%Y-%m-%d"),
    })
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), "utf-8")


def worker():
    # ES_CONTINUOUS (+ ES_SYSTEM_REQUIRED): Windows does not sleep until the queue is empty. The screen can turn off.
    stay_awake = lambda on: ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | on)
    while True:
        if work.empty():
            stay_awake(False)
        job = work.get()
        stay_awake(True)
        series = job["total"] > 1

        def on_progress(d):
            info = d["info_dict"]
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job.update(vid=info.get("id"), **{"ep" if series else "title": info.get("title") or job["title"]})
            if d["status"] == "downloading":
                job.update(state="downloading",
                           part="picture" if info.get("vcodec") not in (None, "none") else "sound",
                           percent=round(100 * d.get("downloaded_bytes", 0) / total) if total else None)

        def on_postprocess(d):
            if d["status"] == "started" and d["postprocessor"] == "Merger":
                job["state"] = "merging"

        log = Log()
        args = YTDLP_ARGS + ["-S", f"res:{job['max']},fps,vcodec:vp9", "-P", job["dir"]]
        if COOKIES.exists():
            args += ["--cookies", str(COOKIES)]
        if series:  # a gentle pace keeps YouTube from blocking a long run
            args += ["--sleep-interval", "3", "--max-sleep-interval", "10"]
        opts = yt_dlp.parse_options(args).ydl_opts
        opts.update(progress_hooks=[on_progress], postprocessor_hooks=[on_postprocess], logger=log)
        saved, failed, skipped, ended = [], [], 0, None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                for n, url in enumerate(job["urls"], 1):
                    if job.get("stop"):
                        ended = "stopped"
                        break
                    try:
                        if free_bytes(job["dir"]) < 2e9:
                            ended = "full"
                            break
                        job.update(state="starting", index=n, percent=None)
                        errors = len(log.errors)
                        info = ydl.extract_info(url, download=False)
                        if info and info.get("entries") is not None:
                            log.errors.append("That is a playlist. Paste it in the link box to pick its episodes.")
                            info = None
                        elif info and ydl.in_download_archive(info):
                            skipped += 1
                            continue
                        elif info:
                            ydl.params["subtitleslangs"] = sub_langs(info)
                            info = ydl.process_ie_result(info, download=True)
                        if not info and len(log.errors) == errors:
                            skipped += 1  # yt-dlp quietly returns nothing for a video already in its archive
                            continue
                        if not info or len(log.errors) > errors:
                            failed.append(url)
                            continue
                        remember(info)  # one at a time, so a long run that is cut off still counts what it saved
                        saved.append(info)
                    except Exception as err:  # one broken video never ends the run
                        log.errors.append(Log.clean(str(err)))
                        failed.append(url)
        except Exception as err:  # keep the worker alive, show the reason on the page
            log.errors.append(Log.clean(str(err)))
        full = "The drive is full. Free up space or pick another Saves to folder, then start it again."
        job.update(failed=failed, error=log.errors[-1] if log.errors else None)
        if not saved:
            if ended == "full":
                job.update(state="error", error=full)
            elif failed or log.errors:
                job["state"] = "error"
            elif ended == "stopped":
                job["state"] = "stopped"
            else:
                job["state"] = "skipped"
            continue
        count = f"{len(saved)} video" + ("s" if len(saved) != 1 else "")
        result = quality(saved[0]) if not series and len(saved) == 1 else count
        if skipped:
            result += f", {skipped} you already had"
        if failed:
            result += f", {len(failed)} failed"
        result += {"stopped": ", then stopped as you asked", "full": ". " + full}.get(ended, "")
        job.update(state="done", result=result, **({} if series else {"vid": saved[0]["id"], "title": saved[0].get("title")}))


def series_info(url, folder):
    """A playlist or channel: its episodes with views and length, so the page can pick and size them."""
    if re.search(r"youtube\.com/(@[^/?]+|channel/[^/?]+|c/[^/?]+|user/[^/?]+)/?$", url):
        url = url.rstrip("/") + "/videos"  # a channel's own page lists its tabs, not its videos
    log = Log()
    opts = yt_dlp.parse_options(["--flat-playlist", "--js-runtimes", "node"]).ydl_opts
    opts["logger"] = log
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    have = have_ids()
    entries = [{"id": e["id"], "title": e.get("title"), "duration": e.get("duration") or 0,
                "views": e.get("view_count") or 0, "have": e["id"] in have}
               for e in (info or {}).get("entries") or [] if e.get("id") and e.get("duration")]
    if not entries:
        raise ValueError(log.errors[-1] if log.errors else "That link has no videos to download.")
    try:
        free = free_bytes(folder or DEFAULT_DIR)
    except OSError:
        free = None
    return {"title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
            "entries": entries, "free": free}


def flat(source, n):
    opts = yt_dlp.parse_options(["--flat-playlist", "-I", f":{n}", "--js-runtimes", "node"]).ydl_opts
    opts["logger"] = Log()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(source, download=False).get("entries") or []
    except Exception:
        return []


def pick(results, have, per_source=6):
    """Videos not downloaded yet, no Shorts or live streams, at most per_source from each source."""
    out, seen = [], set(have)
    for reason, entries in results:
        n = 0
        for e in entries:
            vid = e.get("id")
            if not vid or vid in seen or (e.get("duration") or 0) < 120:
                continue
            seen.add(vid)
            out.append({"id": vid, "title": e.get("title"), "duration": e.get("duration"),
                        "channel": e.get("channel") or e.get("uploader"), "reason": reason})
            n += 1
            if n == per_source:
                break
    return out


def suggest():
    history = load_history()
    channels = collections.Counter((h["channel"], h["channel_url"]) for h in history if h.get("channel_url"))
    tags = collections.Counter(t.lower() for h in history[-30:] for t in h.get("tags") or [])
    sources = [(f"{url}/videos", f"New from {name}", 12) for (name, url), _ in channels.most_common(4)]
    if tags:
        query = " ".join(t for t, _ in tags.most_common(3))
        sources.append((f"ytsearch20:{query}", "Close to what you download", 20))
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda s: (s[1], flat(s[0], s[2])), sources))
    return pick(results, {h["id"] for h in history})


# Sign-in: a separate browser window with its own profile. You sign in to Google there yourself,
# this app never sees the password. It reads the YouTube cookies over the browser's local debug
# port, saves them for yt-dlp and closes the window.
def cdp(method):
    port = (PROFILE / "DevToolsActivePort").read_text().split()[0]
    ws_url = json.load(urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3))["webSocketDebuggerUrl"]
    with connect(ws_url, max_size=None, open_timeout=3) as ws:
        ws.send(json.dumps({"id": 1, "method": method}))
        return json.loads(ws.recv(timeout=5)).get("result", {})


def close_browser():
    try:
        cdp("Browser.close")
    except Exception:
        pass


def save_cookies(cookies):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        if c["domain"].endswith(("youtube.com", "google.com")):
            lines.append("\t".join([c["domain"], str(c["domain"].startswith(".")).upper(), c["path"],
                                    str(c["secure"]).upper(), str(int(max(c["expires"], 0))), c["name"], c["value"]]))
    COOKIES.write_text("\n".join(lines) + "\n", "utf-8")


def sign_in(browser):
    (PROFILE / "DevToolsActivePort").unlink(missing_ok=True)
    subprocess.Popen([str(browser), f"--user-data-dir={PROFILE}", "--remote-debugging-port=0",
                      "--no-first-run", "--no-default-browser-check", SIGN_IN_URL])
    opened = False
    for _ in range(900):  # 15 minutes to finish signing in
        time.sleep(1)
        if account["state"] != "waiting":  # cancelled from the page
            return
        try:
            cookies = cdp("Storage.getCookies")["cookies"]
            opened = True
        except Exception:
            if opened:
                break  # window was closed before signing in
            continue
        if any(c["name"] == "LOGIN_INFO" for c in cookies):
            time.sleep(3)  # let YouTube finish setting its cookies
            save_cookies(cdp("Storage.getCookies")["cookies"])
            close_browser()
            account.update(state="in", error=None)
            return
    close_browser()
    account.update(state="out", error="Sign-in was not finished. Try again and sign in in the window that opens.")


def start_sign_in():
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not browser:
        return account.update(state="out", error="Sign-in needs Google Chrome or Microsoft Edge installed.")
    account.update(state="waiting", error=None)
    threading.Thread(target=sign_in, args=(browser,), daemon=True).start()


def sign_out():
    account.update(state="out", error=None)
    close_browser()
    time.sleep(1)
    COOKIES.unlink(missing_ok=True)
    shutil.rmtree(PROFILE, ignore_errors=True)


def open_window():
    """The page in its own app window (Chrome or Edge, no tabs or address bar). False if it had to use a tab."""
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not browser:
        webbrowser.open(URL)
        return False
    subprocess.Popen([str(browser), f"--app={URL}", f"--user-data-dir={DATA / 'window'}", "--window-size=1120,900",
                      "--no-first-run", "--no-default-browser-check",
                      # keep the page checking in while minimized, so the app can tell open from closed
                      "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                      "--disable-backgrounding-occluded-windows"])
    return True


def quit_when_closed():
    """The page checks in every second. When it stops, its window was closed, so the app quits."""
    while True:
        time.sleep(5)
        if time.time() - last_seen[0] > idle_limit[0]:
            time.sleep(5)  # after sleep or hibernate the page needs a moment to check in again
            if time.time() - last_seen[0] > idle_limit[0]:
                if account["state"] == "waiting":
                    close_browser()
                os._exit(0)


def add_to_start_menu():
    """Lets the app be found in the Start menu. Refreshed on every start, so it follows the exe if it moves."""
    if not getattr(sys, "frozen", False):
        return
    lnk = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{NAME}.lnk"
    q = lambda p: str(p).replace("'", "''")  # PowerShell single-quote escaping
    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
                      f"$s.TargetPath='{q(sys.executable)}';$s.Save()"], creationflags=subprocess.CREATE_NO_WINDOW)


class Handler(BaseHTTPRequestHandler):
    def reply(self, body, code=200, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Host") not in HOSTS:  # blocks DNS-rebinding pages
            return self.reply({"error": f"Open {NAME} at {URL}"}, 403)
        if self.path == "/":
            return self.reply((ASSETS / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/favicon.ico":
            return self.reply((ASSETS / "icon.ico").read_bytes(), ctype="image/x-icon")
        if self.path == "/api/state":
            last_seen[0] = time.time()
            return self.reply({"jobs": [{k: v for k, v in j.items() if k != "urls"} for j in jobs[::-1]],
                               "history": load_history()[::-1][:60], "default_dir": DEFAULT_DIR,
                               "account": account, "setup": setup})
        if self.path == "/api/suggest":
            return self.reply(suggest())
        self.reply({"error": "Not found"}, 404)

    def do_POST(self):
        # JSON only: other websites cannot send that to localhost without a preflight we never answer
        if self.headers.get("Host") not in HOSTS or self.headers.get("Content-Type") != "application/json":
            return self.reply({"error": "Refused"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            assert isinstance(body, dict)
        except Exception:
            return self.reply({"error": "Bad request"}, 400)
        if self.path == "/api/signin":
            start_sign_in()
            return self.reply(account)
        if self.path == "/api/signout":
            sign_out()
            return self.reply(account)
        if self.path == "/api/open":  # video links open in your normal browser, not in the app window
            if str(body.get("url", "")).startswith("https://www.youtube.com/"):
                webbrowser.open(body["url"])
            return self.reply({})
        if self.path == "/api/series":
            try:
                return self.reply(series_info(str(body.get("url", "")).strip(), str(body.get("dir", "")).strip()))
            except Exception as err:
                return self.reply({"error": "Could not read that playlist: " + Log.clean(str(err))}, 400)
        if self.path == "/api/retry":  # the videos that failed, as a new run
            old = next((j for j in jobs if j["id"] == body.get("id") and j.get("failed") and not j.get("retried")), None)
            if not old:
                return self.reply({"error": "Nothing to retry."}, 400)
            old["retried"] = True
            job = {"id": uuid.uuid4().hex, "urls": old["failed"], "total": len(old["failed"]), "max": old["max"],
                   "title": old["title"], "dir": old["dir"], "state": "queued"}
            jobs.append(job)
            work.put(job)
            return self.reply({})
        if self.path == "/api/stop":  # finishes the episode it is on, then stops
            for job in jobs:
                if job["id"] == body.get("id"):
                    job["stop"] = True
            return self.reply({})
        if self.path != "/api/download":
            return self.reply({"error": "Not found"}, 404)
        top = body.get("max") if body.get("max") in (2160, 1080, 720) else 2160
        ids = [i for i in body.get("ids") or [] if isinstance(i, str) and re.fullmatch(r"[\w-]{11}", i)]
        if ids:  # episodes picked from a playlist
            urls, title = [f"https://www.youtube.com/watch?v={i}" for i in ids], str(body.get("title") or "Playlist")
        else:
            url = str(body.get("url", "")).strip()
            if not url.startswith(("https://", "http://")):
                return self.reply({"error": "That is not a link. Copy the address from YouTube and paste it here."}, 400)
            vid = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([\w-]{11})", url)
            if vid and vid[1] in have_ids():
                title = next((h["title"] for h in load_history() if h["id"] == vid[1]), None)
                return self.reply({"have": title or "this video"})
            urls, title = [url], url
        job = {"id": uuid.uuid4().hex, "urls": urls, "total": len(urls), "max": top, "title": title,
               "dir": str(body.get("dir") or DEFAULT_DIR).strip(), "state": "queued"}
        jobs.append(job)
        work.put(job)
        self.reply({k: v for k, v in job.items() if k != "urls"})

    def log_message(self, *args):
        pass


def main():
    ThreadingHTTPServer.allow_reuse_address = False  # on Windows, reuse would let a second copy share the port
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running: just show it
        open_window()
        return
    update_ytdlp()  # only here, past the port check, so a second start never swaps the copy in use
    global yt_dlp
    import yt_dlp
    print("yt-dlp", yt_dlp.version.__version__, "from", yt_dlp.__file__)
    threading.Thread(target=lambda: (install_missing(), worker()), daemon=True).start()
    if "--no-browser" not in sys.argv:
        # an app window reports in every second; a normal browser tab can be throttled to once a minute
        idle_limit[0] = 30 if open_window() else 180
        threading.Thread(target=quit_when_closed, daemon=True).start()
        add_to_start_menu()
    server.serve_forever()


if __name__ == "__main__":
    main()
