"""Persistent local Aer worker for DirectCentauro candidate-index selection.

The worker supports strict JSONL selector requests and the legacy text protocol.
JSON responses bind to the exact received payload bytes before the framing LF.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import math
import os
import signal
import socket
import json
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Protocol

from eic_quantum.contracts.blind_selector import ContractValidationError, MAX_JSONL_LINE_BYTES, execute_blind_request, validate_request
from eic_quantum.payloads.quantum_min_search import (
    DEFAULT_EXPONENT_A,
    DEFAULT_SIMULATOR_SEED,
    _load_qiskit,
    build_inverse_power_circuit_with_metadata,
)

PROTOCOL_VERSION = "v1"
DIAGNOSTIC_PROTOCOL_VERSION = "v2"
DEFAULT_MAX_CANDIDATES = 128
MAX_CANDIDATES = 128
ACTIVE_HOLDOUT_PROFILES = frozenset({
    (3, 512, 314159), (3, 32, 314159), (6, 2048, 314159),
    (3, 512, 271828), (3, 32, 271828), (6, 2048, 271828),
    (3, 512, 1618033), (3, 32, 1618033), (6, 2048, 1618033),
})
BENCHMARK_TIMING_PROFILES = frozenset(
    (exponent, shots, seed)
    for exponent in (0.5, 1.0, 2.0, 3.0, 4.0, 6.0)
    for shots in (32, 64, 128, 256, 512, 1024, 2048, 4096)
    for seed in (314159, 271828, 1618033)
)


class WorkerStageError(ValueError):
    """A bounded failure marker that carries no request or environment details."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(stage)
        self.stage = stage
        self.cause = cause


class StopEvent(Protocol):
    """The minimal signal-safe stop interface accepted by ``serve``."""

    def is_set(self) -> bool: ...


SignalHandler = Callable[[int, FrameType | None], None] | int | None


