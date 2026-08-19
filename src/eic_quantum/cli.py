"""Direct eic-shell command line tools for the Quantum Centauro plugin."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Mapping, Protocol, Sequence

DEFAULT_SHOTS = 512
DEFAULT_EXPONENT = 3.0
DEFAULT_SEED = 314159
DEFAULT_MAX_CANDIDATES = 128
DEFAULT_TIMEOUT_MILLISECONDS = 5000
SOCKET_NAME = "direct-centauro-worker.sock"
LIFECYCLE_NAME = "direct-centauro-worker.lifecycle.json"
WORKER_TERMINATION_TIMEOUT_SECONDS = 5.0
SignalHandler = Callable[[int, FrameType | None], Any] | int | None


class WorkerProcess(Protocol):
    """The narrow lifecycle interface used for a worker owned by qc-run."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class UsageError(ValueError):
    """An invalid invocation that should be reported with exit status 2."""


class SignalExit(Exception):
    """Request cleanup before returning a conventional signal exit code."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _positive_decimal(value: str, *, name: str, maximum: int) -> int:
    if not value.isdecimal() or (len(value) > 1 and value.startswith("0")):
        raise UsageError(f"{name} must be a decimal integer from 0 through {maximum}.")
    parsed = int(value)
    if parsed > maximum:
        raise UsageError(f"{name} must be a decimal integer from 0 through {maximum}.")
    return parsed


def event_index(value: str) -> int:
    return _positive_decimal(value, name="--event-index", maximum=9999)


def timeout_milliseconds(value: str) -> int:
    if not value.isdecimal() or (len(value) > 1 and value.startswith("0")):
        raise UsageError("--timeout-milliseconds must be an unpadded decimal integer from 1 through 60000.")
    parsed = int(value)
    if not 1 <= parsed <= 60000:
        raise UsageError("--timeout-milliseconds must be an unpadded decimal integer from 1 through 60000.")
    return parsed


def validate_input(path: Path) -> Path:
    resolved = path.expanduser()
    if resolved.suffix != ".root" or not resolved.is_file():
        raise UsageError(f"INPUT must name an existing regular .root file: {path}")
    return resolved.resolve()


def resolve_eicrecon(value: str | None) -> Path:
    candidate = value or "eicrecon"
    resolved_name = shutil.which(candidate) if not Path(candidate).is_absolute() else candidate
    if resolved_name is None:
        raise UsageError(f"EICrecon executable was not found: {candidate}")
    resolved = Path(resolved_name).expanduser().resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise UsageError(f"EICrecon must resolve to an executable file: {candidate}")
    return resolved


def validate_detector_environment(environment: Mapping[str, str]) -> None:
    detector_path = environment.get("DETECTOR_PATH")
    detector_config = environment.get("DETECTOR_CONFIG")
    if not detector_path or not detector_config or not (Path(detector_path) / f"{detector_config}.xml").is_file():
        raise UsageError(
            "Detector environment is not prepared. Run "
            "'source /opt/detector/epic-main/bin/thisepic.sh epic_craterlake' inside eic-shell."
        )


def discover_plugin_prefix(explicit_prefix: str | None, environment: Mapping[str, str]) -> Path:
    if explicit_prefix:
        resolved = Path(explicit_prefix).expanduser().resolve()
        if (resolved / "plugins" / "quantum_centauro.so").is_file():
            return resolved
        raise UsageError(f"Quantum Centauro plugin is unavailable at {resolved / 'plugins' / 'quantum_centauro.so'}.")

    candidates: list[Path] = []
    for variable in ("QUANTUM_CENTAURO_PREFIX", "EICrecon_MY"):
        if environment.get(variable):
            candidates.append(Path(environment[variable]))
    for entry in environment.get("JANA_PLUGIN_PATH", "").split(os.pathsep):
        if entry and (Path(entry) / "quantum_centauro.so").is_file():
            candidates.append(Path(entry).parent)

    for prefix in candidates:
        resolved = prefix.expanduser().resolve()
        if (resolved / "plugins" / "quantum_centauro.so").is_file():
            return resolved
    requested = explicit_prefix or "QUANTUM_CENTAURO_PREFIX, EICrecon_MY, or JANA_PLUGIN_PATH"
    raise UsageError(
        "Quantum Centauro plugin is not discoverable. Expected "
        f"<prefix>/plugins/quantum_centauro.so from {requested}."
    )


def child_environment(environment: Mapping[str, str], plugin_prefix: Path) -> dict[str, str]:
    child = dict(environment)
    plugin_directory = str(plugin_prefix / "plugins")
    existing = child.get("JANA_PLUGIN_PATH", "")
    child["EICrecon_MY"] = str(plugin_prefix)
    child["JANA_PLUGIN_PATH"] = plugin_directory if not existing else f"{plugin_directory}{os.pathsep}{existing}"
    return child


def _safe_run_path(path: Path) -> Path:
    expanded = path.expanduser()
    if str(expanded) in {"", ".", "/"}:
        raise UsageError("--run-dir must name a non-root run directory.")
    if expanded.is_symlink():
        raise UsageError("--run-dir must not be a symlink.")
    return expanded.resolve(strict=False)


def _ensure_socket_path_fits(run_dir: Path) -> None:
    if len(os.fsencode(run_dir / SOCKET_NAME)) >= 108:
        raise UsageError("RUN_DIR is too long for the Linux AF_UNIX socket path; choose a shorter directory.")


def create_fresh_run_dir(requested: Path | None) -> Path:
    if requested is not None:
        run_dir = _safe_run_path(requested)
        _ensure_socket_path_fits(run_dir)
        if run_dir.exists() or run_dir.is_symlink():
            raise UsageError(f"RUN_DIR must be fresh and unused: {run_dir}")
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(mode=0o700)
        run_dir.chmod(0o700)
        return run_dir

    parent = Path.cwd() / "runs"
    parent.mkdir(parents=True, exist_ok=True)
    stem = datetime.now().strftime("qc-%Y%m%d-%H%M%S")
    for suffix in range(1000):
        run_dir = parent / (stem if suffix == 0 else f"{stem}-{suffix}")
        _ensure_socket_path_fits(run_dir)
        try:
            run_dir.mkdir(mode=0o700)
        except FileExistsError:
            continue
        run_dir.chmod(0o700)
        return run_dir
    raise UsageError("Could not create a collision-safe fresh run directory beneath ./runs.")


def require_existing_run_dir(run_dir: Path) -> Path:
    resolved = _safe_run_path(run_dir)
    if not resolved.is_dir() or resolved.is_symlink():
        raise UsageError(f"RUN_DIR must be an existing non-symlink directory: {run_dir}")
    _ensure_socket_path_fits(resolved)
    return resolved


def _read_lifecycle(run_dir: Path) -> dict[str, object]:
    lifecycle_path = run_dir / LIFECYCLE_NAME
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        raise UsageError(f"Worker lifecycle is unavailable: {lifecycle_path}")
    try:
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UsageError(f"Worker lifecycle is invalid: {lifecycle_path}") from error
    pid = lifecycle.get("pid")
    if (
        not isinstance(pid, int)
        or pid <= 0
        or lifecycle.get("state") != "started"
        or lifecycle.get("worker_identity") != f"direct_centauro_aer_{pid}"
    ):
        raise UsageError(f"Worker lifecycle does not describe a running direct Centauro worker: {lifecycle_path}")
    return lifecycle


def _ready_receipt(run_dir: Path, expected_pid: int, nonce: str) -> bool:
    socket_path = run_dir / SOCKET_NAME
    if not socket_path.exists() or not socket_path.is_socket():
        return False
    try:
        lifecycle = _read_lifecycle(run_dir)
        if lifecycle["pid"] != expected_pid:
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            connection.connect(str(socket_path))
            request = {"schema_version": "active-holdout-admin/v1", "operation": "ready", "nonce": nonce}
            connection.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            raw = connection.makefile("rb").readline(65537)
        receipt = json.loads(raw.decode("utf-8"))
    except (OSError, UsageError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        receipt.get("schema_version") == "active-holdout-admin-receipt/v1"
        and receipt.get("operation") == "ready"
        and receipt.get("result") == "ok"
        and receipt.get("nonce") == nonce
        and receipt.get("pid") == expected_pid
        and receipt.get("worker_identity") == f"direct_centauro_aer_{expected_pid}"
    )


def wait_for_worker_ready(run_dir: Path, expected_pid: int, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _ready_receipt(run_dir, expected_pid, secrets.token_urlsafe(24)):
            return
        time.sleep(0.1)
    raise RuntimeError(f"Local worker failed readiness ownership check; see {run_dir / 'worker.log'}")


def require_running_worker(run_dir: Path) -> int:
    lifecycle = _read_lifecycle(run_dir)
    pid = lifecycle["pid"]
    assert isinstance(pid, int)
    if not _ready_receipt(run_dir, pid, secrets.token_urlsafe(24)):
        raise UsageError(f"Matching worker socket is unavailable or untrusted in RUN_DIR: {run_dir}")
    return pid


def worker_argv(run_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "eic_quantum.services.direct_centauro_quantum_worker",
        "--socket",
        str(run_dir / SOCKET_NAME),
        "--shots",
        str(DEFAULT_SHOTS),
        "--exponent",
        str(DEFAULT_EXPONENT),
        "--seed",
        str(DEFAULT_SEED),
        "--max-candidates",
        str(DEFAULT_MAX_CANDIDATES),
        "--lifecycle",
        str(run_dir / LIFECYCLE_NAME),
    ]


def reconstruction_argv(
    mode: str, input_path: Path, run_dir: Path, eicrecon: Path, *, event: int, timeout_milliseconds: int
) -> list[str]:
    if mode not in {"shadow", "active"}:
        raise UsageError("MODE must be shadow or active.")
    return [
        str(eicrecon),
        "-Pplugins=quantum_centauro",
        "-Pnthreads=1",
        "-Pjana:nevents=1",
        f"-Pjana:nskip={event}",
        "-Pjana:parameter_strictness=2",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_{mode}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSocketPath={run_dir / SOCKET_NAME}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTimeoutMilliseconds={timeout_milliseconds}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumShots={DEFAULT_SHOTS}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumExponentA={DEFAULT_EXPONENT}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSeed={DEFAULT_SEED}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:qiskitMaxCandidates={DEFAULT_MAX_CANDIDATES}",
        f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumTracePath={run_dir / f'{mode}.trace.jsonl'}",
        "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumFallbackPolicy=classical",
        f"-Ppodio:output_file={run_dir / f'{mode}.edm4eic.root'}",
        "-Ppodio:output_collections=EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets",
        str(input_path),
    ]


def _spawn_worker(run_dir: Path, log_path: Path, environment: Mapping[str, str]) -> WorkerProcess:
    log = log_path.open("xb")
    try:
        return subprocess.Popen(worker_argv(run_dir), stdout=log, stderr=subprocess.STDOUT, env=dict(environment))
    finally:
        log.close()


def _remove_stale_owned_socket(run_dir: Path) -> None:
    """Remove only the exact stale socket left after killing this run's worker."""
    socket_path = run_dir / SOCKET_NAME
    try:
        if run_dir.is_symlink() or not stat.S_ISDIR(run_dir.lstat().st_mode):
            return
        socket_status = socket_path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISSOCK(socket_status.st_mode):
        socket_path.unlink()


