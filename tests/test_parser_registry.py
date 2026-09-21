"""The registry must survive a missing third-party library.

A .pdf we cannot open costs one escalated email. An import that raises costs
all 520, including the 192 .txt attachments that need no library at all. That
is the failure this test exists to prevent.
"""
from __future__ import annotations

import importlib
import sys


def _reload_registry():
    """Re-import core.parsers and its adapters from scratch."""
    for name in [m for m in list(sys.modules) if m.startswith("core.parsers")]:
        del sys.modules[name]
    return importlib.import_module("core.parsers")


def test_missing_pdf_library_keeps_the_other_formats(monkeypatch):
    # A sys.modules entry of None makes `import pdfplumber` raise ImportError,
    # which is what a machine without the library does.
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    try:
        parsers = _reload_registry()
        assert ".txt" in parsers.supported()
        assert ".xlsx" in parsers.supported()
        assert ".docx" in parsers.supported()
        assert ".pdf" not in parsers.supported()
        assert parsers.MISSING_PARSERS == {"pdf": "pdfplumber"}
        # An unregistered format is an escalation, not a crash.
        assert parsers.for_path("x/y_BL.pdf") is None
    finally:
        # Without this, every later test in the run inherits a registry with
        # no PDF support.
        monkeypatch.undo()
        _reload_registry()


def test_all_four_register_normally():
    parsers = _reload_registry()
    assert parsers.supported() == (".docx", ".pdf", ".txt", ".xlsx")
    assert parsers.MISSING_PARSERS == {}
