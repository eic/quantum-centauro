#!/usr/bin/env python3
"""Run a bounded, reproducible EICrecon Centauro operational comparison."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from eic_quantum import cli

MODES = ("fastjet_centauro", "direct_classical", "direct_active")
FIXTURE_SHA256 = "46ca132f2dff26a1bfd82cedf867f3bc54b8a7b39c418f17525cf464e5d631c6"
RADIUS = 0.8
MAX_CANDIDATES = 128
DEFAULT_SHOTS = 512
DEFAULT_EXPONENT = 3.0
DEFAULT_SEED = 314159
DEFAULT_TIMEOUT_MILLISECONDS = 5000


class BenchmarkError(RuntimeError):
    """A precondition or measurement failure that invalidates the comparison."""


def _positive_int(value: str) -> int:
    if not value.isdecimal() or int(value) <= 0:
        raise argparse.ArgumentTypeError("must be a positive decimal integer")
    return int(value)


def _nonnegative_int(value: str) -> int:
    if not value.isdecimal():
        raise argparse.ArgumentTypeError("must be a non-negative decimal integer")
    return int(value)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive finite number") from error
    if not parsed > 0.0 or parsed == float("inf"):
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def sha256_file(path: Path) -> str:
    """Hash one fixture without holding a ROOT file in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def balanced_orders(blocks: int, modes: Sequence[str] = MODES) -> list[tuple[str, ...]]:
    """Rotate mode order across blocks so each method occupies each position."""
    if blocks <= 0 or not modes:
        raise ValueError("blocks and modes must be non-empty")
    return [tuple(modes[offset % len(modes) :] + modes[: offset % len(modes)]) for offset in range(blocks)]


def _base_argv(eicrecon: Path, input_path: Path, output_path: Path, collection: str, event_index: int) -> list[str]:
    return [
        str(eicrecon),
        "-Pplugins_to_ignore=default",
        "-Pnthreads=1",
        "-Pjana:nevents=1",
        f"-Pjana:nskip={event_index}",
        "-Pjana:parameter_strictness=2",
        f"-Ppodio:output_file={output_path}",
        f"-Ppodio:output_collections=EventHeader,ReconstructedBreitFrameParticles,{collection}",
        str(input_path),
    ]


def fastjet_argv(eicrecon: Path, input_path: Path, output_path: Path, event_index: int) -> list[str]:
    """Build the normal upstream Centauro invocation with its R=0.8 factory setting."""
    argv = _base_argv(eicrecon, input_path, output_path, "ReconstructedCentauroJets", event_index)
    return [
        argv[0],
        "-Pplugins=log,reco,podio",
        f"-Preco:ReconstructedCentauroJets:rJet={RADIUS}",
        "-Preco:ReconstructedCentauroJets:jetAlgo=plugin_algorithm",
        "-Preco:ReconstructedCentauroJets:areaType=active_area",
        *argv[1:],
    ]


def direct_argv(
    eicrecon: Path,
    input_path: Path,
    output_path: Path,
    trace_path: Path,
    event_index: int,
    mode: str,
    *,
    socket_path: Path | None,
    timeout_milliseconds: int,
    shots: int,
    exponent: float,
    seed: int,
) -> list[str]:
    """Build a DirectCentauro command with all execution controls recorded."""
    if mode not in {"classical", "active"}:
        raise ValueError("direct benchmark mode must be classical or active")
    if mode == "active" and socket_path is None:
        raise ValueError("active DirectCentauro requires a local worker socket")
    prefix = "quantum_centauro:reconstructeddirectcentaurojets"
    argv = _base_argv(eicrecon, input_path, output_path, "ReconstructedDirectCentauroJets", event_index)
    parameters = [
        "-Pplugins=log,quantum_centauro,podio",
        f"-P{prefix}:rJet={RADIUS}",
        f"-P{prefix}:quantumMode={'classical' if mode == 'classical' else 'qiskit_active'}",
        f"-P{prefix}:quantumTimeoutMilliseconds={timeout_milliseconds}",
        f"-P{prefix}:quantumShots={shots}",
        f"-P{prefix}:quantumExponentA={exponent}",
        f"-P{prefix}:quantumSeed={seed}",
        f"-P{prefix}:qiskitMaxCandidates={MAX_CANDIDATES}",
        f"-P{prefix}:quantumTracePath={trace_path}",
        f"-P{prefix}:quantumFallbackPolicy=classical",
    ]
    if socket_path is not None:
        parameters.append(f"-P{prefix}:quantumSocketPath={socket_path}")
    return [argv[0], *parameters, *argv[1:]]


