"""Direct eic-shell CLI coverage with fake worker and EICrecon processes only."""

from __future__ import annotations

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
if behavior == 'ignore-term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while True:
    connection, _ = server.accept()
    with connection:
        request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
        if behavior == 'malformed-receipt':
            connection.sendall(b'{\\n')
            continue
        receipt = {'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}
        if behavior == 'wrong-nonce': receipt['nonce'] = 'different-nonce'
        if behavior == 'wrong-pid': receipt['pid'] = pid + 1
        if behavior == 'wrong-identity': receipt['worker_identity'] = 'direct_centauro_aer_wrong'
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
    working_directories = tmp_path / "eicrecon-working-directories.txt"
    monkeypatch.setenv("EICRECON_ARGUMENTS", str(arguments))
    monkeypatch.setenv("EICRECON_WORKING_DIRECTORIES", str(working_directories))
    return _executable(
        tmp_path / "fake-eicrecon.py",
        """#!/usr/bin/env python3
import os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['EICRECON_ARGUMENTS']).open('a', encoding='utf-8') as recorded:
    recorded.write('\\0'.join(args) + '\\n')
with Path(os.environ['EICRECON_WORKING_DIRECTORIES']).open('a', encoding='utf-8') as recorded:
    recorded.write(os.getcwd() + '\\n')
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
    environment = os.environ | {"FAKE_WORKER_BEHAVIOR": behavior}
    process = subprocess.Popen([sys.executable, str(fake_worker), str(run_dir)], env=environment)
    _wait_for_socket(run_dir)
    return process


def test_entry_points_and_help_are_mapped() -> None:
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'qc-run = "eic_quantum.cli:run_main"' in pyproject
    assert 'qc-worker = "eic_quantum.cli:worker_main"' in pyproject
    assert 'qc-reconstruct = "eic_quantum.cli:reconstruct_main"' in pyproject
    for name in ("qc-run", "qc-worker", "qc-reconstruct"):
        with pytest.raises(SystemExit, match="0"):
            cli._main(name, ["--help"])
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
    environment = {
        "QUANTUM_CENTAURO_PREFIX": str(quantum),
        "EICrecon_MY": str(eicrecon),
        "JANA_PLUGIN_PATH": str(jana / "plugins"),
    }
    assert cli.discover_plugin_prefix(str(explicit), environment) == explicit
    assert cli.discover_plugin_prefix(None, environment) == quantum
    assert cli.discover_plugin_prefix(None, {"EICrecon_MY": str(eicrecon)}) == eicrecon
    assert cli.discover_plugin_prefix(None, {"JANA_PLUGIN_PATH": str(jana / "plugins")}) == jana


def test_direct_validation_errors_happen_before_run_directory_side_effects(
    tmp_path: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "run"
    result = cli._main("qc-run", [str(tmp_path / "missing.root"), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)])
    assert result == 2
    assert not run_dir.exists()
    assert "existing regular .root" in capsys.readouterr().err
    assert not (tmp_path / "eicrecon-working-directories.txt").exists()

    input_path = _input(tmp_path)
    monkeypatch.delenv("DETECTOR_PATH")
    result = cli._main("qc-run", [str(input_path), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)])
    assert result == 2
    assert not run_dir.exists()
    assert "source /opt/detector" in capsys.readouterr().err
    assert not (tmp_path / "eicrecon-working-directories.txt").exists()

    monkeypatch.setenv("DETECTOR_PATH", prepared_environment["detector"])
    monkeypatch.delenv("QUANTUM_CENTAURO_PREFIX")
    result = cli._main("qc-run", [str(input_path), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)])
    assert result == 2
    assert not run_dir.exists()
    assert "plugin is not discoverable" in capsys.readouterr().err
    assert not (tmp_path / "eicrecon-working-directories.txt").exists()


def test_invalid_event_is_rejected_before_run_directory_or_worker(
    tmp_path: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = False

    def unexpected_spawn(*_args: object, **_kwargs: object) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(cli, "_spawn_worker", unexpected_spawn)
    run_dir = tmp_path / "run"
    with pytest.raises(SystemExit, match="2"):
        cli._main("qc-run", [str(_input(tmp_path)), "--event-index", "10000", "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)])
    assert not run_dir.exists()
    assert not started


@pytest.mark.parametrize("command", ["qc-run", "qc-reconstruct"])
@pytest.mark.parametrize("timeout", ["0", "01", "+1", "1.0", "60001"])
def test_invalid_timeout_is_rejected_before_run_directory_worker_or_eicrecon(
    command: str,
    timeout: str,
    tmp_path: Path,
    prepared_environment: dict[str, str],
    fake_eicrecon: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = False

    def unexpected_spawn(*_args: object, **_kwargs: object) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(cli, "_spawn_worker", unexpected_spawn)
    input_path = _input(tmp_path)
    run_dir = tmp_path / "run"
    arguments = (
        [str(input_path), "--run-dir", str(run_dir), "--timeout-milliseconds", timeout, "--eicrecon", str(fake_eicrecon)]
        if command == "qc-run"
        else ["shadow", str(input_path), "--run-dir", str(run_dir), "--timeout-milliseconds", timeout, "--eicrecon", str(fake_eicrecon)]
    )
    with pytest.raises(SystemExit, match="2"):
        cli._main(command, arguments)
    assert not run_dir.exists()
    assert not started
    assert not (tmp_path / "eicrecon-arguments.txt").exists()


def test_auto_run_directs_shadow_then_active_and_preserves_exact_argv(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(short_tmp_path)
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    result = cli._main("qc-run", [str(_input(tmp_path)), "--event-index", "6", "--eicrecon", str(fake_eicrecon)])
    assert result == 0

    run_dirs = list((short_tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    rows = (tmp_path / "eicrecon-arguments.txt").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    shadow, active = ([*row.split("\0")] for row in rows)
    assert "-Pjana:nevents=1" in shadow
    assert "-Pjana:nskip=6" in shadow
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTimeoutMilliseconds=5000" in shadow
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_shadow" in shadow
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_active" in active
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumFallbackPolicy=classical" in active
    assert "-Ppodio:output_collections=EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets" in active
    for mode in ("shadow", "active"):
        assert (run_dir / f"{mode}.edm4eic.root").is_file()
        assert (run_dir / f"{mode}.trace.jsonl").is_file()
        assert (run_dir / f"{mode}.reconstruction.log").is_file()
    assert (run_dir / "worker.log").is_file()
    assert (run_dir / cli.LIFECYCLE_NAME).is_file()
    assert not (run_dir / cli.SOCKET_NAME).exists()
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert (tmp_path / "eicrecon-working-directories.txt").read_text(encoding="utf-8").splitlines() == [str(run_dir), str(run_dir)]
    output = capsys.readouterr().out
    assert "Completed shadow reconstruction." in output
    assert "Completed active reconstruction." in output
    assert "Terminal A worker remains running" not in output


def test_direct_defaults_to_source_event_zero(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    run_dir = short_tmp_path / "default"
    assert cli._main("qc-run", [str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 0
    rows = (tmp_path / "eicrecon-arguments.txt").read_text(encoding="utf-8").splitlines()
    assert all("-Pjana:nskip=0" in row.split("\0") for row in rows)


def test_direct_propagates_child_failure_and_cleans_its_worker(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    monkeypatch.setenv("FAIL_MODE", "shadow")
    run_dir = short_tmp_path / "run"
    assert cli._main("qc-run", [str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 23
    assert (run_dir / "shadow.reconstruction.log").is_file()
    assert not (run_dir / "active.reconstruction.log").exists()
    assert not (run_dir / cli.SOCKET_NAME).exists()


def test_forced_worker_kill_removes_only_the_stale_owned_socket(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_WORKER_BEHAVIOR", "ignore-term")
    monkeypatch.setenv("FAIL_MODE", "shadow")
    monkeypatch.setattr(cli, "WORKER_TERMINATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    run_dir = short_tmp_path / "forced"
    assert cli._main("qc-run", [str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 23
    assert not (run_dir / cli.SOCKET_NAME).exists()

    regular_file = run_dir / cli.SOCKET_NAME
    regular_file.write_text("not a socket", encoding="utf-8")
    cli._remove_stale_owned_socket(run_dir)
    assert regular_file.is_file()
    regular_file.unlink()
    target = run_dir / "target"
    target.touch()
    regular_file.symlink_to(target)
    cli._remove_stale_owned_socket(run_dir)
    assert regular_file.is_symlink()


def test_already_exited_worker_reaps_and_removes_its_actual_stale_socket(short_tmp_path: Path) -> None:
    run_dir = short_tmp_path / "already-exited"
    run_dir.mkdir(mode=0o700)
    socket_path = run_dir / cli.SOCKET_NAME
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.close()
    waited = False

    class AlreadyExitedWorker:
        pid = 1

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("an already-exited worker must not be terminated")

        def wait(self, timeout: float | None = None) -> int:
            nonlocal waited
            assert timeout == cli.WORKER_TERMINATION_TIMEOUT_SECONDS
            waited = True
            return 0

        def kill(self) -> None:
            raise AssertionError("an already-exited worker must not be killed")

    cli._stop_worker(AlreadyExitedWorker(), run_dir)
    assert waited
    assert not socket_path.exists()


def test_manual_reconstruct_requires_live_worker_and_refuses_overwrite(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = short_tmp_path / "manual"
    run_dir.mkdir()
    process = _start_fake_worker(fake_worker, run_dir)
    try:
        _wait_for_socket(run_dir)
        input_path = _input(tmp_path)
        assert cli._main("qc-reconstruct", ["shadow", str(input_path), "--run-dir", str(run_dir), "--event-index", "6", "--timeout-milliseconds", "3210", "--eicrecon", str(fake_eicrecon)]) == 0
        output = capsys.readouterr().out
        log_path = run_dir / "shadow.reconstruction.log"
        assert "Running shadow reconstruction..." in output
        assert "This can take a few minutes." in output
        assert f"Detailed reconstruction log: {log_path}" in output
        assert "Completed shadow reconstruction." in output
        assert f"ROOT output: {run_dir / 'shadow.edm4eic.root'}" in output
        assert f"Trace output: {run_dir / 'shadow.trace.jsonl'}" in output
        assert "Terminal A worker remains running; stop it with Ctrl-C" in output
        arguments = (tmp_path / "eicrecon-arguments.txt").read_text(encoding="utf-8").strip().split("\0")
        assert (tmp_path / "eicrecon-working-directories.txt").read_text(encoding="utf-8").splitlines() == [str(run_dir)]
        assert arguments == [
            "-Pplugins=quantum_centauro", "-Pnthreads=1", "-Pjana:nevents=1", "-Pjana:nskip=6",
            "-Pjana:parameter_strictness=2", "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_shadow",
            f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSocketPath={run_dir / cli.SOCKET_NAME}",
            "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTimeoutMilliseconds=3210",
            "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumShots=512",
            "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumExponentA=3.0",
            "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSeed=314159",
            "-Pquantum_centauro:reconstructeddirectcentaurojets:qiskitMaxCandidates=128",
            f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTracePath={run_dir / 'shadow.trace.jsonl'}",
            "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumFallbackPolicy=classical",
            f"-Ppodio:output_file={run_dir / 'shadow.edm4eic.root'}",
            "-Ppodio:output_collections=EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets",
            str(input_path),
        ]
        assert cli._main("qc-reconstruct", ["shadow", str(input_path), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 2
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_manual_reconstruct_failure_reports_its_detailed_log(
    tmp_path: Path,
    short_tmp_path: Path,
    fake_worker: Path,
    prepared_environment: dict[str, str],
    fake_eicrecon: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = short_tmp_path / "manual-failure"
    run_dir.mkdir()
    process = _start_fake_worker(fake_worker, run_dir)
    monkeypatch.setenv("FAIL_MODE", "shadow")
    try:
        assert cli._main("qc-reconstruct", ["shadow", str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 23
        output = capsys.readouterr()
        assert f"shadow reconstruction failed. Detailed log: {run_dir / 'shadow.reconstruction.log'}" in output.err
        assert "Terminal A worker remains running" not in output.out
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_fresh_worker_run_directory_is_not_reused(tmp_path: Path) -> None:
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    assert cli._main("qc-worker", ["--run-dir", str(run_dir)]) == 2


def test_worker_runs_foreground_with_an_injected_worker_entry(short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eic_quantum.services import direct_centauro_quantum_worker

    observed: list[str] = []
    monkeypatch.setattr(direct_centauro_quantum_worker, "main", lambda: observed.extend(sys.argv) or 0)
    run_dir = short_tmp_path / "worker"
    assert cli._main("qc-worker", ["--run-dir", str(run_dir)]) == 0
    assert observed == [
        "qc-worker", "--socket", str(run_dir / cli.SOCKET_NAME), "--shots", "512", "--exponent", "3.0",
        "--seed", "314159", "--max-candidates", "128", "--lifecycle", str(run_dir / cli.LIFECYCLE_NAME),
    ]
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700


@pytest.mark.parametrize("behavior", ["wrong-nonce", "wrong-pid", "wrong-identity", "malformed-receipt", "malformed-lifecycle"])
def test_readiness_rejects_invalid_lifecycle_and_receipts(short_tmp_path: Path, fake_worker: Path, behavior: str) -> None:
    run_dir = short_tmp_path / behavior
    run_dir.mkdir(mode=0o700)
    process = _start_fake_worker(fake_worker, run_dir, behavior=behavior)
    try:
        assert not cli._ready_receipt(run_dir, process.pid, "expected-nonce")
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_direct_signal_exit_cleans_the_owned_worker(
    tmp_path: Path, short_tmp_path: Path, fake_worker: Path, prepared_environment: dict[str, str], fake_eicrecon: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_spawn_worker", _spawn_fake_worker(fake_worker))
    monkeypatch.setattr(cli, "run_reconstruction", lambda *_args, **_kwargs: (_ for _ in ()).throw(cli.SignalExit(143)))
    run_dir = short_tmp_path / "signal"
    assert cli._main("qc-run", [str(_input(tmp_path)), "--run-dir", str(run_dir), "--eicrecon", str(fake_eicrecon)]) == 143
    assert not (run_dir / cli.SOCKET_NAME).exists()
