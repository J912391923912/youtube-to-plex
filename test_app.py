import json
import tempfile
import threading
import time
import types
from pathlib import Path

import app
from app import Log, free_bytes, pick

found = pick([
    ("New from A", [{"id": "a", "duration": 600}, {"id": "old", "duration": 600}, {"id": "short", "duration": 30}]),
    ("Close to what you download", [{"id": "a", "duration": 600}, {"id": "b", "duration": 300}, {"id": "live"}]),
], have={"old"})
assert [v["id"] for v in found] == ["a", "b"], found  # no repeats, no Shorts, no live, nothing already downloaded
assert found[0]["reason"] == "New from A"
assert Log.clean("ERROR: [youtube] BaW_jenozKc: This video is unavailable") == "This video is unavailable"
assert free_bytes(Path.home() / "no-such-folder" / "deeper") > 0  # Saves to may not exist yet

# suggestions: a 40-video run on one day counts once, so channels you came back to on other days win,
# and three searches start from across the whole history
hist = [{"id": f"r{i}", "title": "t", "channel": "Run", "channel_url": "run", "when": "2026-10-01"} for i in range(40)]
hist += [{"id": c + d, "title": "t", "channel": c, "channel_url": c, "when": "2026-10-0" + d} for c in "BCDEFGHI" for d in "23"]
asked, real = [], (app.load_history, app.flat)
app.load_history, app.flat = (lambda: hist), (lambda url, n: asked.append(url) or [])
app.suggest()
app.load_history, app.flat = real
assert "run/videos" not in asked and sum(u.startswith("ytsearch") for u in asked) == 3, asked
# following: only what came out after you followed, not what you have, not a premiere that has not aired
app.FOLLOWS = Path(tempfile.mkdtemp()) / "follows.json"
app.FOLLOWS.write_text(json.dumps([{"url": "c", "title": "C", "max": 1080, "dir": ".", "seen": ["was-up-then"]}]))
real = app.flat, app.have_ids
app.flat = lambda url, n: [{"id": "was-up-then", "duration": 600}, {"id": "posted-now1", "duration": 600},
                           {"id": "have-it-now", "duration": 600}, {"id": "airs-soon-1"}]
app.have_ids = lambda: {"have-it-now"}
app.check_follows()
app.flat, app.have_ids = real
assert [j["urls"] for j in app.jobs] == [["https://www.youtube.com/watch?v=posted-now1"]], app.jobs
app.jobs.clear()
app.work.get_nowait()

app.YTDLP_REPO = "http://127.0.0.1:9"  # nothing answers there, like starting offline
app.update_ytdlp()  # must carry on with the copy it has

# yt-dlp failing outside any one video ends that job with an error, and the next job still runs
app.yt_dlp = types.SimpleNamespace(parse_options=lambda args: types.SimpleNamespace(ydl_opts={}),
                                   YoutubeDL=lambda opts: 1 / 0)
runs = [{"id": str(i), "urls": ["u"], "total": 1, "max": 720, "dir": ".", "title": "t", "state": "queued"} for i in "ab"]
for job in runs:
    app.work.put(job)
threading.Thread(target=app.worker, daemon=True).start()
deadline = time.time() + 5
while any(j["state"] == "queued" for j in runs) and time.time() < deadline:
    time.sleep(0.05)
assert [j["state"] for j in runs] == ["error", "error"], runs
print("ok")