def _stop_worker(worker: WorkerProcess, run_dir: Path) -> None:
    if worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=WORKER_TERMINATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=WORKER_TERMINATION_TIMEOUT_SECONDS)
    else:
        worker.wait(timeout=WORKER_TERMINATION_TIMEOUT_SECONDS)
    _remove_stale_owned_socket(run_dir)


def run_reconstruction(
    mode: str,
    input_path: Path,
    run_dir: Path,
    *,
    event: int,
    timeout_milliseconds: int,
    plugin_prefix: str | None,
    eicrecon: str | None,
    environment: Mapping[str, str] | None = None,
    manual: bool = False,
) -> int:
    environment = os.environ if environment is None else environment
    if mode not in {"shadow", "active"}:
        raise UsageError("MODE must be shadow or active.")
    validated_input = validate_input(input_path)
    validated_run_dir = require_existing_run_dir(run_dir)
    validate_detector_environment(environment)
    resolved_eicrecon = resolve_eicrecon(eicrecon)
    prefix = discover_plugin_prefix(plugin_prefix, environment)
    for output in (
        validated_run_dir / f"{mode}.edm4eic.root",
        validated_run_dir / f"{mode}.trace.jsonl",
        validated_run_dir / f"{mode}.reconstruction.log",
    ):
        if output.exists() or output.is_symlink():
            raise UsageError(f"Refusing to overwrite existing {mode} artifact: {output}")
    require_running_worker(validated_run_dir)
    argv = reconstruction_argv(
        mode,
        validated_input,
        validated_run_dir,
        resolved_eicrecon,
        event=event,
        timeout_milliseconds=timeout_milliseconds,
    )
    log_path = validated_run_dir / f"{mode}.reconstruction.log"
    root_path = validated_run_dir / f"{mode}.edm4eic.root"
    trace_path = validated_run_dir / f"{mode}.trace.jsonl"
    print(f"Running {mode} reconstruction...", flush=True)
    print("This can take a few minutes.", flush=True)
    print(f"Detailed reconstruction log: {log_path}", flush=True)
    with log_path.open("x", encoding="utf-8") as log:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=child_environment(environment, prefix),
            cwd=validated_run_dir,
            check=False,
        )
    if completed.returncode:
        print(f"{mode} reconstruction failed. Detailed log: {log_path}", file=sys.stderr)
    else:
        print(f"Completed {mode} reconstruction.", flush=True)
        print(f"ROOT output: {root_path}", flush=True)
        print(f"Trace output: {trace_path}", flush=True)
        print(f"Detailed reconstruction log: {log_path}", flush=True)
        if manual:
            print("Terminal A worker remains running; stop it with Ctrl-C after reconstruction finishes.", flush=True)
    return completed.returncode


