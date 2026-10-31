"""
downloader.py — Download Instagram Reel audio.

Strategy (first success wins):
  1. yt-dlp with a static cookies.txt (INSTAREAD_COOKIES_FILE or backend/cookies.txt)
  2. yt-dlp anonymous (works for many public reels, but IG rate-limits it)
  3. yt-dlp with Firefox cookies (Chromium browsers on Windows use App-Bound
     encryption since v127, so yt-dlp can't decrypt Chrome/Edge/Brave cookies)
  4. instaloader fallback (optional dependency) — fetches the video URL directly

Returns (audio_path, title, thumbnail, uploader, duration_seconds)
"""
import os
import re
import shutil
import urllib.request
import uuid
from urllib.parse import urlparse

import yt_dlp

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COOKIES = os.path.join(BACKEND_DIR, "cookies.txt")
AUDIO_EXTS = ("mp3", "m4a", "webm", "opus", "ogg", "mp4")


class DownloadError(RuntimeError):
    """All download methods failed.

    `kind` is a coarse classification ("auth" / "rate_limit" / "unknown") and
    `hint` is a short, safe message for the client. The raw yt-dlp text stays
    in the exception message (server log only).
    """

    def __init__(self, message: str, kind: str = "unknown", hint: str = ""):
        super().__init__(message)
        self.kind = kind
        self.hint = hint or "Could not download this reel."


def classify_errors(errors: list[str]) -> tuple[str, str]:
    text = " ".join(errors).lower()
    if any(s in text for s in ("429", "rate-limit", "rate limit", "too many requests")):
        return "rate_limit", "Instagram is rate limiting requests. Wait a while and try again."
    if any(s in text for s in ("login", "log in", "cookie", "checkpoint", "not logged")):
        return "auth", "Instagram needs a fresh login. Re-export backend/cookies.txt and retry."
    return "unknown", (
        "Could not download this reel. It may be private or removed, "
        "or yt-dlp may need updating (pip install -U yt-dlp)."
    )


def _cookies_file() -> str | None:
    path = os.getenv("INSTAREAD_COOKIES_FILE") or DEFAULT_COOKIES
    return path if os.path.isfile(path) else None


def _find_output(out_template: str) -> str | None:
    for ext in AUDIO_EXTS:
        candidate = f"{out_template}.{ext}"
        if os.path.exists(candidate):
            return candidate
    return None


def _cleanup(out_template: str) -> None:
    # Remove partial files from a failed attempt so the next one starts clean
    folder, prefix = os.path.split(out_template)
    for name in os.listdir(folder):
        if name.startswith(prefix):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _ytdlp_opts(out_template: str, has_ffmpeg: bool, auth: dict) -> dict:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
        },
        **auth,
    }
    if has_ffmpeg:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "48",
        }]
        # Whisper resamples to 16 kHz mono anyway; this keeps uploads tiny
        opts["postprocessor_args"] = {"extractaudio": ["-ac", "1", "-ar", "16000"]}
    return opts


def _try_ytdlp(url: str, out_template: str, has_ffmpeg: bool):
    attempts = []
    cookies = _cookies_file()
    if cookies:
        attempts.append(("cookies.txt", {"cookiefile": cookies}))
    attempts.append(("anonymous", {}))
    attempts.append(("firefox", {"cookiesfrombrowser": ("firefox",)}))

    errors = []
    for label, auth in attempts:
        try:
            with yt_dlp.YoutubeDL(_ytdlp_opts(out_template, has_ffmpeg, auth)) as ydl:
                info = ydl.extract_info(url, download=True)
            path = _find_output(out_template)
            if path:
                return info, path
            errors.append(f"{label}: finished but no file produced")
        except Exception as e:
            errors.append(f"{label}: {str(e).strip()[:200]}")
        _cleanup(out_template)
    return None, errors


_ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
# Supported forms: /reel/<code>/, /reels/<code>/, /p/<code>/, /tv/<code>/ and
# the username-prefixed share form /<user>/reel/<code>/
_PATH_RE = re.compile(
    r"^/(?:[A-Za-z0-9_.]{1,30}/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,40})/?$"
)


def shortcode(url) -> str | None:
    """Return the post shortcode for a genuine instagram.com post URL, else None.

    Validates scheme, exact host, no credentials/port, and an anchored path,
    so look-alike hosts or 'instagram.com' hidden in a query string never match.
    """
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlparse(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if parts.username or parts.password or port is not None:
        return None
    if (parts.hostname or "").lower() not in _ALLOWED_HOSTS:
        return None
    m = _PATH_RE.match(parts.path)
    return m.group(1) if m else None


def canonical_url(url: str) -> str | None:
    """Strip tracking params (?xtok=, ?igsh=) so one reel maps to one URL."""
    code = shortcode(url)
    return f"https://www.instagram.com/reel/{code}/" if code else None


def _try_instaloader(url: str, out_template: str):
    try:
        import instaloader
    except ImportError:
        return None, ["instaloader: not installed (pip install instaloader)"]

    code = shortcode(url)
    if not code:
        return None, ["instaloader: could not parse shortcode from URL"]

    try:
        L = instaloader.Instaloader(quiet=True)
        post = instaloader.Post.from_shortcode(L.context, code)
        if not post.is_video:
            return None, ["instaloader: post is not a video"]
        path = out_template + ".mp4"
        req = urllib.request.Request(post.video_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as f:
            shutil.copyfileobj(resp, f)
        info = {
            "title": (post.caption or "")[:80],
            "thumbnail": post.url,
            "uploader": post.owner_username,
            "duration": int(post.video_duration or 0),
        }
        return info, path
    except Exception as e:
        _cleanup(out_template)
        return None, [f"instaloader: {str(e).strip()[:200]}"]


def download_reel_audio(url: str, output_dir: str = None) -> tuple:
    # Defence in depth: only ever hand a canonical Instagram URL to yt-dlp
    url = canonical_url(url)
    if url is None:
        raise ValueError("Not a valid Instagram reel URL.")
    if output_dir is None:
        output_dir = os.path.normpath(os.path.join(BACKEND_DIR, "..", "downloads"))
    os.makedirs(output_dir, exist_ok=True)
    out_template = os.path.join(output_dir, str(uuid.uuid4()))
    has_ffmpeg = shutil.which("ffmpeg") is not None

    info, result = _try_ytdlp(url, out_template, has_ffmpeg)
    errors = [] if info else result
    if info is None:
        info, result = _try_instaloader(url, out_template)
        if info is None:
            errors += result

    if info is None:
        kind, hint = classify_errors(errors)
        if kind != "rate_limit" and not _cookies_file():
            hint += (" Tip: export Instagram cookies to backend/cookies.txt "
                     "(e.g. with the 'Get cookies.txt LOCALLY' extension).")
        raise DownloadError(
            "All download methods failed:\n  " + "\n  ".join(errors), kind, hint
        )

    audio_path = result
    # yt-dlp's IG title is a generic "Video by <user>"; the caption is more useful
    caption   = (info.get("description") or "").strip().split("\n")[0][:80]
    title     = caption or info.get("title") or "Instagram Reel"
    thumbnail = info.get("thumbnail") or ""
    uploader  = info.get("uploader") or info.get("channel") or "Unknown"
    duration  = info.get("duration") or 0

    return audio_path, title, thumbnail, uploader, duration