def _worker_argv(run_dir: Path, shots: int, exponent: float, seed: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "eic_quantum.services.direct_centauro_quantum_worker",
        "--socket",
        str(run_dir / cli.SOCKET_NAME),
        "--shots",
        str(shots),
        "--exponent",
        str(exponent),
        "--seed",
        str(seed),
        "--max-candidates",
        str(MAX_CANDIDATES),
        "--lifecycle",
        str(run_dir / cli.LIFECYCLE_NAME),
    ]


def _start_worker(run_dir: Path, environment: dict[str, str], shots: int, exponent: float, seed: int) -> tuple[cli.WorkerProcess, float]:
    cli._ensure_socket_path_fits(run_dir)
    started = time.perf_counter()
    with (run_dir / "worker.log").open("xb") as log:
        worker = subprocess.Popen(_worker_argv(run_dir, shots, exponent, seed), stdout=log, stderr=subprocess.STDOUT, env=environment)
    try:
        cli.wait_for_worker_ready(run_dir, worker.pid)
    except Exception:
        cli._stop_worker(worker, run_dir)
        raise
    return worker, (time.perf_counter() - started) * 1000.0


def _run_eicrecon(argv: list[str], environment: dict[str, str], run_dir: Path) -> dict[str, Any]:
    """Time one fresh EICrecon process and retain its complete log."""
    log_path = run_dir / "eicrecon.log"
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    with log_path.open("x", encoding="utf-8") as log:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=environment, cwd=run_dir, check=False)
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = (usage_after.ru_utime + usage_after.ru_stime) - (usage_before.ru_utime + usage_before.ru_stime)
    return {
        "returncode": completed.returncode,
        "wall_ms": (time.perf_counter() - started) * 1000.0,
        "cpu_ms": cpu_seconds * 1000.0,
        "log_path": log_path,
    }


def read_trace(path: Path) -> list[dict[str, Any]]:
    """Load one complete JSONL trace or reject it as an incomplete artifact."""
    if not path.is_file() or path.stat().st_size == 0:
        raise BenchmarkError(f"missing or empty trace: {path}")
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("trace record is not an object")
            records.append(record)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"invalid trace: {path}") from error
    if not records:
        raise BenchmarkError(f"empty trace: {path}")
    return records


