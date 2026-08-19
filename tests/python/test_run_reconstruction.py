"""Bounded event-selection coverage for the reconstruction wrapper using a fake executable."""

import os
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest


INPUT_BASENAME = "pythia8NCDIS_10x275_minQ2=10_beamEffects_xAngle=-0.025_hiDiv_1.0171.edm4hep.root"


@pytest.fixture
def short_tmp_path():
    with tempfile.TemporaryDirectory(prefix="qcr-") as directory:
        yield Path(directory)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _runtime_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_dir = tmp_path / "run"
    input_dir = tmp_path / "input"
    run_dir.mkdir()
    input_dir.mkdir()
    (input_dir / INPUT_BASENAME).touch()
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(run_dir / "direct-centauro-worker.sock"))
    server.close()
    return run_dir, input_dir, tmp_path / "arguments.txt"


def _run_wrapper(tmp_path: Path, nevents: str | None, nskip: str | None = None, mode: str = "shadow") -> tuple[subprocess.CompletedProcess[str], Path]:
    run_dir, input_dir, arguments = _runtime_paths(tmp_path)
    eicrecon = tmp_path / "fake-eicrecon.sh"
    _write_executable(eicrecon, "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$ARGUMENTS\"\n")
    wrapper = Path(__file__).parents[2] / "scripts/run-reconstruction"
    env = os.environ | {
        "ARGUMENTS": str(arguments),
        "RUN_DIR": str(run_dir),
        "INPUT_DIR": str(input_dir),
        "INPUT_BASENAME": INPUT_BASENAME,
        "EICRECON_BIN": str(eicrecon),
        "EICRECON_TIMEOUT_MILLISECONDS": "1000",
    }
    if nevents is not None:
        env["EICRECON_NEVENTS"] = nevents
    if nskip is not None:
        env["EICRECON_NSKIP"] = nskip
    return subprocess.run(["bash", str(wrapper), mode], check=False, env=env, capture_output=True, text=True, timeout=5), arguments


@pytest.mark.parametrize(
    ("mode", "nevents", "nskip", "expected_nevents", "expected_nskip"),
    [
        ("shadow", None, None, "1", "0"),
        ("active", None, None, "1", "0"),
        ("shadow", "7", "6", "7", "6"),
        ("active", "7", "6", "7", "6"),
        ("shadow", "10", "9999", "10", "9999"),
        ("active", "10", "9999", "10", "9999"),
    ],
)
def test_reconstruction_passes_bounded_event_selection(short_tmp_path: Path, mode: str, nevents: str | None, nskip: str | None, expected_nevents: str, expected_nskip: str) -> None:
    result, arguments = _run_wrapper(short_tmp_path, nevents, nskip, mode)

    assert result.returncode == 0, result.stderr
    run_dir = short_tmp_path / "run"
    assert arguments.read_text(encoding="utf-8").splitlines() == [
        "-Pplugins=quantum_centauro",
        "-Pnthreads=1",
        f"-Pjana:nevents={expected_nevents}",
        f"-Pjana:nskip={expected_nskip}",
        "-Pjana:parameter_strictness=2",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_{mode}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSocketPath={run_dir}/direct-centauro-worker.sock",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTimeoutMilliseconds=1000",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumShots=512",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumExponentA=3.0",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSeed=314159",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:qiskitMaxCandidates=128",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTracePath={run_dir}/{mode}.trace.jsonl",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumFallbackPolicy=classical",
        f"-Ppodio:output_file={run_dir}/{mode}.edm4eic.root",
        "-Ppodio:output_collections=EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets",
        f"{short_tmp_path}/input/{INPUT_BASENAME}",
    ]


@pytest.mark.parametrize("nevents", ["0", "11", "-1", "+1", "1.0", " 1", "1 ", "", "01"])
def test_reconstruction_rejects_invalid_event_limits_before_eicrecon(short_tmp_path: Path, nevents: str) -> None:
    result, arguments = _run_wrapper(short_tmp_path, nevents)

    assert result.returncode == 2
    assert "EICRECON_NEVENTS" in result.stderr
    assert not arguments.exists()


@pytest.mark.parametrize("nskip", ["00", "01", "-1", "+1", "1.0", " 1", "1 ", "", "10000"])
def test_reconstruction_rejects_invalid_event_indexes_before_eicrecon(short_tmp_path: Path, nskip: str) -> None:
    result, arguments = _run_wrapper(short_tmp_path, None, nskip)

    assert result.returncode == 2
    assert "EICRECON_NSKIP" in result.stderr
    assert not arguments.exists()