def _run_direct(arguments: argparse.Namespace) -> int:
    environment = os.environ
    input_path = validate_input(arguments.input)
    validate_detector_environment(environment)
    resolve_eicrecon(arguments.eicrecon)
    prefix = discover_plugin_prefix(arguments.plugin_prefix, environment)
    run_dir = create_fresh_run_dir(arguments.run_dir)
    worker: WorkerProcess | None = None
    old_handlers: dict[int, SignalHandler] = {}

    def interrupted(returncode: int) -> Callable[[int, object], None]:
        def handler(_signum: int, _frame: object) -> None:
            raise SignalExit(returncode)
        return handler

    try:
        old_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, interrupted(130))
        old_handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, interrupted(143))
        print(f"Starting local worker in {run_dir}...", flush=True)
        worker = _spawn_worker(run_dir, run_dir / "worker.log", child_environment(environment, prefix))
        wait_for_worker_ready(run_dir, worker.pid)
        for mode in ("shadow", "active"):
            if worker.poll() is not None:
                raise RuntimeError(f"Local worker exited; see {run_dir / 'worker.log'}")
            result = run_reconstruction(
                mode,
                input_path,
                run_dir,
                event=arguments.event_index,
                timeout_milliseconds=arguments.timeout_milliseconds,
                plugin_prefix=str(prefix),
                eicrecon=str(resolve_eicrecon(arguments.eicrecon)),
                environment=environment,
            )
            if result:
                return result
        print(f"Completed shadow and active reconstruction. Run directory: {run_dir}")
        return 0
    except KeyboardInterrupt:
        return 130
    except SignalExit as error:
        return error.returncode
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for signal_number, handler in old_handlers.items():
            signal.signal(signal_number, handler)
        if worker is not None:
            _stop_worker(worker, run_dir)