class DirectCentauroQuantumWorker:
    """Own one Aer sampler for the lifetime of the local Unix-socket service."""

    def __init__(self, shots: int, exponent_a: float, seed: int, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> None:
        if (shots <= 0 or not math.isfinite(exponent_a) or exponent_a <= 0 or seed < 0 or
                max_candidates <= 0 or max_candidates > MAX_CANDIDATES or max_candidates & (max_candidates - 1)):
            raise ValueError("shots, exponent, seed, and a power-of-two max candidate count from 1 through 128 must be valid worker settings.")
        _load_qiskit()
        self._shots = shots
        self._exponent_a = exponent_a
        self._seed = seed
        self._max_candidates = max_candidates
        self._versions = {
            "qiskit": importlib.metadata.version("qiskit"),
            "qiskit_aer": importlib.metadata.version("qiskit-aer"),
        }
        self._sampler = None

    def lifecycle_settings(self) -> dict[str, object]:
        settings = {"shots": self._shots, "exponent": self._exponent_a, "seed": self._seed,
                    "max_candidates": self._max_candidates, **self._versions}
        return settings

    def reconfigure(self, shots: int, exponent_a: float, seed: int, *, benchmark_timing: bool = False) -> None:
        allowed = BENCHMARK_TIMING_PROFILES if benchmark_timing else ACTIVE_HOLDOUT_PROFILES
        if (exponent_a, shots, seed) not in allowed:
            raise ValueError("administrative profile is not in the benchmark timing allowlist" if benchmark_timing else "administrative profile is not in the active-holdout allowlist")
        self._shots, self._exponent_a, self._seed = shots, exponent_a, seed
        self._sampler = None

    def select(self, distances: list[float]) -> dict[str, Any]:
        if not distances or len(distances) > self._max_candidates:
            raise ValueError("candidate count is outside the local Aer bound.")
        if any(not math.isfinite(value) or value < 0 for value in distances):
            raise ValueError("distances must be finite and non-negative.")
        minimum = min(distances)
        if minimum == 0:
            return {
                "index": distances.index(0),
                "counts_by_candidate": {str(index): 0 for index in range(len(distances))},
                "amplitudes": [],
                "probabilities": [],
                "state_preparation_ms": 0.0,
                "sampling_ms": 0.0,
                "qubits": 0,
                "circuit_depth": 0,
                "zero_distance_bypass": True,
                "preparation": {"method": "exact_zero_bypass", "version": "v1", "cutoff": 0.0, "dropped_probability_mass": 0.0, "state_fidelity": 1.0},
            }
        preparation_started = time.perf_counter()
        try:
            circuit, amplitudes, probabilities, preparation = build_inverse_power_circuit_with_metadata(distances, self._exponent_a)
        except (ValueError, OverflowError, TypeError) as error:
            raise WorkerStageError("state_preparation", error) from error
        preparation_ms = (time.perf_counter() - preparation_started) * 1000.0
        sampling_started = time.perf_counter()
        try:
            # Generic StatePreparation must be fully expanded for Aer.  The
            # stabilization cutoff keeps this synthesis numerically bounded.
            if self._sampler is None:
                _, _, sampler_type = _load_qiskit()
                self._sampler = sampler_type(default_shots=self._shots, seed=self._seed)
            assert self._sampler is not None
            sampled_circuit = circuit.decompose(reps=10)
            result = self._sampler.run([sampled_circuit], shots=self._shots).result()
        except (ValueError, OverflowError, TypeError) as error:
            raise WorkerStageError("aer_sampling", error) from error
        sampling_ms = (time.perf_counter() - sampling_started) * 1000.0
        try:
            counts = result[0].data.meas.get_counts()
        except (ValueError, OverflowError, TypeError, IndexError, KeyError, AttributeError) as error:
            raise WorkerStageError("counts_result_parsing", error) from error
        votes = [0] * len(distances)
        for bitstring, count in counts.items():
            index = int(str(bitstring).replace(" ", ""), 2)
            if index < len(votes):
                votes[index] += int(count)
        return {
            "index": max(range(len(votes)), key=lambda index: (votes[index], -index)),
            "counts_by_candidate": {str(index): count for index, count in enumerate(votes)},
            "amplitudes": amplitudes,
            "probabilities": probabilities,
            "state_preparation_ms": preparation_ms,
            "sampling_ms": sampling_ms,
            "qubits": circuit.num_qubits,
            "circuit_depth": circuit.depth(),
            "zero_distance_bypass": False,
            "preparation": preparation,
        }


def parse_request(line: str) -> tuple[str, list[float]]:
    fields = line.strip().split()
    if len(fields) < 2:
        raise ValueError("expected protocol version followed by one or more distances")
    if fields[0] == PROTOCOL_VERSION:
        return fields[0], [float(field) for field in fields[1:]]
    if fields[0] == DIAGNOSTIC_PROTOCOL_VERSION and len(fields) >= 5:
        return fields[0], [float(field) for field in fields[4:]]
    raise ValueError("expected v1 distances or v2 event mode iteration distances")


def parse_blind_request(payload: bytes) -> dict[str, Any]:
    """Strictly decode one unframed v1 truth-blind JSON payload."""
    try:
        return validate_request(json.loads(payload.decode("utf-8", errors="strict")))
    except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as error:
        raise ValueError("invalid truth-blind selector request") from error


def process_blind_request(
    worker: DirectCentauroQuantumWorker,
    payload: bytes,
    *,
    wire_request_sha256: str,
) -> dict[str, Any]:
    """Execute one strict request using its own execution controls.

    A worker instance owns the Aer sampler.  Requests must agree with its fixed
    settings so a caller cannot silently influence a persistent worker after it
    starts.  The zero-distance bypass remains inside ``worker.select`` and
    therefore never constructs or samples an Aer circuit.
    """
    parsing_started = time.perf_counter()
    request = parse_blind_request(payload)
    parsing_ms = (time.perf_counter() - parsing_started) * 1000.0
    if (request["shots"], float(request["exponent_a"]), request["seed"]) != (
        worker._shots, worker._exponent_a, worker._seed,
    ):
        raise ValueError("request execution controls do not match persistent worker settings")
    if len(request["candidates"]) > worker._max_candidates:
        raise ValueError("candidate count is outside the local Aer bound")

    def select(distances: list[float], shots: int, exponent_a: float, seed: int) -> dict[str, Any]:
        del shots, exponent_a, seed
        result = worker.select(distances)
        return {
            "selected_candidate_index": result["index"],
            "counts_by_candidate": result["counts_by_candidate"],
            "amplitudes": result["amplitudes"],
            "probabilities": result["probabilities"],
            "circuit": {"qubits": result["qubits"], "depth": result["circuit_depth"]},
            "timings_ms": {
                "state_preparation": result["state_preparation_ms"],
                "sampling": result["sampling_ms"],
                "request_parsing_validation": parsing_ms,
                "response_assembly_serialization": 0.0,
            },
            "zero_distance_bypass": result["zero_distance_bypass"],
            "preparation": result["preparation"],
            "worker": {"implementation": "direct_centauro_aer", **worker.lifecycle_settings()},
        }

    assembly_started = time.perf_counter()
    response = execute_blind_request(
        request,
        select,
        wire_request_sha256=wire_request_sha256,
        wire_request_payload=payload,
    )
    response["timings_ms"]["response_assembly_serialization"] = (time.perf_counter() - assembly_started) * 1000.0
    return response


def attach_worker_metadata(response: dict[str, Any], *, pid: int, request_sequence: int) -> dict[str, Any]:
    """Attach live, non-truth worker identity metadata to a validated response."""
    if pid <= 0 or request_sequence <= 0 or not isinstance(response.get("worker"), dict):
        raise ValueError("worker metadata requires a positive PID, sequence, and worker object")
    return {
        **response,
        "worker": {
            **response["worker"],
            "pid": pid,
            "identity": f"direct_centauro_aer_{pid}",
            "request_sequence": request_sequence,
        },
    }


def sanitize_worker_error(error: Exception, stage: str = "worker_handler") -> dict[str, str]:
    """Return bounded lifecycle diagnostics without leaking request/environment data."""
    codes = {ValueError: "selector_value_error", OverflowError: "selector_overflow_error", TypeError: "selector_type_error"}
    if isinstance(error, WorkerStageError):
        stage = error.stage
        error = error.cause
    return {
        "exception_class": type(error).__name__,
        "stage": stage,
        "code": codes.get(type(error), "selector_internal_error"),
        "message_sha256": hashlib.sha256(str(error).encode("utf-8")).hexdigest(),
    }


def administrative_response(worker: DirectCentauroQuantumWorker, request: dict[str, Any]) -> dict[str, Any]:
    """Serve nonce-bound health and allowlisted reconfiguration without selector work."""
    benchmark_timing = request.get("schema_version") == "benchmark-timing-admin/v1"
    if (set(request) - {"schema_version", "operation", "nonce", "profile"} or
            request.get("schema_version") not in {"active-holdout-admin/v1", "benchmark-timing-admin/v1"}):
        raise ValueError("invalid administrative request")
    nonce = request.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16:
        raise ValueError("administrative nonce is invalid")
    operation = request.get("operation")
    if operation not in {"health", "ready", "reconfigure"}:
        raise ValueError("administrative operation is invalid")
    if operation == "reconfigure":
        profile = request.get("profile")
        if not isinstance(profile, dict) or set(profile) != {"a", "shots", "seed"}:
            raise ValueError("administrative profile is invalid")
        if type(profile["a"]) not in (int, float) or type(profile["shots"]) is not int or type(profile["seed"]) is not int:
            raise ValueError("administrative profile types are invalid")
        allowed = BENCHMARK_TIMING_PROFILES if benchmark_timing else ACTIVE_HOLDOUT_PROFILES
        if (float(profile["a"]), profile["shots"], profile["seed"]) not in allowed:
            raise ValueError("administrative profile is not in the benchmark timing allowlist" if benchmark_timing else "administrative profile is not in the active-holdout allowlist")
        if benchmark_timing:
            worker.reconfigure(profile["shots"], float(profile["a"]), profile["seed"], benchmark_timing=True)
        else:
            worker.reconfigure(profile["shots"], float(profile["a"]), profile["seed"])
    return {"schema_version": "benchmark-timing-admin-receipt/v1" if benchmark_timing else "active-holdout-admin-receipt/v1", "operation": operation,
            "result": "ok", "nonce": nonce, "pid": os.getpid(),
            "worker_identity": f"direct_centauro_aer_{os.getpid()}", "settings": worker.lifecycle_settings()}


def _send_response(connection: socket.socket, response: str) -> None:
    """Write one already-bounded protocol response to a connected client."""
    connection.sendall(response.encode("utf-8"))


def _install_stop_handlers(stop_event: threading.Event) -> tuple[SignalHandler, SignalHandler]:
    """Turn terminal shutdown signals into a cooperative service stop."""
    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop_event.set()

    return (
        signal.signal(signal.SIGINT, request_stop),
        signal.signal(signal.SIGTERM, request_stop),
    )


def serve(socket_path: Path, worker: DirectCentauroQuantumWorker, lifecycle_path: Path | None = None, stop_event: StopEvent | None = None) -> None:
    if socket_path.exists() or socket_path.is_symlink():
        raise RuntimeError(f"refusing to replace existing socket: {socket_path}")
    if lifecycle_path is not None and (lifecycle_path.exists() or lifecycle_path.is_symlink()):
        raise RuntimeError(f"refusing to replace existing lifecycle: {lifecycle_path}")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    requests = 0
    disconnects = 0
    last_error: dict[str, str] | None = None
    last_request: dict[str, Any] | None = None
    last_request_sha256: str | None = None
    try:
        server.bind(str(socket_path))
        server.listen(1)
        server.settimeout(0.1)
        if lifecycle_path is not None:
            lifecycle_path.write_text(json.dumps({"pid": os.getpid(), "worker_identity": f"direct_centauro_aer_{os.getpid()}", "state": "started", "settings": worker.lifecycle_settings()}) + "\n", encoding="utf-8")
        print(
            f"Worker ready at {socket_path}. Keep this terminal open; press Ctrl-C after reconstruction finishes.",
            flush=True,
        )
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                framed_line = connection.makefile("rb").readline(MAX_JSONL_LINE_BYTES + 1)
                administrative = False
                selector_succeeded = False
                activity_event: int | None = None
                activity_iteration: int | None = None
                stage = "request_validation"
                try:
                    if not framed_line.endswith(b"\n") or len(framed_line) > MAX_JSONL_LINE_BYTES:
                        raise ValueError("invalid JSONL framing")
                    payload = framed_line[:-1]
                    line = payload.decode("utf-8", errors="strict")
                    if line.lstrip().startswith("{"):
                        decoded = json.loads(line)
                        if decoded.get("schema_version") in {"active-holdout-admin/v1", "benchmark-timing-admin/v1"}:
                            administrative = True
                            response = json.dumps(administrative_response(worker, decoded), sort_keys=True,
                                                  separators=(",", ":"), allow_nan=False) + "\n"
                        else:
                            last_request = validate_request(decoded)
                            activity_event = last_request["event"]
                            activity_iteration = last_request["iteration"]
                            last_request_sha256 = hashlib.sha256(payload).hexdigest()
                            stage = "worker_handler"
                            response = json.dumps(attach_worker_metadata(process_blind_request(worker, payload,
                                                                               wire_request_sha256=last_request_sha256),
                                                                               pid=os.getpid(), request_sequence=requests + 1), sort_keys=True,
                                                   separators=(",", ":"), allow_nan=False) + "\n"
                            selector_succeeded = True
                    else:
                        stage = "worker_handler"
                        version, distances = parse_request(line)
                        selected = worker.select(distances)
                        if version == PROTOCOL_VERSION:
                            response = f"{selected['index']}\n"
                        else:
                            response = (f"v2 index={selected['index']} worker_pid={os.getpid()} worker_identity=direct_centauro_aer_{os.getpid()} request_sequence={requests + 1} "
                                         f"state_preparation_ms={selected['state_preparation_ms']:.9f} sampling_ms={selected['sampling_ms']:.9f} "
                                         f"qubits={selected['qubits']} shots={worker._shots} exponent={worker._exponent_a:.9f} "
                                         f"zero_distance_bypass={int(selected['zero_distance_bypass'])}\n")
                        selector_succeeded = True
                except (ValueError, OverflowError, TypeError) as error:
                    last_error = sanitize_worker_error(error, stage)
                    response = "ERR\n"
                try:
                    _send_response(connection, response)
                except (BrokenPipeError, ConnectionResetError):
                    disconnects += 1
                    print("Client disconnected while receiving a response; worker remains available.", flush=True)
                    continue
                if not administrative:
                    requests += 1
                    if selector_succeeded:
                        activity = f"Processed selector request #{requests}"
                        if activity_event is not None and activity_iteration is not None:
                            activity += f" (event {activity_event}, iteration {activity_iteration})"
                        print(activity, flush=True)
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)
        if lifecycle_path is not None:
            lifecycle = {"pid": os.getpid(), "worker_identity": f"direct_centauro_aer_{os.getpid()}", "state": "stopped", "requests_served": requests, "client_disconnects": disconnects, "settings": worker.lifecycle_settings()}
            if last_error is not None:
                lifecycle["last_error"] = last_error
            if last_request is not None:
                lifecycle["last_request"] = last_request
                lifecycle["last_request_sha256"] = last_request_sha256
            lifecycle_path.write_text(json.dumps(lifecycle) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent local DirectCentauro Aer worker.")
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--shots", type=int, default=512)
    parser.add_argument("--exponent", type=float, default=DEFAULT_EXPONENT_A)
    parser.add_argument("--seed", type=int, default=DEFAULT_SIMULATOR_SEED)
    parser.add_argument("--lifecycle", type=Path)
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    arguments = parser.parse_args()
    arguments.socket.parent.mkdir(parents=True, exist_ok=True)
    worker = DirectCentauroQuantumWorker(arguments.shots, arguments.exponent, arguments.seed, arguments.max_candidates)
    stop_event = threading.Event()
    previous_sigint, previous_sigterm = _install_stop_handlers(stop_event)
    try:
        serve(arguments.socket, worker, arguments.lifecycle, stop_event)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
    print("Worker stopped cleanly.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