def trace_metrics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Extract the DirectCentauro fields used for artifact and failure checks."""
    items = list(records)
    summaries = [record for record in items if record.get("record_type") == "summary"]
    if len(summaries) != 1:
        raise BenchmarkError("trace must contain exactly one summary record")
    iterations = [record for record in items if record.get("record_type") == "iteration"]
    summary = summaries[0]
    required = {
        "fallback_count",
        "service_error_count",
        "timeout_count",
        "zero_distance_bypass_count",
        "nontrivial_request_count",
        "qiskit_applied_count",
        "canonical_final_partition",
        "jet_count",
    }
    if not required <= set(summary):
        raise BenchmarkError("trace summary is missing benchmark fields")
    return {
        "fallback_count": summary["fallback_count"],
        "service_error_count": summary["service_error_count"],
        "timeout_count": summary["timeout_count"],
        "zero_distance_bypass_count": summary["zero_distance_bypass_count"],
        "nontrivial_request_count": summary["nontrivial_request_count"],
        "qiskit_applied_count": summary["qiskit_applied_count"],
        "jet_count": summary["jet_count"],
        "canonical_final_partition": summary["canonical_final_partition"],
        "iterations": len(iterations),
        "ipc_latency_ms_total": sum(float(record.get("total_ipc_latency_ms", 0.0)) for record in iterations),
        "state_preparation_ms_total": sum(float(record.get("state_preparation_ms", 0.0)) for record in iterations),
        "aer_sampling_ms_total": sum(float(record.get("sampling_ms", 0.0)) for record in iterations),
        "invalid_protocol_responses": sum(
            1 for record in iterations if record.get("failure_stage") in {"response_parsing_validation", "request_validation"}
        ),
    }


def _validate_active_trace(metrics: dict[str, Any]) -> None:
    if any(metrics[name] != 0 for name in ("fallback_count", "service_error_count", "timeout_count", "invalid_protocol_responses")):
        raise BenchmarkError(f"active run recorded fallback, timeout, service, or protocol errors: {metrics}")
    if metrics["nontrivial_request_count"] != metrics["qiskit_applied_count"]:
        raise BenchmarkError("active run did not apply every nontrivial valid worker proposal")


def _prepare_run_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)


def _measure_mode(
    mode: str,
    *,
    root: Path,
    block: int,
    sequence: int,
    warmups: int,
    eicrecon: Path,
    input_path: Path,
    event_index: int,
    base_environment: dict[str, str],
    plugin_prefix: Path,
    timeout_milliseconds: int,
    shots: int,
    exponent: float,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    # Short names keep the active worker's AF_UNIX socket below its path limit.
    mode_label = {"fastjet_centauro": "f", "direct_classical": "c", "direct_active": "a"}[mode]
    mode_root = root / f"block-{block:02d}" / f"e{event_index}" / f"{sequence}-{mode_label}"
    _prepare_run_dir(mode_root)
    direct = mode != "fastjet_centauro"
    active = mode == "direct_active"
    child_environment = cli.child_environment(base_environment, plugin_prefix) if direct else dict(base_environment)
    worker: cli.WorkerProcess | None = None
    worker_cold_start_ms: float | None = None
    warmup_rows: list[dict[str, Any]] = []
    try:
        if active:
            worker, worker_cold_start_ms = _start_worker(mode_root, child_environment, shots, exponent, seed)

        for warmup in range(warmups):
            warmup_dir = mode_root / f"warmup-{warmup:02d}"
            _prepare_run_dir(warmup_dir)
            output_path = warmup_dir / "reconstructed.edm4eic.root"
            trace_path = warmup_dir / "direct.trace.jsonl"
            if mode == "fastjet_centauro":
                argv = fastjet_argv(eicrecon, input_path, output_path, event_index)
            else:
                argv = direct_argv(eicrecon, input_path, output_path, trace_path, event_index, "active" if active else "classical", socket_path=mode_root / cli.SOCKET_NAME if active else None, timeout_milliseconds=timeout_milliseconds, shots=shots, exponent=exponent, seed=seed)
            result = _run_eicrecon(argv, child_environment, warmup_dir)
            if result["returncode"] != 0 or not output_path.is_file() or not result["log_path"].is_file():
                raise BenchmarkError(f"{mode} warmup {warmup} failed; see {result['log_path']}")
            if direct:
                metrics = trace_metrics(read_trace(trace_path))
                if active:
                    _validate_active_trace(metrics)
            warmup_rows.append({"block": block, "mode": mode, "event_index": event_index, "warmup": warmup, "wall_ms": result["wall_ms"], "cpu_ms": result["cpu_ms"]})

        measured_dir = mode_root / "measured"
        _prepare_run_dir(measured_dir)
        output_path = measured_dir / "reconstructed.edm4eic.root"
        trace_path = measured_dir / "direct.trace.jsonl"
        if mode == "fastjet_centauro":
            argv = fastjet_argv(eicrecon, input_path, output_path, event_index)
        else:
            argv = direct_argv(eicrecon, input_path, output_path, trace_path, event_index, "active" if active else "classical", socket_path=mode_root / cli.SOCKET_NAME if active else None, timeout_milliseconds=timeout_milliseconds, shots=shots, exponent=exponent, seed=seed)
        result = _run_eicrecon(argv, child_environment, measured_dir)
        if result["returncode"] != 0 or not output_path.is_file() or not result["log_path"].is_file():
            raise BenchmarkError(f"{mode} measurement failed; see {result['log_path']}")

        metrics = trace_metrics(read_trace(trace_path)) if direct else None
        if active and metrics is not None:
            _validate_active_trace(metrics)
        row: dict[str, Any] = {
            "block": block,
            "sequence": sequence,
            "mode": mode,
            "event_index": event_index,
            "eicrecon_wall_ms": result["wall_ms"],
            "eicrecon_cpu_ms": result["cpu_ms"],
            "events_per_second": 1000.0 / result["wall_ms"],
            "worker_cold_start_ms": worker_cold_start_ms,
            "worker_warmup_runs": warmups if active else 0,
            "output_path": str(output_path.relative_to(root)),
            "log_path": str(result["log_path"].relative_to(root)),
            "trace_path": str(trace_path.relative_to(root)) if direct else None,
            "direct_partition_match": None,
        }
        if metrics is not None:
            row.update({key: value for key, value in metrics.items() if key != "canonical_final_partition"})
        return row, warmup_rows, metrics
    finally:
        if worker is not None:
            cli._stop_worker(worker, mode_root)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Create statistical summaries from measured rows only."""
    methods: dict[str, dict[str, Any]] = {}
    for mode in MODES:
        current = [row for row in rows if row["mode"] == mode]
        walls = [float(row["eicrecon_wall_ms"]) for row in current]
        cpus = [float(row["eicrecon_cpu_ms"]) for row in current]
        methods[mode] = {
            "measurements": len(current),
            "wall_ms_min": min(walls),
            "wall_ms_max": max(walls),
            "wall_ms_median": statistics.median(walls),
            "wall_ms_mean": statistics.fmean(walls),
            "wall_ms_stdev": statistics.stdev(walls) if len(walls) > 1 else 0.0,
            "cpu_ms_median": statistics.median(cpus),
            "events_per_second_median": statistics.median(float(row["events_per_second"]) for row in current),
        }
        if mode == "direct_active":
            cold = [float(row["worker_cold_start_ms"]) for row in current if row["worker_cold_start_ms"] is not None]
            methods[mode]["worker_cold_start_ms_median"] = statistics.median(cold)
    event_indices = sorted({row["event_index"] for row in rows})
    return {
        "measurement_count": len(rows),
        "event_indices": event_indices,
        "event_count": len(event_indices),
        "methods": methods,
        "direct_active_vs_classical": comparisons,
        "all_direct_partitions_match": all(item["partition_match"] for item in comparisons),
    }


