import os
import subprocess
from types import SimpleNamespace

import pytest

import summarizer
import transcriber


# ── transcriber: compression cleanup ─────────────────────────────────────────

@pytest.fixture()
def big_audio(tmp_path, monkeypatch):
    p = tmp_path / "clip.m4a"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(transcriber, "MAX_UPLOAD_BYTES", 10)  # force the shrink path
    monkeypatch.setattr(transcriber.shutil, "which", lambda n: "ffmpeg")
    return p


def test_failed_ffmpeg_removes_partial_output(big_audio, monkeypatch):
    def fake_run(cmd, **kw):
        open(cmd[-1], "wb").write(b"partial")  # ffmpeg created output, then failed
        return SimpleNamespace(returncode=1, stderr="line1\nboom: invalid data")
    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="invalid data"):
        transcriber._shrink_if_needed(str(big_audio))
    assert not os.path.exists(str(big_audio).replace(".m4a", ".small.mp3"))


def test_still_too_big_after_compression_cleans_up(big_audio, monkeypatch):
    def fake_run(cmd, **kw):
        open(cmd[-1], "wb").write(b"y" * 50)  # still > 10 bytes
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="too long"):
        transcriber._shrink_if_needed(str(big_audio))
    assert not os.path.exists(str(big_audio).replace(".m4a", ".small.mp3"))


def test_small_file_is_untouched(tmp_path):
    p = tmp_path / "ok.m4a"
    p.write_bytes(b"x")
    assert transcriber._shrink_if_needed(str(p)) == (str(p), False)


def test_ffmpeg_timeout_cleans_up(big_audio, monkeypatch):
    def fake_run(cmd, **kw):
        open(cmd[-1], "wb").write(b"partial")
        raise subprocess.TimeoutExpired(cmd, 300)
    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        transcriber._shrink_if_needed(str(big_audio))
    assert not os.path.exists(str(big_audio).replace(".m4a", ".small.mp3"))


