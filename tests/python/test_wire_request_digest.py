"""Source-only protocol coverage for request digest binding and local limits."""

import hashlib

import pytest

from eic_quantum.contracts.blind_selector import (
    ContractValidationError,
    execute_blind_request,
    join_evaluation,
    validate_response,
)
from eic_quantum.payloads.quantum_min_search import (
    MAX_QISKIT_LOCAL_CANDIDATES,
    _next_power_of_two_dimension,
    validate_request as validate_payload_request,
)
from eic_quantum.services.direct_centauro_quantum_worker import parse_blind_request


def _result(distances, shots, exponent_a, seed):
    del distances, shots, exponent_a, seed
    return {
        "selected_candidate_index": 0,
        "counts_by_candidate": {"0": 1},
        "amplitudes": [1.0],
        "probabilities": [1.0],
        "circuit": {"qubits": 1, "depth": 1},
        "timings_ms": {"state_preparation": 0.0, "sampling": 0.0, "request_parsing_validation": 0.0, "response_assembly_serialization": 0.0},
        "zero_distance_bypass": False,
        "preparation": {"method": "stabilized_state_preparation", "version": "v1", "cutoff": 0.0, "dropped_probability_mass": 0.0, "state_fidelity": 1.0},
        "worker": {},
    }


@pytest.mark.parametrize("payload", [
    b'{"event":"caf\xc3\xa9","schema_version":"selector-request/v1","request_id":"one","iteration":0,"candidates":[{"kind":"beam","candidate_index":0,"j":null,"i":0,"distance":1}],"shots":1,"exponent_a":1,"seed":0}',
    b'{"schema_version":"selector-request/v1", "request_id":"one", "event":"caf\xc3\xa9", "iteration":0, "candidates":[{"candidate_index":0,"kind":"beam","i":0,"j":null,"distance":1.0}], "shots":1, "exponent_a":1e0, "seed":0}',
    b'{"seed":0,"exponent_a":1,"shots":1,"candidates":[{"distance":-0.0,"j":null,"i":0,"kind":"beam","candidate_index":0}],"iteration":0,"event":"caf\xc3\xa9","request_id":"one","schema_version":"selector-request/v1"}',
])
def test_worker_wire_digest_preserves_valid_json_spelling(payload):
    request = parse_blind_request(payload)
    digest = hashlib.sha256(payload).hexdigest()
    response = execute_blind_request(request, _result, wire_request_sha256=digest, wire_request_payload=payload)

    assert response["request_sha256"] == digest
    assert validate_response(response, request, expected_request_sha256=digest) == response


def test_altered_wire_digest_is_rejected():
    payload = b'{"schema_version":"selector-request/v1","request_id":"one","event":"one","iteration":0,"candidates":[{"candidate_index":0,"kind":"beam","i":0,"j":null,"distance":1}],"shots":1,"exponent_a":1,"seed":0}'
    request = parse_blind_request(payload)
    response = execute_blind_request(
        request,
        _result,
        wire_request_sha256=hashlib.sha256(payload).hexdigest(),
        wire_request_payload=payload,
    )

    with pytest.raises(ContractValidationError, match="digest"):
        validate_response(response, request, expected_request_sha256="0" * 64)


def test_join_evaluation_accepts_a_trusted_noncanonical_wire_digest():
    payload = b'{"event":"one", "schema_version":"selector-request/v1", "request_id":"one", "iteration":0, "candidates":[{"candidate_index":0,"kind":"beam","i":0,"j":null,"distance":1.0}], "shots":1, "exponent_a":1e0, "seed":0}'
    request = parse_blind_request(payload)
    digest = hashlib.sha256(payload).hexdigest()
    response = execute_blind_request(
        request,
        _result,
        wire_request_sha256=digest,
        wire_request_payload=payload,
    )

    joined = join_evaluation(request, response, {"truth": "kept outside"}, expected_request_sha256=digest)

    assert joined["response"] == response


def test_join_evaluation_rejects_a_mismatched_trusted_wire_digest():
    payload = b'{"event":"one", "schema_version":"selector-request/v1", "request_id":"one", "iteration":0, "candidates":[{"candidate_index":0,"kind":"beam","i":0,"j":null,"distance":1.0}], "shots":1, "exponent_a":1e0, "seed":0}'
    request = parse_blind_request(payload)
    response = execute_blind_request(
        request,
        _result,
        wire_request_sha256=hashlib.sha256(payload).hexdigest(),
        wire_request_payload=payload,
    )

    with pytest.raises(ContractValidationError, match="digest"):
        join_evaluation(request, response, {"truth": "kept outside"}, expected_request_sha256="0" * 64)


def test_unrelated_wire_payload_cannot_supply_a_response_digest():
    payload = b'{"schema_version":"selector-request/v1","request_id":"one","event":"one","iteration":0,"candidates":[{"candidate_index":0,"kind":"beam","i":0,"j":null,"distance":1}],"shots":1,"exponent_a":1,"seed":0}'
    unrelated_payload = payload.replace(b'"event":"one"', b'"event":"two"')
    request = parse_blind_request(payload)

    with pytest.raises(ContractValidationError, match="does not match"):
        execute_blind_request(
            request,
            _result,
            wire_request_sha256=hashlib.sha256(unrelated_payload).hexdigest(),
            wire_request_payload=unrelated_payload,
        )


def test_invalid_utf8_request_is_rejected():
    with pytest.raises(ValueError, match="invalid truth-blind"):
        parse_blind_request(b'{"schema_version":"selector-request/v1","event":"\xff"}')


def test_local_payload_cap_accepts_128_candidates_without_aer():
    candidates = [
        {"kind": "beam", "i": index, "j": None, "distance": float(index + 1)}
        for index in range(128)
    ]
    request = {
        "request_id": "cap-128",
        "event_id": 0,
        "algorithm": "direct_centauro",
        "mode": "shadow",
        "provider": "local",
        "shots": 1,
        "candidates": candidates,
        "metadata": {},
    }

    assert MAX_QISKIT_LOCAL_CANDIDATES == 128
    assert _next_power_of_two_dimension(len(candidates)) == 128
    assert validate_payload_request(request) is request


def test_shipped_cpp_fail_closed_default_and_response_guards_are_present():
    root = __import__("pathlib").Path(__file__).parents[2]
    config_source = (root / "integration/eicrecon/additions/src/algorithms/reco/DirectCentauroJetReconstructionConfig.h").read_text()
    socket_source = (root / "integration/eicrecon/additions/src/algorithms/reco/DirectCentauroQuantumSocketClient.h").read_text()

    assert "bool quantumFailClosed = false;" in config_source
    assert "quantum_fail_closed=${QUANTUM_FAIL_CLOSED:-true}" in (root / "scripts/run-shadow").read_text()
    assert 'quantumFailClosed="$quantum_fail_closed"' in (root / "scripts/run-shadow").read_text()
    assert "reply.probabilities.back() < 0.0" in socket_source
    assert "!std::isfinite(reply.amplitudes.back())" in socket_source
    assert 'worker.contains("max_candidates")' in socket_source
    assert "worker.at(\"max_candidates\").get<std::size_t>() < candidates.size()" in socket_source
    assert "reply.workerResponseAssemblyMilliseconds < 0.0" in socket_source
