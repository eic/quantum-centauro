"""Lifecycle coverage for the local launcher using deterministic fake processes."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture
def short_tmp_path():
    with tempfile.TemporaryDirectory(prefix="qca-") as directory:
        yield Path(directory)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture(autouse=True)
def explicit_test_preflight_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EICRECON_BIN", "/bin/true")


@pytest.mark.parametrize(
    ("nevents", "event_index", "expected_nevents", "expected_index", "option_order"),
    [
        (None, None, "1", "0", ("--run-dir", "--input-dir", "--input-basename", "--python-bin")),
        ("7", "6", "7", "6", ("--run-dir", "--input-dir", "--input-basename", "--event-index", "--python-bin")),
        ("10", "9999", "10", "9999", ("--input-basename", "--python-bin", "--eicrecon-bin", "--event-index", "--run-dir", "--input-dir")),
    ],
)
def test_launcher_runs_both_modes_with_bounded_event_selection(
    short_tmp_path: Path, nevents: str | None, event_index: str | None, expected_nevents: str, expected_index: str,
    option_order: tuple[str, ...],
) -> None:
    worker = short_tmp_path / "fake-worker.py"
    reconstruction = short_tmp_path / "fake-reconstruction.sh"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, signal, socket, sys
with open(os.path.join(os.environ['RUN_DIR'], 'worker-pythonpath.txt'), 'w', encoding='utf-8') as pythonpath:
    pythonpath.write(os.environ['PYTHONPATH'])
path = os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.sock')
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
pid = os.getpid()
with open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8') as lifecycle:
    json.dump({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}, lifecycle)
def stop(*_):
    server.close()
    os.unlink(path)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    connection, _ = server.accept()
    with connection:
        request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
        response = {'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}
        connection.sendall(json.dumps(response).encode('utf-8') + b'\\n')
""")
    _write(reconstruction, """#!/usr/bin/env bash
set -euo pipefail
mode=$1
[[ -S "$RUN_DIR/direct-centauro-worker.sock" ]]
    printf '%s %s %s\n' "$mode" "$EICRECON_NEVENTS" "$EICRECON_NSKIP" >> "$RUN_DIR/modes.txt"
""")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / "run"
    option_values = {
        "--run-dir": str(run_dir),
        "--input-dir": str(input_dir),
        "--input-basename": "fixture.root",
        "--event-index": event_index,
        "--eicrecon-bin": "/bin/true",
        "--python-bin": sys.executable,
    }
    arguments = [argument for option in option_order for argument in (option, option_values[option])]
    result = subprocess.run(
        ["bash", str(launcher), *arguments],
        check=False,
        env=os.environ | {
            "WORKER_SCRIPT": str(worker),
            "RECONSTRUCTION_SCRIPT": str(reconstruction),
        } | ({"EICRECON_NEVENTS": nevents} if nevents is not None else {}),
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert (run_dir / "modes.txt").read_text(encoding="utf-8") == f"shadow {expected_nevents} {expected_index}\nactive {expected_nevents} {expected_index}\n"
    assert (run_dir / "worker.log").exists()
    assert (run_dir / "shadow.reconstruction.log").exists()
    assert (run_dir / "active.reconstruction.log").exists()
    assert not (run_dir / "direct-centauro-worker.sock").exists()
    assert (run_dir / "worker-pythonpath.txt").read_text(encoding="utf-8").split(":", 1)[0] == str(Path(__file__).parents[2] / "src")


@pytest.mark.parametrize("option", ["--run-dir", "--input-dir", "--input-basename", "--event-index", "--eicrecon-bin", "--python-bin"])
@pytest.mark.parametrize("invalid_value", [None, "--another-option"])
def test_launcher_rejects_missing_or_option_values_before_side_effects(
    short_tmp_path: Path, option: str, invalid_value: str | None
) -> None:
    preflight = short_tmp_path / "fake-preflight.sh"
    worker = short_tmp_path / "fake-worker.sh"
    reconstruction = short_tmp_path / "fake-reconstruction.sh"
    preflight_marker = short_tmp_path / "preflight-started"
    worker_marker = short_tmp_path / "worker-started"
    reconstruction_marker = short_tmp_path / "reconstruction-started"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    run_dir = short_tmp_path / "run"
    _write(preflight, "#!/usr/bin/env bash\ntouch \"$PREFLIGHT_MARKER\"\n")
    _write(worker, "#!/usr/bin/env bash\ntouch \"$WORKER_MARKER\"\n")
    _write(reconstruction, "#!/usr/bin/env bash\ntouch \"$RECONSTRUCTION_MARKER\"\n")
    values = {
        "--run-dir": str(run_dir),
        "--input-dir": str(input_dir),
        "--input-basename": "fixture.root",
        "--event-index": "6",
        "--eicrecon-bin": str(preflight),
        "--python-bin": sys.executable,
    }
    arguments = [argument for name, value in values.items() if name != option for argument in (name, value)]
    arguments.append(option)
    if invalid_value is not None:
        arguments.append(invalid_value)

    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts/run-local-aer"), *arguments],
        check=False,
        env=os.environ | {
            "EICRECON_BIN": str(preflight),
            "WORKER_SCRIPT": str(worker),
            "RECONSTRUCTION_SCRIPT": str(reconstruction),
            "PREFLIGHT_MARKER": str(preflight_marker),
            "WORKER_MARKER": str(worker_marker),
            "RECONSTRUCTION_MARKER": str(reconstruction_marker),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not run_dir.exists()
    assert not preflight_marker.exists()
    assert not worker_marker.exists()
    assert not reconstruction_marker.exists()


def test_launcher_preserves_existing_pythonpath_for_its_worker(short_tmp_path: Path) -> None:
    worker = short_tmp_path / "fake-worker.py"
    reconstruction = short_tmp_path / "fake-reconstruction.sh"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, signal, socket, sys
path = os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.sock')
server = socket.socket(socket.AF_UNIX); server.bind(path); server.listen(1)
pid = os.getpid()
open(os.path.join(os.environ['RUN_DIR'], 'worker-pythonpath.txt'), 'w', encoding='utf-8').write(os.environ['PYTHONPATH'])
json.dump({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}, open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8'))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    connection, _ = server.accept()
    request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
    connection.sendall(json.dumps({'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}).encode('utf-8') + b'\\n')
""")
    _write(reconstruction, "#!/usr/bin/env bash\nexit 0\n")
    run_dir = short_tmp_path / "run"
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts/run-local-aer"), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        env=os.environ | {"WORKER_SCRIPT": str(worker), "RECONSTRUCTION_SCRIPT": str(reconstruction), "PYTHON_BIN": sys.executable, "PYTHONPATH": "/existing/pythonpath"},
        capture_output=True, text=True, check=False, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert (run_dir / "worker-pythonpath.txt").read_text(encoding="utf-8") == f"{Path(__file__).parents[2] / 'src'}:/existing/pythonpath"


def test_launcher_source_uses_no_eval_and_defaults_to_container_wrapper() -> None:
    source = (Path(__file__).parents[2] / "scripts/run-local-aer").read_text(encoding="utf-8")
    assert "eval" not in source
    assert 'eicrecon_bin=${EICRECON_BIN:-"$repo_root/scripts/eicrecon-container"}' in source


def test_launcher_preflight_failure_precedes_run_directory_and_worker(short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = short_tmp_path / "fake-worker.sh"
    preflight = short_tmp_path / "failing-preflight.sh"
    marker = short_tmp_path / "worker-started"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, "#!/usr/bin/env bash\ntouch \"$MARKER\"\n")
    _write(preflight, "#!/usr/bin/env bash\n[[ \"$#\" -eq 1 && \"$1\" == \"--preflight\" ]] || exit 64\nprintf 'preflight failed\\n' >&2\nexit 2\n")
    monkeypatch.setenv("EICRECON_BIN", str(preflight))

    run_dir = short_tmp_path / "run"
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts/run-local-aer"), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        env=os.environ | {"WORKER_SCRIPT": str(worker), "MARKER": str(marker)},
        capture_output=True, text=True, check=False, timeout=5,
    )

    assert result.returncode == 2
    assert "preflight failed" in result.stderr
    assert not run_dir.exists()
    assert not marker.exists()


def test_launcher_calls_custom_wrapper_preflight_before_creating_run_directory(short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = short_tmp_path / "preflight.sh"
    marker = short_tmp_path / "preflight-args"
    worker = short_tmp_path / "fake-worker.sh"
    worker_marker = short_tmp_path / "worker-started"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(wrapper, "#!/usr/bin/env bash\n[[ \"$#\" -eq 1 && \"$1\" == \"--preflight\" ]] || exit 64\n[[ ! -e \"$RUN_DIR\" ]] || exit 65\n[[ ! -e \"$WORKER_MARKER\" ]] || exit 66\nprintf '%s\\n' \"$1\" > \"$MARKER\"\n")
    _write(worker, "#!/usr/bin/env bash\ntouch \"$WORKER_MARKER\"\nexit 1\n")
    monkeypatch.setenv("EICRECON_BIN", str(wrapper))

    run_dir = short_tmp_path / "run"
    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts/run-local-aer"), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        env=os.environ | {"MARKER": str(marker), "WORKER_MARKER": str(worker_marker), "WORKER_SCRIPT": str(worker)}, capture_output=True, text=True, check=False, timeout=5,
    )

    assert result.returncode == 1
    assert marker.read_text(encoding="utf-8") == "--preflight\n"
    assert worker_marker.exists()
    assert run_dir.exists()


@pytest.mark.parametrize("nevents", ["0", "11", "-1", "+1", "1.0", " 1", "1 ", "", "01"])
def test_launcher_rejects_invalid_event_limits_before_starting_worker(short_tmp_path: Path, nevents: str) -> None:
    worker = short_tmp_path / "fake-worker.sh"
    reconstruction = short_tmp_path / "fake-reconstruction.sh"
    marker = short_tmp_path / "started"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    _write(worker, "#!/usr/bin/env bash\ntouch \"$MARKER\"\n")
    _write(reconstruction, "#!/usr/bin/env bash\nexit 99\n")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(short_tmp_path / "run"), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        check=False,
        env=os.environ | {
            "EICRECON_NEVENTS": nevents,
            "MARKER": str(marker),
            "WORKER_SCRIPT": str(worker),
            "RECONSTRUCTION_SCRIPT": str(reconstruction),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 2
    assert "EICRECON_NEVENTS" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("event_index", ["00", "01", "-1", "+1", "1.0", " 1", "1 ", "", "10000"])
def test_launcher_rejects_invalid_event_indexes_before_preflight_or_worker(short_tmp_path: Path, event_index: str) -> None:
    worker = short_tmp_path / "fake-worker.sh"
    preflight = short_tmp_path / "fake-preflight.sh"
    marker = short_tmp_path / "started"
    preflight_marker = short_tmp_path / "preflight-started"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    _write(worker, "#!/usr/bin/env bash\ntouch \"$MARKER\"\n")
    _write(preflight, "#!/usr/bin/env bash\ntouch \"$PREFLIGHT_MARKER\"\n")
    run_dir = short_tmp_path / "run"

    result = subprocess.run(
        ["bash", str(Path(__file__).parents[2] / "scripts/run-local-aer"), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root", "--event-index", event_index],
        check=False,
        env=os.environ | {"EICRECON_BIN": str(preflight), "WORKER_SCRIPT": str(worker), "MARKER": str(marker), "PREFLIGHT_MARKER": str(preflight_marker)},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 2
    assert "--event-index" in result.stderr
    assert not run_dir.exists()
    assert not preflight_marker.exists()
    assert not marker.exists()


def test_launcher_rejects_a_stale_run_directory(short_tmp_path: Path) -> None:
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / "stale"
    run_dir.mkdir()

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(run_dir), "--input-dir", str(short_tmp_path), "--input-basename", "fixture.root"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 2
    assert "new, unused" in result.stderr


def test_launcher_rejects_a_socket_not_owned_by_its_worker(short_tmp_path: Path) -> None:
    worker = short_tmp_path / "unrelated-worker.py"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, socket, time
path = os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.sock')
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
with open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8') as lifecycle:
    json.dump({'pid': 1, 'worker_identity': 'direct_centauro_aer_1', 'state': 'started', 'settings': {}}, lifecycle)
time.sleep(10)
""")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(short_tmp_path / "run"), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        check=False,
        env=os.environ | {"WORKER_SCRIPT": str(worker), "WORKER_READY_TIMEOUT_SECONDS": "1", "PYTHON_BIN": sys.executable},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 1
    assert "ownership check" in result.stderr


def test_launcher_stops_its_worker_when_shadow_reconstruction_fails(short_tmp_path: Path) -> None:
    worker = short_tmp_path / "fake-worker.py"
    reconstruction = short_tmp_path / "failing-reconstruction.sh"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, signal, socket, sys
path = os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.sock')
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
pid = os.getpid()
with open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8') as lifecycle:
    json.dump({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}, lifecycle)
def stop(*_):
    server.close()
    os.unlink(path)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    connection, _ = server.accept()
    with connection:
        request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
        response = {'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}
        connection.sendall(json.dumps(response).encode('utf-8') + b'\\n')
""")
    _write(reconstruction, """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$1" >> "$RUN_DIR/modes.txt"
exit 23
""")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / "run"

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        check=False,
        env=os.environ | {"WORKER_SCRIPT": str(worker), "RECONSTRUCTION_SCRIPT": str(reconstruction), "PYTHON_BIN": sys.executable},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 23
    assert (run_dir / "modes.txt").read_text(encoding="utf-8") == "shadow\n"
    assert (run_dir / "shadow.reconstruction.log").exists()
    assert not (run_dir / "active.reconstruction.log").exists()
    assert not (run_dir / "direct-centauro-worker.sock").exists()


def test_launcher_stops_after_worker_dies_during_shadow(short_tmp_path: Path) -> None:
    worker = short_tmp_path / "fake-worker.py"
    reconstruction = short_tmp_path / "worker-killing-reconstruction.sh"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, signal, socket, sys
path = os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.sock')
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
pid = os.getpid()
with open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8') as lifecycle:
    json.dump({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}, lifecycle)
def stop(*_):
    server.close()
    os.unlink(path)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    connection, _ = server.accept()
    with connection:
        request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
        response = {'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}
        connection.sendall(json.dumps(response).encode('utf-8') + b'\\n')
""")
    _write(reconstruction, """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$1" >> "$RUN_DIR/modes.txt"
kill "$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["pid"])' "$RUN_DIR/direct-centauro-worker.lifecycle.json")"
""")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / "run"

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        check=False,
        env=os.environ | {"WORKER_SCRIPT": str(worker), "RECONSTRUCTION_SCRIPT": str(reconstruction), "PYTHON_BIN": sys.executable},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 1
    assert "worker exited" in result.stderr
    assert (run_dir / "modes.txt").read_text(encoding="utf-8") == "shadow\n"
    assert not (run_dir / "active.reconstruction.log").exists()


def test_launcher_keeps_worker_alive_until_active_failure(short_tmp_path: Path) -> None:
    worker = short_tmp_path / "fake-worker.py"
    reconstruction = short_tmp_path / "active-failing-reconstruction.sh"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, signal, socket, sys
path = os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.sock')
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
pid = os.getpid()
with open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8') as lifecycle:
    json.dump({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}, lifecycle)
def stop(*_):
    server.close()
    os.unlink(path)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    connection, _ = server.accept()
    with connection:
        request = json.loads(connection.makefile('rb').readline().decode('utf-8'))
        response = {'schema_version': 'active-holdout-admin-receipt/v1', 'operation': 'ready', 'result': 'ok', 'nonce': request['nonce'], 'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}'}
        connection.sendall(json.dumps(response).encode('utf-8') + b'\\n')
""")
    _write(reconstruction, """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$1" >> "$RUN_DIR/modes.txt"
[[ "$1" == shadow ]] || exit 24
""")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / "run"

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        check=False,
        env=os.environ | {"WORKER_SCRIPT": str(worker), "RECONSTRUCTION_SCRIPT": str(reconstruction), "PYTHON_BIN": sys.executable},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 24
    assert (run_dir / "modes.txt").read_text(encoding="utf-8") == "shadow\nactive\n"
    assert not (run_dir / "direct-centauro-worker.sock").exists()


def test_launcher_returns_interrupt_status_and_cleans_its_worker(short_tmp_path: Path) -> None:
    worker = short_tmp_path / "fake-worker.py"
    input_dir = short_tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fixture.root").touch()
    _write(worker, """#!/usr/bin/env python3
import json, os, signal, sys, time
pid = os.getpid()
with open(os.path.join(os.environ['RUN_DIR'], 'direct-centauro-worker.lifecycle.json'), 'w', encoding='utf-8') as lifecycle:
    json.dump({'pid': pid, 'worker_identity': f'direct_centauro_aer_{pid}', 'state': 'started', 'settings': {}}, lifecycle)
def stop(*_):
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    time.sleep(1)
""")
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / "run"
    process = subprocess.Popen(
        ["bash", str(launcher), "--run-dir", str(run_dir), "--input-dir", str(input_dir), "--input-basename", "fixture.root"],
        env=os.environ | {"WORKER_SCRIPT": str(worker), "PYTHON_BIN": sys.executable},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lifecycle = run_dir / "direct-centauro-worker.lifecycle.json"
    for _ in range(100):
        if lifecycle.exists():
            break
        time.sleep(0.01)
    else:
        process.kill()
        process.communicate(timeout=5)
        raise AssertionError("worker did not publish lifecycle data")
    worker_pid = int(__import__("json").loads(lifecycle.read_text(encoding="utf-8"))["pid"])
    process.send_signal(signal.SIGINT)
    _, stderr = process.communicate(timeout=5)

    assert process.returncode == 130, stderr
    assert not (run_dir / "direct-centauro-worker.sock").exists()
    with __import__("pytest").raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_launcher_rejects_a_run_directory_with_an_overlong_unix_socket_path(short_tmp_path: Path) -> None:
    launcher = Path(__file__).parents[2] / "scripts/run-local-aer"
    run_dir = short_tmp_path / ("r" * 90)

    result = subprocess.run(
        ["bash", str(launcher), "--run-dir", str(run_dir), "--input-dir", str(short_tmp_path), "--input-basename", "fixture.root"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 2
    assert "too long for Linux AF_UNIX" in result.stderr
    assert not run_dir.exists()
