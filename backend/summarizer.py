"""
summarizer.py — Turn a transcript into an evidence-linked "gist card" using Groq
(gpt-oss-120b by default; override with GROQ_SUMMARY_MODEL).

The card is structured JSON: TL;DR, steps, tools, verbatim snippets, insights,
warnings and "what's missing". Every item carries a short evidence quote and a
timestamp. Quotes are checked against the transcript so the UI can flag anything
the model did not take word for word from the reel.

If the structured call fails, it falls back to the original free-text summary.
"""
import json
import logging
import os
import re

import groq
from groq import Groq

from errors import ConfigError

log = logging.getLogger("reelgist")

_client = None

# Input budget in characters (not tokens). Deliberately far below the model's
# context window: free-tier tokens-per-minute limits bite long before context does.
MAX_CHARS = int(os.getenv("GROQ_SUMMARY_MAX_CHARS", "30000"))

MODEL = os.getenv("GROQ_SUMMARY_MODEL", "openai/gpt-oss-120b")

# ── Structured card ───────────────────────────────────────────────────────────

_ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence": {"type": "string"},
        "t": {"type": ["number", "null"]},
    },
    "required": ["text", "evidence", "t"],
    "additionalProperties": False,
}

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "steps": {"type": "array", "items": _ITEM},
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "what": {"type": "string"},
                    "evidence": {"type": "string"},
                    "t": {"type": ["number", "null"]},
                },
                "required": ["name", "what", "evidence", "t"],
                "additionalProperties": False,
            },
        },
        "snippets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["prompt", "command", "code", "setting", "url", "other"]},
                    "text": {"type": "string"},
                    "t": {"type": ["number", "null"]},
                },
                "required": ["kind", "text", "t"],
                "additionalProperties": False,
            },
        },
        "insights": {"type": "array", "items": _ITEM},
        "warnings": {"type": "array", "items": _ITEM},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tldr", "steps", "tools", "snippets", "insights", "warnings", "missing"],
    "additionalProperties": False,
}

CARD_PROMPT = """You extract trustworthy, actionable knowledge from short tech videos (Instagram Reels).

The transcript is given as lines "[<start seconds>] <text>". It may be in English, Hindi, or a mix (Hinglish).

Fill the JSON fields:
- tldr: one sentence saying what the viewer will learn.
- steps: actions the viewer should take, in order.
- tools: every named tool, product, model, library or website, with a short "what" (what it is used for here).
- snippets: prompts, commands, code, settings or URLs that are SPOKEN verbatim. Copy them exactly as said.
- insights: tips or concepts explained.
- warnings: caveats, limits or mistakes to avoid.
- missing: things the creator refers to but does NOT actually give, e.g. "says to comment a keyword to get the prompt, but the prompt is never said", "mentions settings shown on screen", "link in bio". Empty list if nothing is missing.

Rules:
- The transcript is the COMPLETE reel. Never say or imply it is truncated or cut off (unless it ends with "[...transcript truncated for length]").
- Be thorough: extract every instruction the speaker gives ("go to", "use", "install", "open", "give it", "type") as a step, and every named product as a tool. Short reels still usually have steps and tools.
- Only include what the speaker actually says. Never invent steps, tools or details. Empty lists are fine only when the reel truly has none.
- "missing" is about what the CREATOR withholds or only shows visually, not about the transcript.
- Write tldr, text, what and missing in clear English, even when the transcript is Hindi or mixed.
- evidence: copy a short exact phrase (3 to 15 words) from ONE transcript line that supports the item, in the transcript's original language and script. Never translate, fix or paraphrase evidence.
- t: the start time of the line the evidence comes from, or null if unsure.
- Keep each text and what under 25 words. Preserve technical names exactly as spoken.
"""

# ── Legacy free-text summary (fallback) ───────────────────────────────────────

