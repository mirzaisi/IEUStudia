"""The programs IEUStudia starts and waits for run at a normal program's priority, also while IEUStudia transcribes
below normal: started at that priority on a busy PC, nvidia-smi didn't answer in 20 s, "claude --version" in 60 s and
Edge's print of a PDF failed after 180 s (owner's laptop, 12 Nov 2026)."""

import subprocess
import sys

import pytest

from studia import ai, ai_setup, export, hardware

NORMAL = 0x20 if sys.platform == "win32" else 0


@pytest.mark.parametrize("mod", [ai, ai_setup, hardware])
def test_the_flags_ask_for_a_normal_priority_and_no_window(mod):
    assert mod._NO_WINDOW & NORMAL == NORMAL
    assert mod._NO_WINDOW & getattr(subprocess, "CREATE_NO_WINDOW", 0) == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_edge_prints_at_a_normal_priority(monkeypatch, tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        (tmp_path / "p.pdf").write_bytes(b"%PDF")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(export, "find_browser", lambda: "edge")
    monkeypatch.setattr(export.subprocess, "run", run)
    page = tmp_path / "p.html"
    page.write_text("<p>x</p>", encoding="utf-8")
    assert export.html_to_pdf(page, tmp_path / "p.pdf")
    assert seen["creationflags"] & NORMAL == NORMAL


def test_the_card_check_asks_nvidia_smi_at_a_normal_priority(monkeypatch):
    from studia.engine import models

    seen = []

    def run(cmd, **kw):
        seen.append(kw.get("creationflags", 0))
        return subprocess.CompletedProcess(cmd, 0, stdout="NVIDIA Test, 8188\n")

    monkeypatch.setattr(models.subprocess, "run", run)
    models.detect_gpu.cache_clear()
    try:
        assert models.detect_gpu(quick=True)["name"] == "NVIDIA Test"
    finally:
        models.detect_gpu.cache_clear()
    monkeypatch.setattr(hardware.subprocess, "run", run)
    hardware.nvidia_gpu()
    assert seen and all(f & NORMAL == NORMAL for f in seen)
