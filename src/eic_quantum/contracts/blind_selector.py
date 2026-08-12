"""Fail-closed, truth-blind selector request and response contract.

``request_sha256`` is a deterministic object-level helper for unit use.  A
serving worker binds responses to the exact UTF-8 JSON payload bytes it received,
excluding its single LF framing delimiter.  Evaluation truth deliberately has no
place in either DTO and is attached only after that digest check succeeds.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from typing import Any

REQUEST_SCHEMA_VERSION = "selector-request/v1"
RESPONSE_SCHEMA_VERSION_V1 = "selector-response/v1"
RESPONSE_SCHEMA_VERSION_V2 = "selector-response/v2"
RESPONSE_SCHEMA_VERSION = RESPONSE_SCHEMA_VERSION_V2
MAX_JSONL_LINE_BYTES = 64 * 1024

REQUEST_FIELDS = frozenset({
    "schema_version", "request_id", "event", "iteration", "candidates", "shots", "exponent_a", "seed",
})
CANDIDATE_FIELDS = frozenset({"candidate_index", "kind", "i", "j", "distance"})
RESPONSE_FIELDS_V1 = frozenset({
    "schema_version", "request_id", "request_sha256", "status", "selected_candidate_index",
    "counts_by_candidate", "amplitudes", "probabilities", "circuit", "timings_ms",
    "zero_distance_bypass", "worker",
})
PREPARATION_FIELDS = frozenset({"method", "version", "cutoff", "dropped_probability_mass", "state_fidelity"})
TIMING_FIELDS_V1 = frozenset({"state_preparation", "sampling"})
TIMING_FIELDS_V2 = TIMING_FIELDS_V1 | {"request_parsing_validation", "response_assembly_serialization"}
RESPONSE_FIELDS_V2 = RESPONSE_FIELDS_V1 | {"preparation"}
RESPONSE_FIELDS = RESPONSE_FIELDS_V2
RESULT_FIELDS = RESPONSE_FIELDS_V2 - {"schema_version", "request_id", "request_sha256", "status"}
FORBIDDEN_FIELD_FRAGMENTS = (
    "classical", "minimum", "argmin", "partition", "selected_candidate", "final_jet", "fastjet",
)


class ContractValidationError(ValueError):
    """Raised when a selector DTO is not strictly truth-blind and allowlisted."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_nonnegative(value: object, path: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ContractValidationError(f"{path} must be a finite non-negative number.")
    return float(value)


