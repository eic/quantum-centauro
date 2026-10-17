"""Unit coverage for the benchmark controller without EICrecon or Aer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmark import run


def test_balanced_orders_rotate_every_mode_through_every_position() -> None:
    orders = run.balanced_orders(3)

    assert orders == [
        ("fastjet_centauro", "direct_classical", "direct_active"),
        ("direct_classical", "direct_active", "fastjet_centauro"),
        ("direct_active", "fastjet_centauro", "direct_classical"),
    ]
    assert {order[index] for order in orders for index in range(3)} == set(run.MODES)


def test_command_builders_fix_radius_threads_and_direct_controls(tmp_path: Path) -> None:
    eicrecon = tmp_path / "eicrecon"
    input_path = tmp_path / "fixture.root"
    output = tmp_path / "output.root"
    trace = tmp_path / "trace.jsonl"
    socket_path = tmp_path / "worker.sock"

    fastjet = run.fastjet_argv(eicrecon, input_path, output, 7)
    direct = run.direct_argv(eicrecon, input_path, output, trace, 7, "active", socket_path=socket_path, timeout_milliseconds=5000, shots=512, exponent=3.0, seed=314159)

    assert "-Pplugins=log,reco,podio" in fastjet
    assert "-Preco:ReconstructedCentauroJets:rJet=0.8" in fastjet
    assert "-Preco:ReconstructedCentauroJets:jetAlgo=plugin_algorithm" in fastjet
    # The Centauro factory fixes its contrib algorithm; it is not a CLI parameter.
    assert not any("jetContribAlgo=" in argument for argument in fastjet)
    assert "-Preco:ReconstructedCentauroJets:areaType=active_area" in fastjet
    assert "-Pnthreads=1" in fastjet
    assert "-Pjana:nskip=7" in fastjet
    assert "-Pplugins=log,quantum_centauro,podio" in direct
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:quantumMode=qiskit_active" in direct
    assert "-Pquantum_centauro:reconstructeddirectcentaurojets:qiskitMaxCandidates=128" in direct
    assert f"-Pquantum_centauro:reconstructeddirectcentaurojets:quantumSocketPath={socket_path}" in direct


def test_trace_metrics_rejects_missing_summary_and_flags_fallback(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"record_type": "iteration"}) + "\n", encoding="utf-8")
    with pytest.raises(run.BenchmarkError, match="summary"):
        run.trace_metrics(run.read_trace(trace))

    records = [
        {"record_type": "iteration", "total_ipc_latency_ms": 1.0, "state_preparation_ms": 2.0, "sampling_ms": 3.0},
        {"record_type": "summary", "fallback_count": 1, "service_error_count": 0, "timeout_count": 0, "zero_distance_bypass_count": 0, "nontrivial_request_count": 1, "qiskit_applied_count": 1, "canonical_final_partition": ["[0]"], "jet_count": 1},
    ]
    metrics = run.trace_metrics(records)
    assert metrics["ipc_latency_ms_total"] == 1.0
    with pytest.raises(run.BenchmarkError, match="fallback"):
        run._validate_active_trace(metrics)


def test_sha256_file_detects_fixture_identity(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.root"
    fixture.write_bytes(b"controlled fixture")

    assert run.sha256_file(fixture) == hashlib.sha256(b"controlled fixture").hexdigest()
