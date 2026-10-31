"""Gist card: structured summary, evidence verification, fallback, and language handling."""
import json
from types import SimpleNamespace

import pytest

import summarizer
import transcriber

SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "Today I will show you Cursor."},
    {"start": 3.0, "end": 7.5, "text": "Install Cursor and open your project."},
    {"start": 7.5, "end": 12.0, "text": "Press command K and type: refactor this function."},
    {"start": 12.0, "end": 15.0, "text": "Comment PROMPT and I'll send you my full prompt."},
]
TEXT = " ".join(s["text"] for s in SEGMENTS)


def card_json(**over):
    card = {
        "tldr": "Use Cursor's Cmd+K to edit code with a prompt.",
        "steps": [{"text": "Install Cursor and open a project", "evidence": "Install Cursor and open your project", "t": 99.0},
                  {"text": "Invented step", "evidence": "this sentence was never said", "t": 5.0}],
        "tools": [{"name": "Cursor", "what": "AI code editor", "evidence": "show you Cursor", "t": None}],
        "snippets": [{"kind": "prompt", "text": "refactor this function", "t": 7.5}],
        "insights": [],
        "warnings": [],
        "missing": ["The full prompt is offered via comments but never said"],
    }
    card.update(over)
    return json.dumps(card)


class FakeClient:
    """Mimics groq.Groq().chat.completions.create; returns queued responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        content, finish = self.responses.pop(0)
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def use(monkeypatch, client):
    monkeypatch.setattr(summarizer, "_get_client", lambda: client)
    return client


# ── structured card ──────────────────────────────────────────────────────────

def test_card_is_returned_verified_and_rendered(monkeypatch):
    c = use(monkeypatch, FakeClient((card_json(), "stop")))
    r = summarizer.summarize(TEXT, SEGMENTS)
    card = r["card"]
    assert r["truncated"] is False

    real, invented = card["steps"]
    assert real["verified"] is True and real["t"] == 3.0          # snapped to the real segment, not the model's 99
    assert invented["verified"] is False                          # flagged for the UI
    assert card["tools"][0]["verified"] is True and card["tools"][0]["t"] == 0.0
    assert card["snippets"][0]["verified"] is True
    assert card["missing"] == ["The full prompt is offered via comments but never said"]

    # plain-text rendering used for copy + library preview
    assert r["summary"].startswith("TL;DR: Use Cursor")
    assert "✅ Install Cursor and open a project [0:03]" in r["summary"]
    assert "❓ The full prompt" in r["summary"]

    # the model saw timestamped lines and a strict schema
    sent = c.calls[0]
    assert "[3.0] Install Cursor" in sent["messages"][1]["content"]
    assert sent["response_format"]["json_schema"]["strict"] is True


def test_bad_json_falls_back_to_free_text(monkeypatch):
    c = use(monkeypatch, FakeClient(("{not json", "stop"), ("{still not", "stop"), ("LEGACY SUMMARY", "stop")))
    r = summarizer.summarize(TEXT, SEGMENTS)
    assert r["card"] is None and r["summary"] == "LEGACY SUMMARY"
    assert len(c.calls) == 3 and "response_format" not in c.calls[2]


EMPTY = json.dumps({"tldr": "x", "steps": [], "tools": [], "snippets": [], "insights": [], "warnings": [], "missing": []})
LONG_TEXT = TEXT * 5  # over the 300-character "should have content" threshold


def test_thin_card_is_retried_and_richer_one_kept(monkeypatch):
    c = use(monkeypatch, FakeClient((EMPTY, "stop"), (card_json(), "stop")))
    r = summarizer.summarize(LONG_TEXT, SEGMENTS)
    assert len(c.calls) == 2 and len(r["card"]["steps"]) == 2


def test_thin_twice_keeps_card_instead_of_legacy(monkeypatch):
    c = use(monkeypatch, FakeClient((EMPTY, "stop"), (EMPTY, "stop")))
    r = summarizer.summarize(LONG_TEXT, SEGMENTS)
    assert len(c.calls) == 2 and r["card"] is not None and r["card"]["steps"] == []


def test_short_reel_with_empty_card_is_accepted(monkeypatch):
    c = use(monkeypatch, FakeClient((EMPTY, "stop")))
    r = summarizer.summarize("Same idea, different market, opposite result.", None)
    assert len(c.calls) == 1 and r["card"] is not None


def test_second_attempt_can_succeed(monkeypatch):
    c = use(monkeypatch, FakeClient(("{not json", "stop"), (card_json(), "stop")))
    r = summarizer.summarize(TEXT, SEGMENTS)
    assert r["card"] is not None and len(c.calls) == 2


def test_cut_off_card_falls_back(monkeypatch):
    use(monkeypatch, FakeClient(('{"tldr": "partial', "length"), ('{"tldr": "p', "length"), ("LEGACY", "stop")))
    assert summarizer.summarize(TEXT, SEGMENTS)["card"] is None


def test_legacy_cut_off_is_flagged(monkeypatch):
    use(monkeypatch, FakeClient(("{bad", "stop"), ("{bad", "stop"), ("LEGACY", "length")))
    assert "incomplete" in summarizer.summarize(TEXT, SEGMENTS)["summary"]


def _schema_rejection(message="Failed to generate JSON", code="json_validate_failed"):
    import groq, httpx
    req = httpx.Request("POST", "https://api.groq.com/x")
    body = {"error": {"message": message, "code": code}}
    return groq.BadRequestError(f"Error code: 400 - {body}", response=httpx.Response(400, request=req), body=body)


class RaisingClient(FakeClient):
    def create(self, **kw):
        self.calls.append(kw)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        msg = SimpleNamespace(content=item[0])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=item[1])])


def test_groq_schema_rejection_retries_then_falls_back(monkeypatch):
    c = use(monkeypatch, RaisingClient(_schema_rejection(), _schema_rejection(), ("LEGACY", "stop")))
    r = summarizer.summarize(TEXT, SEGMENTS)
    assert r["card"] is None and r["summary"] == "LEGACY" and len(c.calls) == 3


def test_other_groq_400_is_not_swallowed(monkeypatch):
    import groq
    use(monkeypatch, RaisingClient(_schema_rejection("model not found", "model_not_found")))
    with pytest.raises(groq.BadRequestError):
        summarizer.summarize(TEXT, SEGMENTS)


def test_empty_transcript_skips_api(monkeypatch):
    monkeypatch.setattr(summarizer, "_get_client", lambda: pytest.fail("API should not be called"))
    r = summarizer.summarize("   ")
    assert "No speech" in r["summary"] and r["card"] is None and r["truncated"] is False


def test_truncation_flag_and_budget(monkeypatch):
    c = use(monkeypatch, FakeClient((card_json(), "stop")))
    monkeypatch.setattr(summarizer, "MAX_CHARS", 100)
    r = summarizer.summarize("a" * 500)
    assert r["truncated"] is True
    assert len(c.calls[0]["messages"][1]["content"]) < 300


def test_translation_is_given_as_context(monkeypatch):
    c = use(monkeypatch, FakeClient((card_json(), "stop")))
    summarizer.summarize(TEXT, SEGMENTS, translation_text="ENGLISH VERSION")
    assert "ENGLISH VERSION" in c.calls[0]["messages"][1]["content"]
    assert "never copy evidence from it" in c.calls[0]["messages"][1]["content"]


# ── verification details ─────────────────────────────────────────────────────

def test_hindi_evidence_verifies_in_original_script():
    segs = [{"start": 0.0, "end": 4.0, "text": "आज मैं आपको Cursor दिखाऊंगा।"},
            {"start": 4.0, "end": 8.0, "text": "पहले Cursor install करो, फिर project खोलो।"}]
    text = " ".join(s["text"] for s in segs)
    card = summarizer.verify_card({
        "tldr": "x",
        "steps": [{"text": "Install Cursor", "evidence": "पहले Cursor install करो", "t": None},
                  {"text": "Translated, not quoted", "evidence": "first install Cursor", "t": None}],
        "tools": [], "snippets": [], "insights": [], "warnings": [], "missing": [],
    }, text, segs)
    assert card["steps"][0]["verified"] is True and card["steps"][0]["t"] == 4.0
    assert card["steps"][1]["verified"] is False


def test_short_quotes_match_whole_words_only():
    segs = [{"start": 0.0, "end": 2.0, "text": "Then type hi and pick haiku."},
            {"start": 2.0, "end": 4.0, "text": "This is the highest tier."}]
    text = " ".join(s["text"] for s in segs)
    card = summarizer.verify_card({
        "tldr": "x", "steps": [], "tools": [], "insights": [], "warnings": [], "missing": [],
        "snippets": [{"kind": "prompt", "text": "hi", "t": None},
                     {"kind": "prompt", "text": "gh", "t": None}],   # inside "highest", not a word
    }, text, segs)
    assert card["snippets"][0]["verified"] is True and card["snippets"][0]["t"] == 0.0
    assert card["snippets"][1]["verified"] is False


def test_out_of_range_and_bad_timestamps_are_dropped():
    card = summarizer.verify_card({
        "tldr": "x",
        "steps": [{"text": "a", "evidence": "nothing like this", "t": 500},
                  {"text": "b", "evidence": "nothing like this", "t": True},
                  {"text": "c", "evidence": "nothing like this", "t": 2.0}],
        "tools": [], "snippets": [], "insights": [], "warnings": [], "missing": [],
    }, TEXT, SEGMENTS)
    assert [s["t"] for s in card["steps"]] == [None, None, 2.0]


def test_list_lengths_are_capped():
    many = [{"text": str(i), "evidence": "x", "t": None} for i in range(50)]
    card = summarizer.verify_card({"tldr": "x", "steps": many, "tools": [], "snippets": [],
                                   "insights": many, "warnings": many, "missing": ["m"] * 50}, TEXT, SEGMENTS)
    assert len(card["steps"]) == 12 and len(card["insights"]) == 8
    assert len(card["warnings"]) == 6 and len(card["missing"]) == 6


# ── transcriber language handling ────────────────────────────────────────────

class FakeAudio:
    def __init__(self, language, fail_translation=False):
        self.language = language
        self.fail = fail_translation
        self.translated = False
        seg = SimpleNamespace(start=0.0, end=2.0, text=" नमस्ते दोस्तों ")
        self.transcriptions = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            text="नमस्ते दोस्तों", segments=[seg], language=language))
        self.translations = SimpleNamespace(create=self.translate)

    def translate(self, **kw):
        self.translated = True
        if self.fail:
            raise RuntimeError("translation down")
        return SimpleNamespace(text="Hello friends",
                               segments=[SimpleNamespace(start=0.0, end=2.0, text=" Hello friends ")])


@pytest.fixture()
def audio_file(tmp_path):
    p = tmp_path / "a.m4a"
    p.write_bytes(b"x")
    return str(p)


def _run(monkeypatch, audio_file, fake):
    monkeypatch.setattr(transcriber, "_get_client", lambda: SimpleNamespace(audio=fake))
    return transcriber.transcribe(audio_file)


def test_hindi_reel_gets_english_translation(monkeypatch, audio_file):
    fake = FakeAudio("Hindi")
    r = _run(monkeypatch, audio_file, fake)
    assert r["language"] == "hindi"
    assert r["translation"]["text"] == "Hello friends"
    assert r["translation"]["segments"][0] == {"start": 0.0, "end": 2.0, "text": "Hello friends"}
    assert r["segments"][0]["text"] == "नमस्ते दोस्तों"


@pytest.mark.parametrize("lang", ["english", "en", "English", None])
def test_english_or_unknown_reel_is_not_translated(monkeypatch, audio_file, lang):
    fake = FakeAudio(lang)
    r = _run(monkeypatch, audio_file, fake)
    assert r["translation"] is None and fake.translated is False


def test_translation_failure_keeps_original(monkeypatch, audio_file):
    fake = FakeAudio("hindi", fail_translation=True)
    r = _run(monkeypatch, audio_file, fake)
    assert r["translation"] is None and r["text"] == "नमस्ते दोस्तों"
