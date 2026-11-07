from pathlib import Path

from app import Log, free_bytes, pick

found = pick([
    ("New from A", [{"id": "a", "duration": 600}, {"id": "old", "duration": 600}, {"id": "short", "duration": 30}]),
    ("Close to what you download", [{"id": "a", "duration": 600}, {"id": "b", "duration": 300}, {"id": "live"}]),
], have={"old"})
assert [v["id"] for v in found] == ["a", "b"], found  # no repeats, no Shorts, no live, nothing already downloaded
assert found[0]["reason"] == "New from A"
assert Log.clean("ERROR: [youtube] BaW_jenozKc: This video is unavailable") == "This video is unavailable"
assert free_bytes(Path.home() / "no-such-folder" / "deeper") > 0  # Saves to may not exist yet
print("ok")