def _environment_record(input_path: Path, input_sha256: str, eicrecon: Path, plugin_prefix: Path, arguments: argparse.Namespace) -> dict[str, Any]:
    packages: dict[str, str] = {}
    for package in ("qiskit", "qiskit-aer"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "unavailable"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "eicrecon": str(eicrecon),
        "plugin_prefix": str(plugin_prefix),
        "input": str(input_path),
        "input_sha256": input_sha256,
        "detector_path": os.environ.get("DETECTOR_PATH"),
        "detector_config": os.environ.get("DETECTOR_CONFIG"),
        "qiskit_packages": packages,
        "configuration": {
            "radius": RADIUS,
            "default_plugins_excluded": True,
            "plugins_by_mode": {
                "fastjet_centauro": ["log", "reco", "podio"],
                "direct_classical": ["log", "quantum_centauro", "podio"],
                "direct_active": ["log", "quantum_centauro", "podio"],
            },
            "threads": 1,
            "event_index": arguments.event_index,
            "event_indices": arguments.event_indices,
            "blocks": arguments.blocks,
            "warmups_per_cell": arguments.warmups,
            "shots": arguments.shots,
            "exponent": arguments.exponent,
            "seed": arguments.seed,
            "max_candidates": MAX_CANDIDATES,
            "timeout_milliseconds": arguments.timeout_milliseconds,
            "modes": list(MODES),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, metavar="INPUT", help="existing external EDM4hep/EDM4eic ROOT fixture")
    parser.add_argument("--output-dir", required=True, type=Path, metavar="DIR", help="fresh directory for new benchmark artifacts")
    parser.add_argument("--fixture-sha256", default=FIXTURE_SHA256, help="expected SHA-256 for INPUT")
    parser.add_argument("--eicrecon", help="EICrecon executable; defaults to PATH")
    parser.add_argument("--plugin-prefix", help="installed quantum-centauro prefix; defaults to environment discovery")
    events = parser.add_mutually_exclusive_group()
    events.add_argument("--event-index", type=cli.event_index, default=0, help="one event index (default: 0)")
    events.add_argument("--event-indices", nargs="+", type=_nonnegative_int, help="distinct event indices, separated by spaces")
    parser.add_argument("--blocks", type=_positive_int, default=6)
    parser.add_argument("--warmups", type=_nonnegative_int, default=1, help="unmeasured fresh EICrecon warmups per method cell")
    parser.add_argument("--timeout-milliseconds", type=cli.timeout_milliseconds, default=DEFAULT_TIMEOUT_MILLISECONDS)
    parser.add_argument("--shots", type=_positive_int, default=DEFAULT_SHOTS)
    parser.add_argument("--exponent", type=_positive_float, default=DEFAULT_EXPONENT)
    parser.add_argument("--seed", type=_nonnegative_int, default=DEFAULT_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    arguments.event_indices = arguments.event_indices or [arguments.event_index]
    if len(set(arguments.event_indices)) != len(arguments.event_indices):
        parser.error("--event-indices must not contain duplicates")
    arguments.event_index = arguments.event_indices[0] if len(arguments.event_indices) == 1 else None
    try:
        input_path = cli.validate_input(arguments.input)
        actual_sha256 = sha256_file(input_path)
        if actual_sha256 != arguments.fixture_sha256:
            raise BenchmarkError(f"fixture SHA-256 mismatch: expected {arguments.fixture_sha256}, got {actual_sha256}")
        cli.validate_detector_environment(os.environ)
        eicrecon = cli.resolve_eicrecon(arguments.eicrecon)
        plugin_prefix = cli.discover_plugin_prefix(arguments.plugin_prefix, os.environ)
        output_dir = arguments.output_dir.expanduser().resolve(strict=False)
        if output_dir.exists() or output_dir.is_symlink():
            raise BenchmarkError(f"output directory must be fresh and unused: {output_dir}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        _prepare_run_dir(output_dir)
        _write_json(output_dir / "environment.json", _environment_record(input_path, actual_sha256, eicrecon, plugin_prefix, arguments))

        rows: list[dict[str, Any]] = []
        warmup_rows: list[dict[str, Any]] = []
        comparisons: list[dict[str, Any]] = []
        for block, order in enumerate(balanced_orders(arguments.blocks), start=1):
            for event_index in arguments.event_indices:
                direct_metrics: dict[str, dict[str, Any]] = {}
                direct_rows: dict[str, dict[str, Any]] = {}
                for sequence, mode in enumerate(order, start=1):
                    print(f"Block {block}/{arguments.blocks}, event {event_index}: {mode}", flush=True)
                    row, warmups, metrics = _measure_mode(
                        mode,
                        root=output_dir,
                        block=block,
                        sequence=sequence,
                        warmups=arguments.warmups,
                        eicrecon=eicrecon,
                        input_path=input_path,
                        event_index=event_index,
                        base_environment=dict(os.environ),
                        plugin_prefix=plugin_prefix,
                        timeout_milliseconds=arguments.timeout_milliseconds,
                        shots=arguments.shots,
                        exponent=arguments.exponent,
                        seed=arguments.seed,
                    )
                    rows.append(row)
                    warmup_rows.extend(warmups)
                    if metrics is not None:
                        direct_metrics[mode] = metrics
                        direct_rows[mode] = row
                classical = direct_metrics["direct_classical"]["canonical_final_partition"]
                active = direct_metrics["direct_active"]["canonical_final_partition"]
                partition_match = classical == active
                direct_rows["direct_classical"]["direct_partition_match"] = partition_match
                direct_rows["direct_active"]["direct_partition_match"] = partition_match
                if not partition_match:
                    print(f"benchmark: partition disagreement in block {block}, event {event_index}", file=sys.stderr)
                comparisons.append({"block": block, "event_index": event_index, "partition_match": partition_match, "classical_partition": classical, "active_partition": active})

        _write_csv(output_dir / "runs.csv", rows)
        _write_json(output_dir / "warmups.json", warmup_rows)
        summary = summarize(rows, comparisons)
        summary["per_event"] = {
            str(event_index): summarize(
                [row for row in rows if row["event_index"] == event_index],
                [item for item in comparisons if item["event_index"] == event_index],
            )
            for event_index in arguments.event_indices
        }
        _write_json(output_dir / "summary.json", summary)
        print(f"Benchmark artifacts: {output_dir}", flush=True)
        return 0
    except (BenchmarkError, cli.UsageError) as error:
        print(f"benchmark: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
