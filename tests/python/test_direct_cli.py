"""Managed qc-run coverage with fake worker and EICrecon processes only."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from eic_quantum import cli


@pytest.fixture
def short_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="qcd-") as directory:
        yield Path(directory)


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_worker(tmp_path: Path) -> Path:
    return _executable(
        tmp_path / "fake-worker.py",
        """#!/usr/bin/env python3
import json, os, signal, socket, sys
from pathlib import Path
run_dir = Path(sys.argv[1])
socket_path = run_dir / 'direct-centauro-worker.sock'
lifecycle_path = run_dir / 'direct-centauro-worker.lifecycle.json'
behavior = os.environ.get('FAKE_WORKER_BEHAVIOR', 'normal')
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(str(socket_path)); server.listen(1)
pid = os.getpid()
lifecycle_path.write_text('{' if behavior == 'malformed-lifecycle' else json.dumps({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}))
def stop(*_):
    server.close(); socket_path.unlink(missing_ok=True)
    lifecycle_path.write_text(json.dumps({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'stopped', 'settings': {}}))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN if behavior == 'ignore-term' else stop)
signal.signal(signal.SIGINT, stop)
while True:
    connection, _ = server.accept()
    with connection:
        request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
        if behavior == 'malformed-receipt':
            connection.sendall(b'{\\n'); continue
        receipt = {'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}
        if behavior == 'wrong-nonce': receipt['nonce'] = 'different-nonce'
        if behavior == 'wrong-pid': receipt['pid'] = pid + 1
        connection.sendall(json.dumps(receipt).encode('utf-8') + b'\\n')
""",
    )


@pytest.fixture
def prepared_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    detector = tmp_path / "detector"
    detector.mkdir()
    (detector / "epic_craterlake.xml").touch()
    prefix = tmp_path / "plugin-prefix"
    (prefix / "plugins").mkdir(parents=True)
    (prefix / "plugins" / "quantum_centauro.so").touch()
    monkeypatch.setenv("DETECTOR_PATH", str(detector))
    monkeypatch.setenv("DETECTOR_CONFIG", "epic_craterlake")
    monkeypatch.setenv("QUANTUM_CENTAURO_PREFIX", str(prefix))
    return {"detector": str(detector), "prefix": str(prefix)}


@pytest.fixture
def fake_eicrecon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arguments = tmp_path / "eicrecon-arguments.txt"
    monkeypatch.setenv("EICRECON_ARGUMENTS", str(arguments))
    return _executable(
        tmp_path / "fake-eicrecon.py",
        """#!/usr/bin/env python3
import os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['EICRECON_ARGUMENTS']).open('a', encoding='utf-8') as recorded:
    recorded.write('\\0'.join(args) + '\\n')
for argument in args:
    if argument.startswith('-Ppodio:output_file=') or argument.startswith('-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTracePath='):
        Path(argument.split('=', 1)[1]).touch()
if any(argument.endswith('quantumMode=qiskit_' + os.environ.get('FAIL_MODE', '')) for argument in args):
    raise SystemExit(23)
