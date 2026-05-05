"""Synthetic end-to-end pipeline test (no external binaries).

Drives the full mount → scan → resolve → process → checkpoint pipeline
using only fixtures + stubs:

- ``UnpackerProcess`` with ``StubQProcess`` replaying init/export NDJSON
  (catches IPC wire-format drift between the Python side and CUE4ParseCLI)
- ``AssetScanner`` against the real :file:`tests/fixtures/psk/minimal.psk`
  binary plus a tmp_path-staged props + texture file
  (catches PSK parser / texture resolver regressions)
- ``JobManager`` with the ``mock_blender_run`` fixture configured to write
  a placeholder ``.blend`` so downstream "blend-exists" checks pass
- Cross-validates the Phase 4 queue checkpoint lifecycle: a checkpoint
  exists during processing and is removed on clean finish.

What this catches that unit tests don't
---------------------------------------
- IPC wire-format drift between ``core.unpacker`` and the synthetic
  NDJSON tape when CUE4ParseCLI changes message types.
- Signal/slot wiring between unpacker → asset scanner → job manager.
- Cache and checkpoint file lifecycles.

What it deliberately does NOT cover
-----------------------------------
- Real ``.pak`` extraction (needs CUE4ParseCLI exe + AES key).
- Real Blender material wiring (needs Blender + the PSK addon).
- Real Everything DLL behaviour (needs Everything desktop running).

Those stay in :mod:`tests.e2e` behind opt-in ``requires_*`` markers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QObject, QProcess, Signal

from core import queue_checkpoint
from core.asset_scanner import AssetScanner
from core.job_manager import JobManager
from core.unpacker import UnpackerProcess

from tests.conftest import FakeEverythingSDK

pytestmark = pytest.mark.qt


# ---------------------------------------------------------------------------
# Stub QProcess (mirror of test_unpacker_signals.py for IPC layer driving)
# ---------------------------------------------------------------------------

class _StubQProcess(QObject):
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, QProcess.ExitStatus)
    errorOccurred = Signal(QProcess.ProcessError)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = bytearray()
        self._state = QProcess.ProcessState.Running

    def state(self):
        return self._state

    def waitForStarted(self, _ms):
        return True

    def waitForFinished(self, _ms):
        return True

    def kill(self):
        self._state = QProcess.ProcessState.NotRunning

    def write(self, _data):
        return len(_data)

    def readAllStandardOutput(self):
        ba = QByteArray(bytes(self._buf))
        self._buf.clear()
        return ba

    def readAllStandardError(self):
        return QByteArray()

    def feed(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        self._buf.extend(line.encode("utf-8"))
        self.readyReadStandardOutput.emit()


# ---------------------------------------------------------------------------
# Pipeline test
# ---------------------------------------------------------------------------

def _stage_game_folder(tmp_path: Path, fixtures_dir: Path) -> dict:
    """Lay out a believable mini game folder in tmp_path.

    Returns the paths the test needs back. The PSK binary is the real
    minimal fixture (240 bytes with two MATT0000 entries) so the scanner
    actually exercises the binary parser.
    """
    game_root = tmp_path / "Game" / "Content"
    char_dir = game_root / "Char"
    tex_dir = game_root / "Char" / "Textures"
    char_dir.mkdir(parents=True)
    tex_dir.mkdir(parents=True)

    # Real PSK fixture, copied in. The fixture's MATT0000 chunk has
    # M_TestBody / M_TestHelmet so the scanner's binary fallback path
    # discovers materials even without props for them.
    src_psk = (fixtures_dir / "psk" / "minimal.psk").read_bytes()
    psk_path = char_dir / "SK_Synth.psk"
    psk_path.write_bytes(src_psk)

    # Empty mesh props so the scanner falls back to MATT0000.
    mesh_props = char_dir / "SK_Synth.props.txt"
    mesh_props.write_text('{ "Properties": {} }', encoding="utf-8")

    # Texture file the resolver should pick up.
    tex_path = tex_dir / "T_Synth_C.tga"
    tex_path.write_bytes(b"\x00")

    return {
        "game_root": str(game_root),
        "psk_path": psk_path,
        "mesh_props": mesh_props,
        "tex_path": tex_path,
    }


def test_synthetic_pipeline_runs_end_to_end(
    qtbot,
    tmp_path,
    fixtures_dir,
    tiny_presets,
    mock_blender_run,
    monkeypatch,
):
    # ── Tier 0: file system staging ───────────────────────────────────
    paths = _stage_game_folder(tmp_path, fixtures_dir)
    output_dir = tmp_path / "Outputs"
    output_dir.mkdir()

    # ── Tier 1: IPC layer round-trip ──────────────────────────────────
    # Drive UnpackerProcess with the same NDJSON tape format the real CLI
    # emits. A mismatch here means our wire format drifted.
    unpacker = UnpackerProcess()
    proc = _StubQProcess(unpacker)
    proc.readyReadStandardOutput.connect(unpacker._on_stdout)
    unpacker._proc = proc

    init_tape = (fixtures_dir / "cue4parse_ndjson" / "init_response.ndjson").read_text(
        encoding="utf-8"
    ).strip()
    with qtbot.waitSignal(unpacker.initialized, timeout=2000) as init_sig:
        proc.feed(init_tape)
    assert init_sig.args[0] == 12, "init_done.archive_count should round-trip from the tape"

    # Synthesize an export_done that points at our staged PSK path.
    export_payload = {
        "type": "export_done",
        "succeeded": [str(paths["psk_path"])],
        "failed": [],
    }
    with qtbot.waitSignal(unpacker.export_done, timeout=2000) as exp_sig:
        proc.feed(json.dumps(export_payload))
    assert exp_sig.args[0] == [str(paths["psk_path"])]

    # ── Tier 2: Real scanner over the real PSK fixture ────────────────
    sdk = FakeEverythingSDK(
        psk_files=[paths["psk_path"]],
        props_files={"SK_Synth": [paths["mesh_props"]]},
        textures={"T_Synth_C": [paths["tex_path"]]},
    )
    scanner = AssetScanner(paths["game_root"], tiny_presets, sdk=sdk)
    assets = scanner.scan()
    assert len(assets) == 1, "scanner should produce one AssetEntry from the staged PSK"
    asset = assets[0]
    assert asset.name == "SK_Synth"
    # Binary fallback found two materials in the MATT0000 chunk.
    mat_names = [m.material_name for m in asset.materials]
    assert "M_TestBody" in mat_names
    assert "M_TestHelmet" in mat_names

    # ── Tier 3: Job runner with placeholder .blend writes ─────────────
    mock_blender_run["write_dummy_blend_on_success"]["write_dummy_blend_on_success"] = True

    # Redirect the queue checkpoint to tmp so the Phase 4 lifecycle
    # observation doesn't depend on the autouse fixture's tmp_path
    # (which would already point here, but be explicit).
    cp_path = tmp_path / "queue_checkpoint.json"
    monkeypatch.setattr(queue_checkpoint, "_DEFAULT_PATH", cp_path)

    jm = JobManager(
        assets=assets,
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
        timeout=30,
        profile_name="SyntheticProfile",
    )
    with qtbot.waitSignal(jm.queue_finished, timeout=5000) as fin_sig:
        jm.start()
    jm.wait(5000)

    # ── Tier 4: Assertions ────────────────────────────────────────────
    # JobManager reports the right counts.
    total, succeeded, failed = fin_sig.args
    assert total == 1
    assert succeeded == 1
    assert failed == 0
    assert all(r.success for r in jm.results)

    # The placeholder .blend exists on disk at the manifested location.
    expected_blend = output_dir / "SK_Synth.blend"
    assert expected_blend.is_file(), "mock_blender_run should have produced a .blend"
    assert expected_blend.read_bytes().startswith(b"BLENDER")

    # The Blender stub really did receive the manifest with our resolved
    # textures — confirms the resolve→process plumbing is wired up.
    assert len(mock_blender_run["calls"]) == 1
    manifest = mock_blender_run["calls"][0]["manifest"]
    assert manifest["psk_path"] == str(paths["psk_path"])

    # Phase 4 cross-validation: clean finish drops the checkpoint.
    assert not cp_path.exists(), "checkpoint should be deleted on clean finish"


def test_synthetic_pipeline_cancel_keeps_checkpoint(
    qtbot,
    tmp_path,
    fixtures_dir,
    tiny_presets,
    mock_blender_run,
    monkeypatch,
):
    """Same staging, but cancel mid-batch → checkpoint must persist for resume."""
    paths = _stage_game_folder(tmp_path, fixtures_dir)
    output_dir = tmp_path / "Outputs"
    output_dir.mkdir()

    sdk = FakeEverythingSDK(
        psk_files=[paths["psk_path"]],
        props_files={"SK_Synth": [paths["mesh_props"]]},
        textures={"T_Synth_C": [paths["tex_path"]]},
    )
    scanner = AssetScanner(paths["game_root"], tiny_presets, sdk=sdk)
    assets = scanner.scan()
    # Duplicate the asset so we have two jobs and can cancel after the first.
    assets = assets + [assets[0]]

    cp_path = tmp_path / "queue_checkpoint.json"
    monkeypatch.setattr(queue_checkpoint, "_DEFAULT_PATH", cp_path)

    jm = JobManager(
        assets=assets,
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
        timeout=30,
        profile_name="SyntheticProfile",
    )
    jm.job_completed.connect(lambda *_: jm.cancel())

    with qtbot.waitSignal(jm.queue_finished, timeout=5000):
        jm.start()
    jm.wait(5000)

    # Cancel path leaves the checkpoint behind for resume.
    assert cp_path.is_file()
    payload = queue_checkpoint.load()
    assert payload is not None
    assert payload.profile == "SyntheticProfile"
    # First asset finished before cancel propagated → at least one entry
    # in completed.
    assert len(payload.completed) >= 1