def _strict_object(value: object, allowed: frozenset[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path} must be an object.")
    keys = set(value)
    if not all(isinstance(key, str) for key in keys):
        raise ContractValidationError(f"{path} keys must be strings.")
    unknown = sorted(keys - allowed)
    if unknown:
        raise ContractValidationError(f"{path} has unknown fields: {', '.join(unknown)}.")
    missing = sorted(allowed - keys)
    if missing:
        raise ContractValidationError(f"{path} is missing required fields: {', '.join(missing)}.")
    return value


def _reject_forbidden_keys(value: object, path: str = "request") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(f"{path} keys must be strings.")
            normalized = key.casefold()
            if any(fragment in normalized for fragment in FORBIDDEN_FIELD_FRAGMENTS):
                raise ContractValidationError(f"{path}.{key} is forbidden in a truth-blind selector DTO.")
            _reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def canonical_json_bytes(value: object) -> bytes:
    """Encode a DTO deterministically and enforce the one-line protocol bound."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ContractValidationError("selector DTO must be finite JSON data.") from error
    if len(encoded) > MAX_JSONL_LINE_BYTES:
        raise ContractValidationError(f"selector DTO exceeds the {MAX_JSONL_LINE_BYTES}-byte JSONL limit.")
    return encoded


def request_sha256(request: Mapping[str, Any]) -> str:
    """Return a canonical object digest, not a cross-language wire binding."""
    return hashlib.sha256(canonical_json_bytes(request)).hexdigest()


def validate_request(value: object) -> dict[str, Any]:
    """Validate the complete v1 blind request, recursively and fail closed."""
    _reject_forbidden_keys(value)
    request = _strict_object(value, REQUEST_FIELDS, "request")
    if request["schema_version"] != REQUEST_SCHEMA_VERSION:
        raise ContractValidationError(f"request.schema_version must be {REQUEST_SCHEMA_VERSION!r}.")
    for name in ("request_id", "event"):
        if not isinstance(request[name], str) or not request[name]:
            raise ContractValidationError(f"request.{name} must be a non-empty string.")
    if not _is_int(request["iteration"]) or request["iteration"] < 0:
        raise ContractValidationError("request.iteration must be a non-negative integer.")
    if not _is_int(request["shots"]) or request["shots"] <= 0:
        raise ContractValidationError("request.shots must be a positive integer.")
    if not _is_int(request["seed"]) or request["seed"] < 0:
        raise ContractValidationError("request.seed must be a non-negative integer.")
    exponent = request["exponent_a"]
    if not isinstance(exponent, (int, float)) or isinstance(exponent, bool) or not math.isfinite(exponent) or exponent <= 0:
        raise ContractValidationError("request.exponent_a must be a positive finite number.")
    candidates = request["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ContractValidationError("request.candidates must be a non-empty array.")
    for index, raw_candidate in enumerate(candidates):
        candidate = _strict_object(raw_candidate, CANDIDATE_FIELDS, f"request.candidates[{index}]")
        if candidate["candidate_index"] != index:
            raise ContractValidationError("candidate indexes must be dense 0..N-1 in order.")
        if candidate["kind"] not in {"pair", "beam"}:
            raise ContractValidationError("candidate kind must be 'pair' or 'beam'.")
        if not _is_int(candidate["i"]) or candidate["i"] < 0:
            raise ContractValidationError("candidate i must be a non-negative integer.")
        if candidate["kind"] == "pair":
            if not _is_int(candidate["j"]) or candidate["j"] < 0 or candidate["i"] >= candidate["j"]:
                raise ContractValidationError("pair candidates require non-negative i < j.")
        elif candidate["j"] is not None:
            raise ContractValidationError("beam candidates require j=null.")
        _finite_nonnegative(candidate["distance"], f"request.candidates[{index}].distance")
    canonical_json_bytes(request)
    return request


def validate_response(
    value: object,
    request: Mapping[str, Any],
    *,
    expected_request_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate a response against an object digest or worker-owned wire digest.

    ``expected_request_sha256`` is reserved for a serving boundary that derived
    it from the exact received JSON payload bytes.  It is never client input.
    """
    validated_request = validate_request(dict(request))
    if not isinstance(value, dict) or value.get("schema_version") not in {RESPONSE_SCHEMA_VERSION_V1, RESPONSE_SCHEMA_VERSION_V2}:
        raise ContractValidationError("response.schema_version must be a supported strict version.")
    schema_version = value["schema_version"]
    response = _strict_object(value, RESPONSE_FIELDS_V1 if schema_version == RESPONSE_SCHEMA_VERSION_V1 else RESPONSE_FIELDS_V2, "response")
    if response["request_id"] != validated_request["request_id"]:
        raise ContractValidationError("response request ID does not match request.")
    expected_digest = request_sha256(validated_request) if expected_request_sha256 is None else expected_request_sha256
    if not isinstance(expected_digest, str) or response["request_sha256"] != expected_digest:
        raise ContractValidationError("response request digest does not match request.")
    if response["status"] != "ok":
        raise ContractValidationError("response status must be 'ok'.")
    candidate_count = len(validated_request["candidates"])
    selected_index = response["selected_candidate_index"]
    if not _is_int(selected_index) or not 0 <= selected_index < candidate_count:
        raise ContractValidationError("response selected candidate index is outside the request.")
    if not isinstance(response["zero_distance_bypass"], bool):
        raise ContractValidationError("response.zero_distance_bypass must be a boolean.")
    counts = response["counts_by_candidate"]
    if not isinstance(counts, dict) or set(counts) != {str(index) for index in range(candidate_count)}:
        raise ContractValidationError("response counts must cover each candidate exactly once.")
    if any(not _is_int(count) or count < 0 for count in counts.values()):
        raise ContractValidationError("response counts must be non-negative integers.")
    for name in ("amplitudes", "probabilities"):
        values = response[name]
        if not isinstance(values, list) or any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item) for item in values):
            raise ContractValidationError(f"response.{name} must contain finite numbers.")
    for name in ("circuit", "timings_ms", "worker"):
        if not isinstance(response[name], dict):
            raise ContractValidationError(f"response.{name} must be an object.")
    timing = _strict_object(response["timings_ms"], TIMING_FIELDS_V1 if schema_version == RESPONSE_SCHEMA_VERSION_V1 else TIMING_FIELDS_V2, "response.timings_ms")
    for name in timing:
        _finite_nonnegative(timing[name], f"response.timings_ms.{name}")
    if schema_version == RESPONSE_SCHEMA_VERSION_V2:
        preparation = _strict_object(response["preparation"], PREPARATION_FIELDS, "response.preparation")
        if preparation["method"] not in {"stabilized_state_preparation", "exact_zero_bypass"}:
            raise ContractValidationError("response.preparation.method is not supported.")
        if preparation["version"] != "v1":
            raise ContractValidationError("response.preparation.version must be 'v1'.")
        for name, maximum in (("cutoff", 1.0), ("dropped_probability_mass", 1.0), ("state_fidelity", 1.0 + 1.0e-9)):
            _finite_nonnegative(preparation[name], f"response.preparation.{name}")
            if float(preparation[name]) > maximum:
                raise ContractValidationError(f"response.preparation.{name} is outside its bounded range.")
    canonical_json_bytes(response)
    return response


