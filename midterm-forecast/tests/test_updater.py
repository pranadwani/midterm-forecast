"""Tests for downloading the latest polls. The FiftyPlusOne API is faked, so
these run offline and without an API key."""
import io
import os
import time
import urllib.error

from app import updater

GOOD_CSV = b"poll_id,question_id,pollster,state,candidate_name,party,pct\n1,1,Test Poll Co,Ohio,A,DEM,47\n"


def fake_api(monkeypatch, body=GOOD_CSV, status=200):
    calls = []

    def urlopen(url, timeout=None):
        calls.append(url)
        if status != 200:
            raise urllib.error.HTTPError(url, status, "error", None, None)
        return io.BytesIO(body)

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    return calls


def test_downloads_missing_files(monkeypatch, tmp_path):
    calls = fake_api(monkeypatch)
    assert updater.fetch_polls("key", str(tmp_path)) == list(updater.POLL_FILES)
    assert len(calls) == 2 and "api_key=key" in calls[0]
    assert (tmp_path / "senate_general.csv").read_bytes() == GOOD_CSV


def test_fresh_files_are_not_downloaded_again(monkeypatch, tmp_path):
    for name in updater.POLL_FILES:
        (tmp_path / name).write_bytes(b"old")
    calls = fake_api(monkeypatch)
    assert updater.fetch_polls("key", str(tmp_path)) == []
    assert calls == []


def test_stale_files_are_replaced(monkeypatch, tmp_path):
    path = tmp_path / "senate_general.csv"
    path.write_bytes(b"old")
    day_ago = time.time() - 24 * 3600
    os.utime(path, (day_ago, day_ago))
    fake_api(monkeypatch)
    assert "senate_general.csv" in updater.fetch_polls("key", str(tmp_path))
    assert path.read_bytes() == GOOD_CSV


def test_bad_key_keeps_existing_files(monkeypatch, tmp_path):
    path = tmp_path / "senate_general.csv"
    path.write_bytes(b"old")
    os.utime(path, (0, 0))
    calls = fake_api(monkeypatch, status=401)
    assert updater.fetch_polls("wrong", str(tmp_path)) == []
    assert len(calls) == 1                         # stops after the key is rejected
    assert path.read_bytes() == b"old"


def test_unexpected_response_is_not_saved(monkeypatch, tmp_path):
    fake_api(monkeypatch, body=b"<html>Service unavailable</html>")
    assert updater.fetch_polls("key", str(tmp_path)) == []
    assert os.listdir(tmp_path) == []


def test_rebuild_when_raw_file_is_newer(tmp_path):
    db = tmp_path / "forecast.db"
    assert updater.needs_rebuild(str(tmp_path), str(db))
    db.write_bytes(b"")
    os.utime(db, (1000, 1000))
    (tmp_path / "senate_general.csv").write_bytes(GOOD_CSV)
    assert updater.needs_rebuild(str(tmp_path), str(db))
    os.utime(db, None)
    os.utime(tmp_path / "senate_general.csv", (1000, 1000))
    assert not updater.needs_rebuild(str(tmp_path), str(db))
