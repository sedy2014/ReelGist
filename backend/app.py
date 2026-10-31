"""
app.py — ReelGist Flask backend API server.

Endpoints:
  POST /api/process        — Download, transcribe, summarize a reel
  GET  /api/library        — List all saved reels
  DELETE /api/library/<id> — Delete a reel from the library
  GET  /api/health         — Health check
"""
import os
import sys
import time
import logging
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import groq

# Load .env from this folder
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from downloader import download_reel_audio, shortcode, canonical_url, DownloadError
from errors import ConfigError
from transcriber import transcribe
from summarizer import summarize
from database import (init_db, save_reel, get_all_reels,
                      get_reel_by_shortcode, delete_reel)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reelgist")

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://127.0.0.1:8080"])

init_db()


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "version": "1.0.0"})


def _error(message: str, status: int, **extra):
    return jsonify({"error": message, **extra}), status


# Shortcodes currently being processed (guards against duplicate expensive runs)
_inflight = set()
_inflight_lock = threading.Lock()


@app.route("/api/process", methods=["POST"])
def process_reel():
    t0 = time.monotonic()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("url"), str):
        return _error("Request body must be JSON like {\"url\": \"<instagram reel url>\"}.", 400)

    raw_url = data["url"].strip()
    code = shortcode(raw_url)
    if not code:
        return _error("Please provide a valid instagram.com reel or post URL.", 400)
    url = canonical_url(raw_url)

    # Already processed? Match on shortcode (share links carry changing ?xtok= params)
    existing = get_reel_by_shortcode(code)
    if existing:
        log.info(f"[{code}] cached ({time.monotonic() - t0:.1f}s)")
        return jsonify({**existing, "cached": True})

    with _inflight_lock:
        if code in _inflight:
            return _error("This reel is already being processed. Please wait.", 409)
        _inflight.add(code)

    audio_path = None
    try:
        # ── Step 1: Download ──────────────────────────────────────────────
        log.info(f"[{code}] downloading")
        audio_path, title, thumbnail, uploader, duration = download_reel_audio(url)
        log.info(f"[{code}] downloaded {duration}s '{title}'")

        # ── Step 2: Transcribe ────────────────────────────────────────────
        trans_result = transcribe(audio_path)
        transcript_text = trans_result["text"]
        segments = trans_result["segments"]
        language = trans_result.get("language")
        translation = trans_result.get("translation") or {}
        log.info(f"[{code}] transcribed {len(transcript_text)} chars, {len(segments)} segments, "
                 f"language={language}, translated={bool(translation)}")

        # ── Step 3: Summarize into a gist card ────────────────────────────
        result = summarize(transcript_text, segments, translation.get("text"))
        summary, card, truncated = result["summary"], result["card"], result["truncated"]

        # ── Step 4: Persist ───────────────────────────────────────────────
        save_reel(url, title, uploader, thumbnail, duration,
                  transcript_text, segments, summary, truncated,
                  card=card, language=language,
                  transcript_en=translation.get("text"), segments_en=translation.get("segments"))
        log.info(f"[{code}] ok ({time.monotonic() - t0:.1f}s, card={card is not None}, truncated={truncated})")

        return jsonify({
            "url": url,
            "title": title,
            "uploader": uploader,
            "thumbnail": thumbnail,
            "duration": duration,
            "transcript": transcript_text,
            "segments": segments,
            "language": language,
            "transcript_en": translation.get("text"),
            "segments_en": translation.get("segments") or [],
            "summary": summary,
            "card": card,
            "truncated": truncated,
            "cached": False,
        })

    except ValueError as e:
        log.warning(f"[{code}] failed: invalid input: {e}")
        return _error(str(e), 400)

    except DownloadError as e:
        log.warning(f"[{code}] failed: download ({e.kind}): {e}")
        status = 429 if e.kind == "rate_limit" else 502
        return _error(e.hint, status, kind=e.kind)

    except ConfigError as e:
        log.error(f"[{code}] failed: configuration: {e}")
        return _error(str(e), 500, kind="config")

    except groq.RateLimitError as e:
        log.warning(f"[{code}] failed: Groq rate limit: {e}")
        return _error("The AI service is rate limiting requests. Try again shortly.", 429, kind="rate_limit")

    except groq.APITimeoutError as e:
        log.warning(f"[{code}] failed: Groq timeout: {e}")
        return _error("The AI service timed out. Please try again.", 504, kind="timeout")

    except (groq.APIConnectionError, groq.APIStatusError) as e:
        log.warning(f"[{code}] failed: Groq error: {e}")
        return _error("The AI service returned an error. Please try again.", 502, kind="upstream")

    except Exception:
        log.exception(f"[{code}] failed: internal error")
        return _error("Internal error while processing this reel. See the server log.", 500, kind="internal")

    finally:
        with _inflight_lock:
            _inflight.discard(code)
        # Always clean up the temp audio file
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass


@app.route("/api/library", methods=["GET"])
def library():
    return jsonify(get_all_reels())


@app.route("/api/library/<int:reel_id>", methods=["DELETE"])
def delete_from_library(reel_id: int):
    delete_reel(reel_id)
    return jsonify({"deleted": reel_id})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    log.info(f"🚀 ReelGist backend running on http://localhost:{port}")
    log.info("   Make sure GROQ_API_KEY is set in backend/.env")
    app.run(host="127.0.0.1", port=port, debug=False)