def _run_packaged_worker(run_dir: Path) -> int:
    from eic_quantum.services.direct_centauro_quantum_worker import main as worker_main

    original_argv = sys.argv
    try:
        sys.argv = ["qc-worker", *worker_argv(run_dir)[3:]]
        return worker_main()
    finally:
        sys.argv = original_argv


def _run_worker(arguments: argparse.Namespace) -> int:
    run_dir = create_fresh_run_dir(arguments.run_dir)
    print(f"Starting local worker in {run_dir}...", flush=True)
    return _run_packaged_worker(run_dir)


def _run_reconstruct(arguments: argparse.Namespace) -> int:
    return run_reconstruction(
        arguments.mode,
        arguments.input,
        arguments.run_dir,
        event=arguments.event_index,
        timeout_milliseconds=arguments.timeout_milliseconds,
        plugin_prefix=arguments.plugin_prefix,
        eicrecon=arguments.eicrecon,
        manual=True,
    )


def _parser(name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=name)
    if name == "qc-run":
        parser.add_argument("input", type=Path, metavar="INPUT")
        parser.add_argument("--event-index", type=event_index, default=0)
        parser.add_argument("--timeout-milliseconds", type=timeout_milliseconds, default=DEFAULT_TIMEOUT_MILLISECONDS)
        parser.add_argument("--run-dir", type=Path)
        parser.add_argument("--plugin-prefix")
        parser.add_argument("--eicrecon")
    elif name == "qc-worker":
        parser.add_argument("--run-dir", required=True, type=Path, metavar="DIR")
    else:
        parser.add_argument("mode", choices=("shadow", "active"), metavar="MODE")
        parser.add_argument("input", type=Path, metavar="INPUT")
        parser.add_argument("--run-dir", required=True, type=Path, metavar="DIR")
        parser.add_argument("--event-index", type=event_index, default=0)
        parser.add_argument("--timeout-milliseconds", type=timeout_milliseconds, default=DEFAULT_TIMEOUT_MILLISECONDS)
        parser.add_argument("--plugin-prefix")
        parser.add_argument("--eicrecon")
    return parser


def _main(name: str, argv: Sequence[str] | None = None) -> int:
    parser = _parser(name)
    arguments = parser.parse_args(argv)
    try:
        if name == "qc-run":
            return _run_direct(arguments)
        if name == "qc-worker":
            return _run_worker(arguments)
        return _run_reconstruct(arguments)
    except UsageError as error:
        parser.print_usage(sys.stderr)
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return 2
    return 2


def run_main() -> int:
    return _main("qc-run")


def worker_main() -> int:
    return _main("qc-worker")


def reconstruct_main() -> int:
    return _main("qc-reconstruct")
