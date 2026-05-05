"""Tests for ``core.crash_reporter``.

These run under the ``qt`` tier because the module installs a
``qInstallMessageHandler`` and uses ``QtMsgType``. The actual dialog is
replaced with a stub so no window is ever shown.
"""

from __future__ import annotations

import json
import sys

import pytest

from core import crash_reporter


@pytest.fixture
def fresh_reporter(tmp_path, monkeypatch):
    """Reset module state and redirect ``logs/`` to ``tmp_path``."""
    monkeypatch.setattr(crash_reporter, "_LOGS_DIR", tmp_path)

    captured: dict = {"calls": []}

    def stub_dialog(report, path):
        captured["calls"].append((report, path))

    crash_reporter.reset_for_tests()
    crash_reporter.install(
        active_profile_provider=lambda: "TestProfile",
        dialog_factory=stub_dialog,
    )

    yield captured, tmp_path

    crash_reporter.reset_for_tests()


def test_install_is_idempotent(fresh_reporter):
    captured, _ = fresh_reporter
    assert crash_reporter.is_installed()
    # Second install should not stack handlers or replace the factory.
    crash_reporter.install(dialog_factory=lambda r, p: captured["calls"].append(("would_replace",)))
    assert crash_reporter.is_installed()


def test_build_report_shape(fresh_reporter):
    report = crash_reporter.build_report("test_kind", "boom")
    assert report["crash_type"] == "test_kind"
    assert report["detail"] == "boom"
    assert report["active_profile"] == "TestProfile"
    assert report["version"]
    assert report["python"]
    assert report["platform"]
    assert isinstance(report["log_tail"], list)


def test_uncaught_exception_writes_report_and_calls_dialog(fresh_reporter):
    captured, logs_dir = fresh_reporter
    try:
        raise RuntimeError("simulated crash")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    crash_files = list(logs_dir.glob("crash_*.json"))
    assert len(crash_files) == 1
    body = json.loads(crash_files[0].read_text(encoding="utf-8"))
    assert body["crash_type"] == "uncaught_exception"
    assert "RuntimeError: simulated crash" in body["detail"]

    assert len(captured["calls"]) == 1
    report, path = captured["calls"][0]
    assert report["crash_type"] == "uncaught_exception"
    assert path == crash_files[0]


def test_keyboard_interrupt_is_passed_to_default_hook(fresh_reporter, monkeypatch):
    """Ctrl+C must keep working — the user expects KeyboardInterrupt to surface."""
    captured, logs_dir = fresh_reporter

    saw: list = []
    monkeypatch.setattr(sys, "__excepthook__", lambda t, v, tb: saw.append(t))

    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())

    assert saw == [KeyboardInterrupt]
    assert not list(logs_dir.glob("crash_*.json"))
    assert captured["calls"] == []


def test_report_subprocess_crash_writes_report_without_dialog(fresh_reporter):
    captured, logs_dir = fresh_reporter
    path = crash_reporter.report_subprocess_crash(
        "Blender", "exe missing", show_dialog=False,
    )
    assert path.exists()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["crash_type"] == "subprocess:Blender"
    assert body["detail"] == "exe missing"
    # show_dialog=False — stub must NOT have been called.
    assert captured["calls"] == []


def test_report_subprocess_crash_with_dialog(fresh_reporter):
    captured, _ = fresh_reporter
    crash_reporter.report_subprocess_crash("CUE4Parse", "could not start")
    assert len(captured["calls"]) == 1
    report, _ = captured["calls"][0]
    assert report["crash_type"] == "subprocess:CUE4Parse"


def test_github_issue_url_is_well_formed(fresh_reporter):
    report = crash_reporter.build_report("uncaught_exception", "Traceback (most recent call last):\n…")
    url = crash_reporter.github_issue_url(report)
    assert url.startswith("https://github.com/exterminathan/EfficientAssetRipper/issues/new?")
    # GitHub silently caps URLs; we must keep ours under the limit.
    assert len(url) <= 7500
    # Title should be in the URL.
    assert "Crash%3A+uncaught_exception" in url or "Crash%3A%20uncaught_exception" in url


def test_github_issue_url_truncates_long_bodies(fresh_reporter):
    long_detail = "x" * 50_000
    report = crash_reporter.build_report("oversized", long_detail)
    url = crash_reporter.github_issue_url(report)
    assert len(url) <= 7500
    assert "Body+truncated" in url or "Body%20truncated" in url


def test_log_tail_includes_recent_messages(fresh_reporter, caplog):
    import logging
    caplog.set_level(logging.INFO)
    logging.getLogger("ear.testing").info("first log line for tail check")
    logging.getLogger("ear.testing").error("second log line for tail check")

    report = crash_reporter.build_report("uncaught_exception", "boom")
    tail = "\n".join(report["log_tail"])
    assert "first log line for tail check" in tail
    assert "second log line for tail check" in tail


def test_dialog_factory_failure_is_swallowed(fresh_reporter):
    """A buggy dialog must not loop the excepthook."""
    captured, _ = fresh_reporter

    def boom_dialog(report, path):
        raise RuntimeError("dialog blew up")

    # Reinstall with the buggy factory
    crash_reporter.reset_for_tests()
    crash_reporter.install(dialog_factory=boom_dialog)

    # Should NOT raise — _safe_show swallows exceptions from the factory.
    try:
        raise ValueError("kaboom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    # If we get here without exception, behaviour is correct.
