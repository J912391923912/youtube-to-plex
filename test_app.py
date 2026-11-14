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