def execute_blind_request(
    request: object,
    select: Callable[[list[float], int, float, int], Mapping[str, Any]],
    *,
    wire_request_sha256: str | None = None,
    wire_request_payload: bytes | None = None,
) -> dict[str, Any]:
    """Run an injected selector with only the blind fields it needs.

    The adapter never accepts evaluation metadata; the callable receives only
    ordered distances and execution controls from the strict request DTO.
    When supplied by a serving boundary, ``wire_request_sha256`` must be paired
    with its exact received payload bytes, excluding the JSONL LF delimiter.
    """
    if (wire_request_sha256 is None) != (wire_request_payload is None):
        raise ContractValidationError("worker wire digest and payload must be supplied together.")
    validated_request = validate_request(request)
    if wire_request_payload is not None:
        if not isinstance(wire_request_payload, bytes) or not isinstance(wire_request_sha256, str):
            raise ContractValidationError("worker wire digest and payload have invalid types.")
        if hashlib.sha256(wire_request_payload).hexdigest() != wire_request_sha256:
            raise ContractValidationError("worker wire digest does not match its payload.")
        try:
            payload_request = validate_request(json.loads(wire_request_payload.decode("utf-8", errors="strict")))
        except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as error:
            raise ContractValidationError("worker wire payload is not a valid selector request.") from error
        if payload_request != validated_request:
            raise ContractValidationError("worker wire payload does not match the selector request.")
    selected = dict(select(
        [float(candidate["distance"]) for candidate in validated_request["candidates"]],
        validated_request["shots"],
        float(validated_request["exponent_a"]),
        validated_request["seed"],
    ))
    unknown = set(selected) - RESULT_FIELDS
    missing = RESULT_FIELDS - set(selected)
    if unknown or missing:
        details = ", ".join(sorted(unknown or missing))
        raise ContractValidationError(f"worker result has invalid fields: {details}.")
    response = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "request_id": validated_request["request_id"],
        "request_sha256": request_sha256(validated_request) if wire_request_sha256 is None else wire_request_sha256,
        "status": "ok",
        **selected,
    }
    return validate_response(response, validated_request, expected_request_sha256=wire_request_sha256)


def join_evaluation(
    request: object,
    response: object,
    evaluation: object,
    *,
    expected_request_sha256: str | None = None,
) -> dict[str, Any]:
    """Attach truth only after validating the blind request/response digest pair.

    A serving boundary may supply its trusted digest of the exact request wire
    payload. Without it, the existing canonical object-digest validation applies.
    """
    validated_request = validate_request(request)
    validated_response = validate_response(
        response,
        validated_request,
        expected_request_sha256=expected_request_sha256,
    )
    if not isinstance(evaluation, dict):
        raise ContractValidationError("evaluation must be an object kept outside the worker DTO.")
    return {"request": validated_request, "response": validated_response, "evaluation": evaluation}