""",
    )


def _input(tmp_path: Path) -> Path:
    input_path = tmp_path / "input.edm4hep.root"
    input_path.touch()
    return input_path


def _spawn_fake_worker(fake_worker: Path):
    def spawn(run_dir: Path, log_path: Path, environment: dict[str, str]) -> subprocess.Popen[bytes]:
        with log_path.open("xb") as log:
            return subprocess.Popen([sys.executable, str(fake_worker), str(run_dir)], stdout=log, stderr=subprocess.STDOUT, env=environment)

    return spawn


def _wait_for_socket(run_dir: Path) -> None:
    for _ in range(100):
        if (run_dir / cli.SOCKET_NAME).is_socket():
            return
        time.sleep(0.01)
    raise AssertionError("fake worker did not start")


def _start_fake_worker(fake_worker: Path, run_dir: Path, *, behavior: str = "normal") -> subprocess.Popen[bytes]:
    process = subprocess.Popen([sys.executable, str(fake_worker), str(run_dir)], env=os.environ | {"FAKE_WORKER_BEHAVIOR": behavior})
    _wait_for_socket(run_dir)
    return process


def test_qc_run_is_the_only_public_entry_point() -> None:
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'qc-run = "eic_quantum.cli:run_main"' in pyproject
    assert "qc-worker" not in pyproject
    assert "qc-reconstruct" not in pyproject
    with pytest.raises(SystemExit, match="0"):
        cli._main(["--help"])
    assert cli.worker_argv(Path("/tmp/qcd-run"))[3:] == [
        "--socket", "/tmp/qcd-run/direct-centauro-worker.sock", "--shots", "512", "--exponent", "3.0",
        "--seed", "314159", "--max-candidates", "128", "--lifecycle", "/tmp/qcd-run/direct-centauro-worker.lifecycle.json",
    ]


def test_plugin_discovery_uses_explicit_then_environment_then_jana(tmp_path: Path) -> None:
    def prefix(name: str) -> Path:
        result = tmp_path / name
        (result / "plugins").mkdir(parents=True)
        (result / "plugins" / "quantum_centauro.so").touch()
        return result

    explicit, quantum, eicrecon, jana = (prefix(name) for name in ("explicit", "quantum", "eicrecon", "jana"))
    environment = {"QUANTUM_CENTAURO_PREFIX": str(quantum), "EICrecon_MY": str(eicrecon), "JANA_PLUGIN_PATH": str(jana / "plugins")}
    assert cli.discover_plugin_prefix(str(explicit), environment) == explicit
    assert cli.discover_plugin_prefix(None, environment) == quantum
    assert cli.discover_plugin_prefix(None, {"EICrecon_MY": str(eicrecon)}) == eicrecon
    assert cli.discover_plugin_prefix(None, {"JANA_PLUGIN_PATH": str(jana / "plugins")}) == jana


def test_invalid_inputs_leave_no_run_directory(
    tmp_path: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "run"
    assert cli._main([str(tmp_path / "missing.root"), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 2
    assert not run_dir.exists()
    assert "existing regular .root" in capsys.readouterr().err

    monkeypatch.delenv("DETECTOR_PATH")
    assert cli._main([str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 2
    assert not run_dir.exists()
    assert "source /opt/detector" in capsys.readouterr().err

    monkeypatch.setenv("DETECTOR_PATH", prepared_environment["detector"])
    monkeypatch.delenv("QUANTUM_CENTAURO_PREFIX")
    assert cli._main([str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 2
    assert not run_dir.exists()
    assert "plugin is not discoverable" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "01", "+1", "1.0", "60001"])
def test_invalid_timeout_is_rejected_before_side_effects(
    value: str, tmp_path: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = False

    def unexpected_spawn(*_args: object, **_kwargs: object) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(cli, "_spawn_worker", unexpected_spawn)
    with pytest.raises(SystemExit, match="2"):
        cli._main([str(_input(tmp_path)), "--run-dir", str(tmp_path / "run"), "--timeout-milliseconds", value, "--eicrecon", str(fake_eicrecon)])
    assert not started


def test_qc_run_runs_shadow_then_active_and_cleans_worker(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(short_tmp_path)
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    assert cli._main([str(_input(tmp_path)), "--event-index", "6", "--eicrecon", str(fake_eicrecon)]) == 0

    run_dir = next((short_tmp_path / "runs").iterdir())
    shadow, active = ([*row.split("\0")] for row in (tmp_path / "eicrecon-arguments.txt").read_text(encoding="utf-8").splitlines())
    assert "-Pjana:nevents=1" in shadow
    assert "-Pjana:nskip=6" in shadow
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_shadow" in shadow
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_active" in active
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumFallbackPolicy=classical" in active
    for mode in ("shadow", "active"):
        assert (run_dir / f"{mode}.edm4eic.root").is_file()
        assert (run_dir / f"{mode}.trace.jsonl").is_file()
        assert (run_dir / f"{mode}.reconstruction.log").is_file()
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert not (run_dir / cli.SOCKET_NAME).exists()


def test_qc_run_propagates_child_failure_and_cleans_worker(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAIL_MODE", "shadow")
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    run_dir = short_tmp_path / "run"
    assert cli._main([str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 23
    assert (run_dir / "shadow.reconstruction.log").is_file()
    assert not (run_dir / "active.reconstruction.log").exists()
    assert not (run_dir / cli.SOCKET_NAME).exists()


@pytest.mark.parametrize("behavior", ["wrong-nonce", "wrong-pid", "malformed-receipt", "malformed-lifecycle"])
def test_readiness_rejects_invalid_lifecycle_and_receipts(short_tmp_path: Path, fake_worker: Path, behavior: str) -> None:
    run_dir = short_tmp_path / behavior
    run_dir.mkdir(mode=0o700)
    process = _start_fake_worker(fake_worker, run_dir, behavior=behavior)
    try:
        assert not cli._ready_receipt(run_dir, process.pid, "expected-nonce")
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_signal_exit_cleans_the_owned_worker(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    monkeypatch.setattr(cli, "run_reconstruction", lambda *_args, **_kwargs: (_ for _ in ()).throw(cli.SignalExit(143)))
    run_dir = short_tmp_path / "signal"
    assert cli._main([str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 143
    assert not (run_dir / cli.SOCKET_NAME).exists()