SYSTEM_PROMPT = """You are an expert at extracting actionable knowledge from spoken content (Instagram Reels, tutorials, demos).

Given a video transcript, produce a structured summary with EXACTLY this format:

**TL;DR:** One punchy sentence summarising the whole video.

**Key Points:**
✅ [Step/action the viewer should take]
💡 [Insight, tip, or concept explained]
⚠️ [Warning, caveat, or common mistake to avoid]
(use whichever emoji fits each point — aim for 5–10 bullet points)

**Tools & Resources Mentioned:**
• [tool/link/name] — brief description
(omit this section if nothing specific was mentioned)

Rules:
- Be specific and concrete, not vague
- Keep each bullet to 1–2 lines max
- Use plain text only, no extra markdown headers
- Preserve technical terms exactly as spoken
- Write in English, even if the transcript is in Hindi or another language
"""

_LIMITS = {"steps": 12, "tools": 15, "snippets": 10, "insights": 8, "warnings": 6, "missing": 6}


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "gsk_your_key_here":
            raise ConfigError(
                "GROQ_API_KEY not set. Add it to backend/.env"
            )
        _client = Groq(api_key=api_key)
    return _client


# ── Evidence verification ─────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace (applied to both sides)."""
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _valid_t(t, max_t: float):
    if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= max_t:
        return round(float(t), 1)
    return None


def _locate(quote: str, segments: list, full_norm: str, model_t, max_t: float):
    """Return (verified, t). t comes from the matching segment when one is found."""
    q = _norm(quote)
    if not q:
        return False, _valid_t(model_t, max_t)
    if len(q) < 4:
        # Very short quotes (e.g. the prompt "hi") must match a whole word, not part of one
        pattern = re.compile(rf"(?<!\w){re.escape(q)}(?!\w)")
        found = lambda text: pattern.search(text) is not None
    else:
        found = lambda text: q in text
    for seg in segments:
        if found(_norm(seg.get("text", ""))):
            return True, round(float(seg.get("start", 0)), 1)
    if found(full_norm):  # quote spans two segments
        return True, _valid_t(model_t, max_t)
    return False, _valid_t(model_t, max_t)


def verify_card(card: dict, transcript_text: str, segments: list) -> dict:
    """Add `verified` to every quoted item and snap timestamps to real segments."""
    segments = segments or []
    full_norm = _norm(transcript_text)
    max_t = max([s.get("end", 0) for s in segments] + [0]) + 1 if segments else 10_000

    out = {"tldr": str(card.get("tldr", "")).strip()}
    for key in ("steps", "insights", "warnings"):
        items = []
        for it in (card.get(key) or [])[:_LIMITS[key]]:
            ok, t = _locate(it.get("evidence", ""), segments, full_norm, it.get("t"), max_t)
            items.append({"text": it.get("text", "").strip(), "evidence": it.get("evidence", "").strip(),
                          "t": t, "verified": ok})
        out[key] = items

    tools = []
    for it in (card.get("tools") or [])[:_LIMITS["tools"]]:
        ok, t = _locate(it.get("evidence", ""), segments, full_norm, it.get("t"), max_t)
        tools.append({"name": it.get("name", "").strip(), "what": it.get("what", "").strip(),
                      "evidence": it.get("evidence", "").strip(), "t": t, "verified": ok})
    out["tools"] = tools

    snippets = []
    for it in (card.get("snippets") or [])[:_LIMITS["snippets"]]:
        ok, t = _locate(it.get("text", ""), segments, full_norm, it.get("t"), max_t)
        snippets.append({"kind": it.get("kind", "other"), "text": it.get("text", "").strip(),
                         "t": t, "verified": ok})
    out["snippets"] = snippets

    out["missing"] = [str(m).strip() for m in (card.get("missing") or [])[:_LIMITS["missing"]] if str(m).strip()]
    return out


# ── Plain-text rendering (copy button, library preview) ───────────────────────

def _ts(t) -> str:
    return f" [{int(t // 60)}:{int(t % 60):02d}]" if isinstance(t, (int, float)) else ""


