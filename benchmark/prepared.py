#!/usr/bin/env python3
"""Replay genuine selector inputs with circuits prepared before timed execution."""
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import importlib.metadata
import json
import math
import platform
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from eic_quantum.payloads.quantum_min_search import (
    _load_qiskit,
    build_inverse_power_circuit_with_metadata,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)

    def percentile(p: float) -> float:
        position = (len(ordered) - 1) * p
        lo, hi = math.floor(position), math.ceil(position)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)

    return {
        "n": len(values), "min": ordered[0], "max": ordered[-1],
        "median": statistics.median(values), "mean": statistics.mean(values),
        "sample_stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "iqr": percentile(0.75) - percentile(0.25),
        "p05": percentile(0.05), "p95": percentile(0.95), "p99": percentile(0.99),
    }


def load_inputs(source: Path) -> tuple[list[dict], list[dict]]:
    inputs, provenance = [], []
    paths = sorted((source / "block-01").glob("e*/**/measured/direct.trace.jsonl"))
    seen = set()
    for path in paths:
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        active = [r for r in records if r.get("record_type") == "iteration" and r.get("mode") == "qiskit_active"]
        if not active:
            continue
        event_index = int(path.relative_to(source / "block-01").parts[0][1:])
        provenance.append({"path": str(path), "sha256": digest(path)})
        for record in active:
            identity = (event_index, record["iteration"])
            if identity in seen:
                raise ValueError(f"duplicate frozen selector input: {identity}")
            seen.add(identity)
            candidates = record["candidates"]
            if not candidates or any(not isinstance(c["distance"], (int, float)) or
                                     not math.isfinite(c["distance"]) or c["distance"] <= 0 for c in candidates):
                raise ValueError(f"prepared Aer replay requires finite positive distances: {identity}")
            inputs.append({"input_id": f"e{event_index}:i{record['iteration']}",
                           "event_index": event_index, "iteration": record["iteration"],
                           "trace_event": record["event"], "classical_index": record["classical_index"],
                           "candidates": candidates})
    if not inputs:
        raise ValueError("no measured active selector inputs found in source block-01")
    return sorted(inputs, key=lambda r: (r["event_index"], r["iteration"])), provenance


class NativeSelector:
    def __init__(self, library: Path, calls: int):
        self.library = ctypes.CDLL(str(library))
        self.function = self.library.classical_selector_ns
        self.function.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint64),
                                  ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_double),
                                  ctypes.c_size_t, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64),
                                  ctypes.POINTER(ctypes.c_uint64)]
        self.function.restype = ctypes.c_double
        self.calls = calls

    def measure(self, arrays: tuple, expected: int) -> float:
        selected, checksum = ctypes.c_uint64(), ctypes.c_uint64()
        ns = self.function(*arrays, len(arrays[0]), self.calls,
                           ctypes.byref(selected), ctypes.byref(checksum))
        if ns <= 0 or selected.value != expected or checksum.value != expected * self.calls:
            raise ValueError("native selection does not match frozen trace or native timing is invalid")
        return ns


def native_arrays(candidates: list[dict]) -> tuple:
    n = len(candidates)
    return ((ctypes.c_int * n)(*(0 if c["kind"] == "pair" else 1 for c in candidates)),
            (ctypes.c_uint64 * n)(*(c["i"] for c in candidates)),
            (ctypes.c_uint64 * n)(*(c["j"] if c["j"] is not None else c["i"] for c in candidates)),
            (ctypes.c_double * n)(*(c["distance"] for c in candidates)))


def backend_experiment_ms(result) -> float | None:
    # Installed Aer forwards global simulator metadata, not experiment timers.
    # Accept only explicit single-experiment shapes if another version exposes
    # them; never interpret an ambiguous global timer as kernel execution time.
    metadata = result[0].metadata.get("simulator_metadata")
    experiments = metadata if isinstance(metadata, list) else metadata.get("results") if isinstance(metadata, dict) else None
    if isinstance(experiments, list) and len(experiments) == 1 and isinstance(experiments[0], dict):
        seconds = experiments[0].get("time_taken")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0:
            return seconds * 1000.0
    return None


