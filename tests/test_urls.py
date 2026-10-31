import pytest

from downloader import shortcode, canonical_url, classify_errors

CODE = "De2eDNFtpfm"


@pytest.mark.parametrize("url", [
    f"https://www.instagram.com/reel/{CODE}/",
    f"https://www.instagram.com/reel/{CODE}",
    f"https://instagram.com/reel/{CODE}/?xtok=abc==",
    f"https://www.instagram.com/reel/{CODE}/?igsh=xyz&utm_source=ig_web_copy_link",
    f"https://www.instagram.com/reels/{CODE}/",
    f"https://www.instagram.com/p/{CODE}/",
    f"https://www.instagram.com/tv/{CODE}/",
    f"https://www.instagram.com/some.user_1/reel/{CODE}/",
    f"http://m.instagram.com/reel/{CODE}/",
    f"  https://www.instagram.com/reel/{CODE}/  ",
])
def test_valid_urls(url):
    assert shortcode(url) == CODE
    assert canonical_url(url) == f"https://www.instagram.com/reel/{CODE}/"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9999/audio.mp3?tag=instagram.com",
    "https://evil.example/?u=instagram.com/reel/De2eDNFtpfm/",
    f"https://notinstagram.com/reel/{CODE}/",
    f"https://instagram.com.evil.example/reel/{CODE}/",
    f"https://www.instagram.com@evil.example/reel/{CODE}/",
    f"https://user:pw@www.instagram.com/reel/{CODE}/",
    f"https://www.instagram.com:8443/reel/{CODE}/",
    f"ftp://www.instagram.com/reel/{CODE}/",
    f"file:///etc/passwd#instagram.com/reel/{CODE}/",
    "https://www.instagram.com/",
    "https://www.instagram.com/someuser/",
    f"https://www.instagram.com/reel/{CODE}/extra/path",
    "https://www.instagram.com/reel/",
    "",
    "not a url",
    None,
    123,
    "https://www.instagram.com/reel/" + "a" * 3000,
])
def test_invalid_urls(url):
    assert shortcode(url) is None
    assert canonical_url(url) is None


def test_classify_errors():
    assert classify_errors(["anonymous: HTTP Error 429: Too Many Requests"])[0] == "rate_limit"
    assert classify_errors(["cookies.txt: login required"])[0] == "auth"
    assert classify_errors(["anonymous: something odd"])[0] == "unknown"
    # The hint is safe user text, not the raw error
    assert "something odd" not in classify_errors(["anonymous: something odd"])[1]