def card_to_text(card: dict) -> str:
    lines = [f"TL;DR: {card.get('tldr', '')}"]

    def block(title, rows):
        if rows:
            lines.append("")
            lines.append(title)
            lines.extend(rows)

    block("Steps:", [f"✅ {s['text']}{_ts(s.get('t'))}" for s in card.get("steps", [])])
    block("Insights:", [f"💡 {s['text']}{_ts(s.get('t'))}" for s in card.get("insights", [])])
    block("Warnings:", [f"⚠️ {s['text']}{_ts(s.get('t'))}" for s in card.get("warnings", [])])
    block("Tools & Resources:", [f"• {t['name']} — {t['what']}" for t in card.get("tools", [])])
    block("Prompts & commands (as spoken):", [f"» {s['text']}{_ts(s.get('t'))}" for s in card.get("snippets", [])])
    block("What's missing from this reel:", [f"❓ {m}" for m in card.get("missing", [])])
    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def _timestamped(transcript_text: str, segments: list) -> str:
    if segments:
        return "\n".join(f"[{s.get('start', 0):.1f}] {s.get('text', '')}" for s in segments)
    return transcript_text


def summarize(transcript_text: str, segments: list | None = None,
              translation_text: str | None = None) -> dict:
    """Return {"summary": str, "card": dict | None, "truncated": bool}."""
    if not (transcript_text or "").strip():
        return {"summary": "(No speech was detected in this reel, so there is nothing to summarize.)",
                "card": None, "truncated": False}

    client = _get_client()

    source = _timestamped(transcript_text, segments or [])
    truncated = len(source) > MAX_CHARS
    text = source[:MAX_CHARS]
    if truncated:
        text += "\n[...transcript truncated for length]"

    user = f"Transcript:\n\n{text}"
    if translation_text:
        # Helps the model understand non-English reels; evidence must still come from the original
        user += ("\n\nEnglish translation (for understanding only; never copy evidence from it):\n\n"
                 + translation_text[: MAX_CHARS // 2])

    best = None
    for attempt in (1, 2):
        try:
            chat = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": CARD_PROMPT},
                          {"role": "user", "content": user}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "gist_card", "strict": True, "schema": CARD_SCHEMA}},
                temperature=0.2,
                # Reasoning tokens count against this, and Groq counts the whole budget toward the
                # free tier's tokens-per-minute limit, so keep it modest
                max_completion_tokens=3000,
                reasoning_effort="low",
            )
            choice = chat.choices[0]
            if choice.finish_reason == "length":
                raise ValueError("card output cut off at the token limit")
            card = verify_card(json.loads(choice.message.content or ""), transcript_text, segments or [])
            if not _is_thin(card, transcript_text):
                return {"summary": card_to_text(card), "card": card, "truncated": truncated}
            # The model occasionally returns a near-empty card for a reel that clearly has content
            log.warning(f"structured card attempt {attempt} looks thin ({_richness(card)} items); retrying")
            if best is None or _richness(card) > _richness(best):
                best = card
        except groq.BadRequestError as e:
            # Groq rejects output that breaks the schema (code "json_validate_failed"); other 400s propagate
            if "json_validate_failed" not in str(e):
                raise
            log.warning(f"structured card attempt {attempt} rejected by schema validation")
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            # json.JSONDecodeError is a ValueError. Other provider/API errors propagate to the caller.
            log.warning(f"structured card attempt {attempt} failed ({e})")

    if best is not None:
        return {"summary": card_to_text(best), "card": best, "truncated": truncated}
    log.warning("falling back to free-text summary")
    return {"summary": _legacy_summary(client, text), "card": None, "truncated": truncated}


def _richness(card: dict) -> int:
    return sum(len(card.get(k, [])) for k in ("steps", "tools", "snippets", "insights", "warnings"))


def _is_thin(card: dict, transcript_text: str) -> bool:
    # A reel with a few sentences of speech should yield at least two items
    return len(transcript_text.strip()) >= 300 and _richness(card) < 2


def _legacy_summary(client, text: str) -> str:
    chat = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": f"Transcript:\n\n{text}"}],
        temperature=0.3,
        max_completion_tokens=3000,
        reasoning_effort="low",
    )
    choice = chat.choices[0]
    summary = choice.message.content or ""
    if choice.finish_reason == "length":
        summary += "\n\n⚠️ Summary was cut off (output limit reached) and may be incomplete."
    return summary
