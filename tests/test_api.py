import threading

import groq
import httpx
import pytest

from downloader import DownloadError
from errors import ConfigError

URL = "https://www.instagram.com/reel/De2eDNFtpfm/?xtok=abc"


def post(client, body, **kw):
    return client.post("/api/process", json=body, **kw)


class Fakes:
    """Replace the pipeline stages inside the app module."""

    def __init__(self, app_module, monkeypatch, tmp_path):
        self.calls = []
        audio = tmp_path / "a.m4a"
        audio.write_bytes(b"x")
        self.audio = str(audio)

        def dl(url):
            self.calls.append(url)
            return self.audio, "Title", "http://t/x.jpg", "user", 55
        monkeypatch.setattr(app_module, "download_reel_audio", dl)
        monkeypatch.setattr(app_module, "transcribe",
                            lambda p: {"text": "hello world", "segments": [{"start": 0, "end": 1, "text": "hello world"}],
                                       "language": "english", "translation": None})
        monkeypatch.setattr(app_module, "summarize",
                            lambda t, segs=None, tr=None: {"summary": "SUMMARY", "card": None, "truncated": False})


@pytest.fixture()
def fakes(app_module, monkeypatch, tmp_path):
    return Fakes(app_module, monkeypatch, tmp_path)


# ── request validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [[1], "str", 5, None, {}, {"url": 123}, {"url": None}, {"url": ["x"]}])
def test_bad_body_is_json_400(client, fakes, body):
    r = post(client, body)
    assert r.status_code == 400
    assert r.is_json and "error" in r.get_json()
    assert fakes.calls == []


def test_non_json_request_is_json_400(client, fakes):
    r = client.post("/api/process", data="url=x", content_type="text/plain")
    assert r.status_code == 400 and r.is_json


def test_hostile_url_never_reaches_downloader(client, fakes):
    r = post(client, {"url": "http://127.0.0.1:9999/audio.mp3?tag=instagram.com"})
    assert r.status_code == 400
    assert fakes.calls == []


# ── happy path, cache, canonical URL ─────────────────────────────────────────

def test_success_then_cache(client, fakes):
    r = post(client, {"url": URL})
    assert r.status_code == 200
    body = r.get_json()
    assert body["url"] == "https://www.instagram.com/reel/De2eDNFtpfm/"
    assert body["summary"] == "SUMMARY" and body["cached"] is False and body["truncated"] is False
    assert fakes.calls == ["https://www.instagram.com/reel/De2eDNFtpfm/"]  # canonical only

    r2 = post(client, {"url": "https://www.instagram.com/reel/De2eDNFtpfm/?xtok=DIFFERENT"})
    assert r2.status_code == 200 and r2.get_json()["cached"] is True
    assert len(fakes.calls) == 1  # no second download


def test_card_and_translation_flow_through_and_are_cached(client, fakes, monkeypatch, app_module):
    card = {"tldr": "T", "steps": [], "tools": [], "snippets": [], "insights": [], "warnings": [], "missing": ["m"]}
    monkeypatch.setattr(app_module, "transcribe", lambda p: {
        "text": "नमस्ते", "segments": [{"start": 0, "end": 1, "text": "नमस्ते"}], "language": "hindi",
        "translation": {"text": "Hello", "segments": [{"start": 0, "end": 1, "text": "Hello"}]}})
    seen = {}

    def fake_summarize(text, segs=None, tr=None):
        seen["translation"] = tr
        return {"summary": "TL;DR: T", "card": card, "truncated": False}
    monkeypatch.setattr(app_module, "summarize", fake_summarize)

    body = post(client, {"url": URL}).get_json()
    assert body["card"] == card and body["language"] == "hindi"
    assert body["transcript_en"] == "Hello" and body["segments_en"][0]["text"] == "Hello"
    assert seen["translation"] == "Hello"  # translation passed to the summarizer as context

    cached = post(client, {"url": URL}).get_json()
    assert cached["cached"] is True and cached["card"] == card and cached["transcript_en"] == "Hello"


def test_temp_audio_removed(client, fakes):
    import os
    post(client, {"url": URL})
    assert not os.path.exists(fakes.audio)


# ── error mapping ────────────────────────────────────────────────────────────

def _raise(exc):
    def f(*a, **k):
        raise exc
    return f


def _req():
    return httpx.Request("POST", "https://api.groq.com/x")


@pytest.mark.parametrize("exc, status", [
    (DownloadError("raw secret path C:\\x", "rate_limit", "slow down"), 429),
    (DownloadError("raw", "auth", "re-export cookies"), 502),
    (DownloadError("raw", "unknown", "could not download"), 502),
    (ConfigError("GROQ_API_KEY not set"), 500),
    (groq.RateLimitError("rl", response=httpx.Response(429, request=_req()), body=None), 429),
    (groq.APITimeoutError(request=_req()), 504),
    (groq.APIConnectionError(request=_req()), 502),
    (groq.InternalServerError("boom", response=httpx.Response(500, request=_req()), body=None), 502),
    (ValueError("too long"), 400),
    (RuntimeError("secret internal detail"), 500),
])
def test_error_mapping(client, fakes, monkeypatch, app_module, exc, status):
    monkeypatch.setattr(app_module, "download_reel_audio", _raise(exc))
    r = post(client, {"url": URL})
    assert r.status_code == status
    assert r.is_json
    text = r.get_data(as_text=True)
    assert "C:\\x" not in text  # raw downloader text stays in the server log
    assert "secret internal detail" not in text
    assert "Traceback" not in text


def test_internal_error_message_is_generic(client, fakes, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "transcribe", _raise(RuntimeError("secret internal detail")))
    r = post(client, {"url": URL})
    assert r.status_code == 500
    assert "secret internal detail" not in r.get_data(as_text=True)


def test_failed_job_leaves_no_row(client, fakes, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "transcribe", _raise(RuntimeError("x")))
    post(client, {"url": URL})
    assert client.get("/api/library").get_json() == []


# ── duplicate-submission guard ───────────────────────────────────────────────

def test_concurrent_duplicate_is_rejected(client, fakes, monkeypatch, app_module):
    started, release = threading.Event(), threading.Event()
    real_transcribe = app_module.transcribe

    def slow(path):
        started.set()
        release.wait(5)
        return real_transcribe(path)
    monkeypatch.setattr(app_module, "transcribe", slow)

    results = {}
    t = threading.Thread(target=lambda: results.update(first=post(app_module.app.test_client(), {"url": URL}).status_code))
    t.start()
    assert started.wait(5)

    second = post(client, {"url": URL})
    assert second.status_code == 409
    assert len(fakes.calls) == 1

    release.set()
    t.join(5)
    assert results["first"] == 200
    # guard is released afterwards
    assert post(client, {"url": URL}).get_json()["cached"] is True


# ── CORS ─────────────────────────────────────────────────────────────────────

def test_cors_null_origin_not_allowed(client):
    r = client.get("/api/health", headers={"Origin": "null"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_cors_frontend_origin_allowed(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:8080"})
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"