def aer_measure(sampler, circuit, shots: int, count: int) -> tuple[float, float, int, float | None]:
    started = time.perf_counter_ns()
    result = sampler.run([circuit], shots=shots).result()
    sampled = time.perf_counter_ns()
    counts = result[0].data.meas.get_counts()
    votes = [0] * count
    for bitstring, value in counts.items():
        index = int(str(bitstring).replace(" ", ""), 2)
        if index < count:
            votes[index] += int(value)
    selected = max(range(count), key=lambda i: (votes[i], -i))
    finished = time.perf_counter_ns()
    backend_ms = backend_experiment_ms(result)  # metadata extraction outside all primary timers
    return (sampled - started) / 1e6, (finished - sampled) / 1e6, selected, backend_ms


def cluster_ratio_ci(per_input: list[dict], bootstrap_seed: int) -> dict:
    # Repeated timings are not independent events. Resample ten event clusters,
    # retaining all iterations belonging to each selected fixture event.
    events = defaultdict(lambda: [0.0, 0.0])
    for item in per_input:
        events[item["event_index"]][0] += item["aer_total_ms"]["median"]
        events[item["event_index"]][1] += item["classical_ns"]["median"] / 1e6
    clusters = list(events.values())
    rng = random.Random(bootstrap_seed)
    ratios = []
    for _ in range(2000):
        selected = rng.choices(clusters, k=len(clusters))
        ratios.append(sum(c[0] for c in selected) / sum(c[1] for c in selected))
    ratios.sort()
    return {"ratio_of_summed_input_medians": sum(c[0] for c in clusters) / sum(c[1] for c in clusters),
            "bootstrap_95_percent_interval": [ratios[49], ratios[1949]],
            "bootstrap_draws": 2000, "bootstrap_seed": bootstrap_seed,
            "resampling_unit": "fixture event cluster", "event_clusters": len(clusters),
            "caution": "Conditional on these fixed fixture events, not evidence of new event diversity."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-benchmark", type=Path)
    source_group.add_argument("--frozen-inputs", type=Path, help="Replay archived frozen_inputs.json without external trace directories")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--classical-library", required=True, type=Path)
    parser.add_argument("--compiler-description", default="not supplied; record the actual compiler and flags")
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--classical-calls", type=int, default=10000)
    parser.add_argument("--shots", type=int, default=512)
    parser.add_argument("--aer-threads", type=int, default=0,
                        help="0 preserves production Aer defaults; positive values limit backend threads")
    parser.add_argument("--exponent", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=314159)
    args = parser.parse_args()
    if min(args.repeats, args.classical_calls, args.shots) <= 0 or min(args.seed, args.aer_threads) < 0 or not math.isfinite(args.exponent) or args.exponent <= 0:
        parser.error("repeats, classical-calls, shots and exponent must be positive; seed and aer-threads non-negative")
    source = args.source_benchmark.resolve() if args.source_benchmark else None
    output, library = args.output_dir.resolve(), args.classical_library.resolve()
    if args.frozen_inputs:
        frozen = args.frozen_inputs.resolve()
        inputs = json.loads(frozen.read_text())
        if not isinstance(inputs, list) or not inputs:
            raise ValueError("frozen inputs must be a non-empty record array")
        identities = set()
        for item in inputs:
            identity = (item["event_index"], item["iteration"])
            candidates = item["candidates"]
            if identity in identities or not candidates or not 0 <= item["classical_index"] < len(candidates):
                raise ValueError("invalid or duplicate frozen selector input")
            if any(not isinstance(c["distance"], (int, float)) or not math.isfinite(c["distance"]) or c["distance"] <= 0 for c in candidates):
                raise ValueError("frozen replay requires finite positive distances")
            identities.add(identity)
        provenance = [{"path": str(frozen), "sha256": digest(frozen)}]
    else:
        inputs, provenance = load_inputs(source)
    native = NativeSelector(library, args.classical_calls)
    _, _, sampler_type = _load_qiskit()
    backend_options = {"max_parallel_threads": args.aer_threads} if args.aer_threads else {}
    output.mkdir(parents=True, exist_ok=False)
    (output / "frozen_inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    prepared = []
    for item in inputs:
        distances = [c["distance"] for c in item["candidates"]]
        arrays = native_arrays(item["candidates"])
        native.measure(arrays, item["classical_index"])  # excluded integrity guard and warmup
        started = time.perf_counter_ns()
        circuit, _, probabilities, metadata = build_inverse_power_circuit_with_metadata(distances, args.exponent)
        built = time.perf_counter_ns()
        ready = circuit.decompose(reps=10)
        decomposed = time.perf_counter_ns()
        minimum = min(distances)
        prepared.append({"item": item, "arrays": arrays, "circuit": ready,
                         "minimum": minimum, "singleton": len(distances) == 1,
                         "candidate_count": len(distances), "qubits": ready.num_qubits,
                         "ready_circuit_depth": ready.depth(), "build_ms": (built - started) / 1e6,
                         "decompose_ms": (decomposed - built) / 1e6, "preparation": metadata,
                         "minimum_count": sum(d == minimum for d in distances),
                         "minimum_probability_mass": sum(p for p, d in zip(probabilities, distances) if d == minimum)})
    rows = []
    fields = ["input_id", "event_index", "iteration", "repeat", "seed", "trial_order",
              "candidate_count", "singleton", "qubits", "classical_ns", "aer_sampling_ms",
              "aer_decode_ms", "aer_total_ms", "aer_over_classical_ratio", "aer_backend_experiment_ms", "sampled_index",
              "first_index_match", "minimum_distance_success", "minimum_count", "minimum_probability_mass"]
    with (output / "prepared_runs.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for repeat in range(args.repeats):
            seed = args.seed + repeat
            sampler_settings = {"default_shots": args.shots, "seed": seed}
            if backend_options:
                sampler_settings["options"] = {"backend_options": backend_options}
            sampler = sampler_type(**sampler_settings)
            # Fresh reproducible seed per repeat, one persistent sampler across
            # genuine inputs; each prepared circuit receives an excluded warmup.
            for offset in range(len(prepared)):
                case = prepared[(offset + repeat) % len(prepared)]
                item = case["item"]
                aer_measure(sampler, case["circuit"], args.shots, case["candidate_count"])
                classical_first = (repeat + offset) % 2 == 0
                if classical_first:
                    classical_ns = native.measure(case["arrays"], item["classical_index"])
                sampling_ms, decode_ms, sampled_index, backend_ms = aer_measure(sampler, case["circuit"], args.shots, case["candidate_count"])
                if not classical_first:
                    classical_ns = native.measure(case["arrays"], item["classical_index"])
                total_ms = sampling_ms + decode_ms
                row = {key: item[key] for key in ("input_id", "event_index", "iteration")}
                row.update({key: case[key] for key in ("candidate_count", "singleton", "qubits", "minimum_count", "minimum_probability_mass")})
                row.update(repeat=repeat, seed=seed, trial_order="classical,aer" if classical_first else "aer,classical",
                           classical_ns=classical_ns, aer_sampling_ms=sampling_ms, aer_decode_ms=decode_ms,
                           aer_total_ms=total_ms, aer_over_classical_ratio=total_ms * 1e6 / classical_ns,
                           aer_backend_experiment_ms=backend_ms,
                           sampled_index=sampled_index, first_index_match=sampled_index == item["classical_index"],
                           minimum_distance_success=item["candidates"][sampled_index]["distance"] == case["minimum"])
                writer.writerow(row)
                handle.flush()
                rows.append(row)
            print(f"Prepared repeat {repeat + 1}/{args.repeats}: {len(rows)} measured Aer calls", flush=True)
    metrics = ("classical_ns", "aer_sampling_ms", "aer_decode_ms", "aer_total_ms", "aer_over_classical_ratio")
    per_input = []
    for case in prepared:
        selected = [r for r in rows if r["input_id"] == case["item"]["input_id"]]
        detail = {k: case[k] for k in ("candidate_count", "singleton", "qubits", "ready_circuit_depth", "build_ms", "decompose_ms", "minimum_count", "minimum_probability_mass", "preparation")}
        detail.update({k: case["item"][k] for k in ("input_id", "event_index", "iteration")})
        detail.update({k: describe([r[k] for r in selected]) for k in metrics})
        detail.update(first_index_matches=sum(r["first_index_match"] for r in selected),
                      minimum_distance_successes=sum(r["minimum_distance_success"] for r in selected))
        per_input.append(detail)
    header = Path(__file__).resolve().parents[1] / "plugin/quantum_centauro/include/quantum_centauro/DirectCentauroJetMinimumSelector.h"
    backend_times = [r["aer_backend_experiment_ms"] for r in rows if r["aer_backend_experiment_ms"] is not None]
    summary = {"scope": "isolated frozen-input circuit-ready selector replay, not event pipeline or QPU timing",
               "source_benchmark": str(source) if source else None, "source_traces": provenance,
               "input_source_kind": "frozen_input_file" if args.frozen_inputs else "benchmark_block_01_traces",
               "events": sorted({r["event_index"] for r in rows}), "inputs": len(inputs),
               "repeats": args.repeats, "measured_aer_calls": len(rows), "excluded_aer_warmups": len(rows),
               "shots": args.shots, "exponent": args.exponent, "seeds": list(range(args.seed, args.seed + args.repeats)),
               "aer_backend_options": backend_options,
               "aer_method_choice": "Production default automatic; no method override.",
               "thread_choice": ("Production defaults; no backend_options supplied." if not backend_options else
                                 "Explicit thread-limited control; not the operational worker's default settings."),
               "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                           "qiskit": importlib.metadata.version("qiskit"), "qiskit_aer": importlib.metadata.version("qiskit-aer")},
               "native_library": {"path": str(library), "sha256": digest(library), "compiler": args.compiler_description,
                                  "selector_header_sha256": digest(header), "calls_per_batch": args.classical_calls,
                                  "includes": "native batch loop/compiler barrier overhead; excludes Python and vector construction"},
               "excluded": ["circuit construction", "circuit decomposition", "sampler creation", "warmups", "IPC", "file I/O", "native compilation", "candidate enumeration"],
               "included": "Aer run/result contains simulator overhead and any internal compilation plus execution of state-preparation gates; decode includes counts extraction and vote selection.",
               "statistics": {k: describe([r[k] for r in rows]) for k in metrics},
               "backend_experiment_runtime": {
                   "available_calls": len(backend_times),
                   "statistics_ms": describe(backend_times) if backend_times else None,
                   "source": "Explicit single experiment time_taken in SamplerPubResult simulator_metadata, if exposed.",
                   "caution": "Secondary simulator-native report, not QPU time; includes state-preparation gate execution. Installed Aer forwards global metadata without experiment timers, so unavailable stays null; no subtraction or inferred kernel timing."},
               "first_index_matches": sum(r["first_index_match"] for r in rows),
               "minimum_distance_successes": sum(r["minimum_distance_success"] for r in rows),
               "singleton_inputs": sum(c["singleton"] for c in prepared),
               "paired_ratio_event_cluster_ci": cluster_ratio_ci(per_input, args.seed),
               "per_candidate_count": {str(n): {k: describe([r[k] for r in rows if r["candidate_count"] == n]) for k in metrics}
                                       for n in sorted({r["candidate_count"] for r in rows})},
               "per_input": per_input}
    (output / "prepared_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"measured_aer_calls": len(rows), "statistics": summary["statistics"],
                      "first_index_matches": summary["first_index_matches"],
                      "minimum_distance_successes": summary["minimum_distance_successes"],
                      "paired_ratio_event_cluster_ci": summary["paired_ratio_event_cluster_ci"]}, indent=2))


if __name__ == "__main__":
    main()
