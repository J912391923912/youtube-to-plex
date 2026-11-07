"""YouTube to Plex: a small local page that downloads YouTube videos for Plex and suggests more."""
import collections
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
from urllib.request import urlopen

import yt_dlp
from websockets.sync.client import connect

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

# Best picture up to 4K (60 fps when offered), VP9 before AV1 so more Plex clients play it
# without transcoding, best audio track (Opus), merged into MKV with chapters and metadata,
# plus a .jpg next to the file that Plex uses as the poster.
YTDLP_ARGS = [
    "-f", "bv*+ba/b",
    "-S", "res:2160,fps,vcodec:vp9",
    "--merge-output-format", "mkv",
    "--embed-metadata", "--embed-chapters",
    "--write-thumbnail", "--convert-thumbnails", "jpg",
    "--download-archive", str(DATA / "archive.txt"),
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


def worker():
    while True:
        job = work.get()
        job["state"] = "starting"

        def on_progress(d):
            info = d["info_dict"]
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job.update(vid=info.get("id"), title=info.get("title") or job["title"])
            if d["status"] == "downloading":
                job.update(state="downloading",
                           part="picture" if info.get("vcodec") not in (None, "none") else "sound",
                           percent=round(100 * d.get("downloaded_bytes", 0) / total) if total else None)

        def on_postprocess(d):
            if d["status"] == "started" and d["postprocessor"] == "Merger":
                job["state"] = "merging"

        log = Log()
        signed_in = ["--cookies", str(COOKIES)] if COOKIES.exists() else []
        opts = yt_dlp.parse_options(YTDLP_ARGS + signed_in + ["-P", job["dir"]]).ydl_opts
        opts.update(progress_hooks=[on_progress], postprocessor_hooks=[on_postprocess], logger=log)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job["url"])
            done = [e for e in (info.get("entries") or [info]) if e] if info else []
            if not done:
                if log.errors:
                    job.update(state="error", error=log.errors[-1])
                else:
                    job["state"] = "skipped"
                continue
            history = load_history()
            for e in done:
                history.append({
                    "id": e["id"], "title": e.get("title"),
                    "channel": e.get("channel") or e.get("uploader"),
                    "channel_url": e.get("channel_url") or e.get("uploader_url"),
                    "tags": (e.get("tags") or [])[:15], "quality": quality(e),
                    "when": time.strftime("%Y-%m-%d"),
                })
            HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), "utf-8")
            result = quality(done[0]) if len(done) == 1 else f"{len(done)} videos"
            job.update(state="done", vid=done[0]["id"], title=done[0].get("title"),
                       result=result + (f", {len(log.errors)} could not be downloaded" if log.errors else ""))
        except Exception as err:  # keep the worker alive, show the reason on the page
            job.update(state="error", error=Log.clean(str(err)))


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
            return self.reply({"jobs": jobs[::-1], "history": load_history()[::-1][:60], "default_dir": DEFAULT_DIR,
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
        if self.path != "/api/download":
            return self.reply({"error": "Not found"}, 404)
        url = str(body.get("url", "")).strip()
        if not url.startswith(("https://", "http://")):
            return self.reply({"error": "That is not a link. Copy the address from YouTube and paste it here."}, 400)
        job = {"id": uuid.uuid4().hex, "url": url, "dir": str(body.get("dir") or DEFAULT_DIR).strip(),
               "state": "queued", "title": url}
        jobs.append(job)
        work.put(job)
        self.reply(job)

    def log_message(self, *args):
        pass


def main():
    ThreadingHTTPServer.allow_reuse_address = False  # on Windows, reuse would let a second copy share the port
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running: just show it
        open_window()
        return
    threading.Thread(target=lambda: (install_missing(), worker()), daemon=True).start()
    if "--no-browser" not in sys.argv:
        # an app window reports in every second; a normal browser tab can be throttled to once a minute
        idle_limit[0] = 30 if open_window() else 180
        threading.Thread(target=quit_when_closed, daemon=True).start()
        add_to_start_menu()
    server.serve_forever()


if __name__ == "__main__":
    main()
