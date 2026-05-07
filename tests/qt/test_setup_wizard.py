"""Tests for `gui.setup_wizard.SetupWizard`.

Mocks the dependency probes so the test doesn't depend on Blender / .NET /
Everything being installed on the runner.
"""

from __future__ import annotations

import pytest

import config
from gui import setup_wizard
from gui.setup_wizard import SetupWizard, should_show_setup

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def patched_probes(monkeypatch):
    """Force all three probes to report 'found' so the page renders cleanly."""
    monkeypatch.setattr(setup_wizard, "detect_blender", lambda: (True, r"C:\Blender\blender.exe"))
    monkeypatch.setattr(setup_wizard, "detect_everything", lambda: (True, r"C:\Everything\Everything64.dll"))
    monkeypatch.setattr(setup_wizard, "detect_dotnet", lambda: (True, ".NET 8.0.0"))
    yield


def test_should_show_setup_when_unset(mock_qsettings):
    assert should_show_setup() is True


def test_should_show_setup_false_when_complete(mock_qsettings):
    config.set("setup_complete", "1")
    assert should_show_setup() is False


def test_wizard_constructs(qtbot, mock_qsettings, patched_probes):
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    # 4 pages: Welcome, Dependency, Profile, Done
    assert len(wiz.pageIds()) == 4


def test_wizard_navigates_forward(qtbot, mock_qsettings, patched_probes):
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    wiz.show()  # required: QWizard.next() needs show() to initialize page machinery
    start = wiz.currentId()
    wiz.next()
    assert wiz.currentId() != start, "next() should advance from welcome page"


def test_wizard_back_button_returns(qtbot, mock_qsettings, patched_probes):
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    wiz.show()  # required: QWizard.next()/back() need show() to initialize page machinery
    welcome_id = wiz.currentId()
    wiz.next()
    second_id = wiz.currentId()
    wiz.back()
    assert wiz.currentId() == welcome_id
    assert second_id != welcome_id


def test_dependency_page_rechecks_and_persists_blender(qtbot, mock_qsettings, monkeypatch):
    """If the probe finds Blender, the wizard should persist it to config."""
    monkeypatch.setattr(setup_wizard, "detect_blender", lambda: (True, r"C:\B\blender.exe"))
    monkeypatch.setattr(setup_wizard, "detect_everything", lambda: (False, "missing"))
    monkeypatch.setattr(setup_wizard, "detect_dotnet", lambda: (False, "missing"))

    config.set("blender_exe", "")  # ensure unset

    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    wiz.show()  # required: QWizard.next() needs show() to initialize page machinery
    wiz.next()  # advance to dependency page → triggers initializePage → recheck

    assert config.get("blender_exe") == r"C:\B\blender.exe"


def test_skip_button_does_not_set_setup_complete(qtbot, mock_qsettings, patched_probes):
    """The Skip Setup custom button rejects without flipping setup_complete."""
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    wiz.show()  # required: QWizard.next() needs show() to initialize page machinery
    skipped = []
    wiz.skipped.connect(lambda: skipped.append(True))

    from PySide6.QtWidgets import QWizard
    wiz._on_custom_button(QWizard.WizardButton.CustomButton1)

    assert skipped == [True]
    assert config.get("setup_complete") != "1"


def test_finish_sets_setup_complete(qtbot, mock_qsettings, patched_probes, tmp_path):
    """Calling accept() (Finish) must flip setup_complete='1'."""
    wiz = SetupWizard()
    qtbot.addWidget(wiz)

    completed = []
    wiz.completed.connect(lambda: completed.append(True))

    # Pre-fill the picker pages so validatePage doesn't reject anything.
    # Skip directly to accept(), which is what the Finish button calls.
    wiz.accept()

    assert config.get("setup_complete") == "1"
    assert completed == [True]


def test_profile_page_uses_first_run_copy_when_setup_incomplete(
    qtbot, mock_qsettings, patched_probes
):
    """Genuine first run → page title is 'Set up your first profile'."""
    config.set("setup_complete", "")
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    # Profile page is the third page (id 2): Welcome=0, Dependency=1, Profile=2.
    profile_page = wiz.page(wiz.pageIds()[2])
    assert profile_page.title() == "Set up your first profile"


def test_profile_page_uses_rerun_copy_when_setup_already_complete(
    qtbot, mock_qsettings, patched_probes
):
    """Re-run from Help → page title is 'Profiles', no first-run framing."""
    config.set("setup_complete", "1")
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    profile_page = wiz.page(wiz.pageIds()[2])
    assert profile_page.title() == "Profiles"


def test_profile_page_button_sets_flag_and_accepts(
    qtbot, mock_qsettings, patched_probes
):
    """Open-Profile-Manager button flags the wizard + accepts (no nested modal)."""
    wiz = SetupWizard()
    qtbot.addWidget(wiz)
    profile_page = wiz.page(wiz.pageIds()[2])

    accepted = []
    wiz.accepted.connect(lambda: accepted.append(True))

    profile_page._open_profile_manager()

    assert wiz.open_profile_manager_after is True
    assert accepted == [True]


def test_main_window_does_not_show_wizard_when_setup_complete(
    qtbot, mock_qsettings, tmp_profiles_dir, monkeypatch
):
    """Regression: setup_complete=1 must skip the wizard entirely."""
    config.set("setup_complete", "1")

    constructed = []
    real_init = SetupWizard.__init__

    def _spy(self, *a, **kw):
        constructed.append(True)
        real_init(self, *a, **kw)

    monkeypatch.setattr(SetupWizard, "__init__", _spy)

    from gui.main_window import MainWindow
    win = MainWindow()
    qtbot.addWidget(win)

    assert constructed == [], "wizard should not be constructed when setup_complete=1"
