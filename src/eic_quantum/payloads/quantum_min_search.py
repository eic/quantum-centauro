"""Local inverse-power amplitude sampling for bounded candidate lists."""

from __future__ import annotations

import math
from typing import Any


REQUIRED_REQUEST_FIELDS = {
    "request_id", "event_id", "algorithm", "mode", "provider", "shots", "candidates", "metadata"
}
MAX_QISKIT_LOCAL_CANDIDATES = 128
DEFAULT_SIMULATOR_SEED = 314159
DEFAULT_TRANSPILER_SEED = 314159
DEFAULT_EXPONENT_A = 3.0
STABILIZED_STATE_PREPARATION_METHOD = "stabilized_state_preparation"
STABILIZED_STATE_PREPARATION_VERSION = "v1"
RECURSIVE_CONTROLLED_RY_METHOD = "recursive_controlled_ry"
RELATIVE_PROBABILITY_CUTOFF = 1.0e-12


class QiskitLocalDependencyError(RuntimeError):
    """Report missing local Qiskit dependencies for the sampler method."""


def _require_string(request: dict[str, Any], name: str) -> str:
    """Return a required non-empty string field or raise ``ValueError``."""
    value = request.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string.")
    return value


def _require_nonnegative_int(request: dict[str, Any], name: str) -> int:
    """Return a required non-negative integer field or raise ``ValueError``."""
    value = request.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def validate_request(request: Any) -> dict[str, Any]:
    """Validate the local request contract and finite non-negative distances.

    Returns the original request. Raises ``ValueError`` for malformed fields.
    """
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object.")
    missing = sorted(REQUIRED_REQUEST_FIELDS - request.keys())
    if missing:
        raise ValueError(f"Request is missing required fields: {', '.join(missing)}.")
    for name in ("request_id", "algorithm", "mode", "provider"):
        _require_string(request, name)
    _require_nonnegative_int(request, "event_id")
    if _require_nonnegative_int(request, "shots") == 0:
        raise ValueError("shots must be greater than zero.")
    if request["mode"] != "shadow":
        raise ValueError("mode must be 'shadow'.")
    if request["provider"] != "local":
        raise ValueError("provider must be 'local'.")
    if not isinstance(request["metadata"], dict):
        raise ValueError("metadata must be an object.")
    candidates = request["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty array.")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidates[{index}] must be an object.")
        if candidate.get("kind") not in {"pair", "beam"}:
            raise ValueError(f"candidates[{index}].kind must be 'pair' or 'beam'.")
        if not isinstance(candidate.get("i"), int) or isinstance(candidate["i"], bool) or candidate["i"] < 0:
            raise ValueError(f"candidates[{index}].i must be a non-negative integer.")
        if candidate["kind"] == "pair" and (not isinstance(candidate.get("j"), int) or isinstance(candidate["j"], bool) or candidate["j"] < 0):
            raise ValueError(f"candidates[{index}].j must be a non-negative integer for a pair.")
        if candidate["kind"] == "beam" and candidate.get("j") is not None:
            raise ValueError(f"candidates[{index}].j must be null for a beam candidate.")
        distance = candidate.get("distance")
        if not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance) or distance < 0:
            raise ValueError(f"candidates[{index}].distance must be a finite non-negative number.")
    return request


def classical_reference(request: dict[str, Any]) -> dict[str, Any]:
    """Return the first classical minimum, deterministically.

    Raises ``ValueError`` when the request contract is invalid.
    """
    validate_request(request)
    selected_index, selected = min(enumerate(request["candidates"]), key=lambda item: (item[1]["distance"], item[0]))
    return {"request_id": request["request_id"], "status": "ok", "selected_candidate_index": selected_index, "selected_kind": selected["kind"], "i": selected["i"], "j": selected["j"], "distance": selected["distance"], "method": "classical_reference_deterministic_argmin", "provider": "local", "shots": request["shots"], "hardware_submission": False, "warnings": [], "metadata": {"algorithm": request["algorithm"], "mode": request["mode"], "tie_breaking": "first candidate index", "source_metadata": request["metadata"]}}


def _require_metadata_seed(metadata: dict[str, Any], name: str, default: int) -> int:
    """Return a non-negative deterministic seed from metadata or its default."""
    value = metadata.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"metadata.{name} must be a non-negative integer.")
    return value


def _require_exponent_a(metadata: dict[str, Any]) -> float:
    """Return the positive finite inverse-power exponent from metadata."""
    value = metadata.get("exponent_a", DEFAULT_EXPONENT_A)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("metadata.exponent_a must be a positive finite number.")
    return float(value)


def _next_power_of_two_dimension(candidate_count: int) -> int:
    """Return the smallest statevector dimension that can encode candidates."""
    return max(2, 1 << (candidate_count - 1).bit_length())


def inverse_power_probabilities(distances: list[float], exponent_a: float) -> list[float]:
    """Normalize probabilities for amplitudes proportional to ``d_i ** (-a)``.

    Probability weights are therefore proportional to ``d_i ** (-2a)``.
    Normalization happens in the log domain after subtracting the largest log
    weight.  This is equivalent to dividing by a positive reference weight, but
    never derives that reference from a classical selected index or argmin.

    Raises ``ValueError`` when distances are not finite positive values or ``a <= 0``.
    """
    if not distances or any(not math.isfinite(distance) or distance <= 0 for distance in distances):
        raise ValueError("inverse-power probabilities require finite positive distances.")
    if not math.isfinite(exponent_a) or exponent_a <= 0:
        raise ValueError("exponent_a must be a positive finite number.")
    log_weights = [-2.0 * exponent_a * math.log(distance) for distance in distances]
    reference_log_weight = max(log_weights)
    scores = [math.exp(log_weight - reference_log_weight) for log_weight in log_weights]
    total = math.fsum(scores)
    return [score / total for score in scores]


def _load_qiskit() -> tuple[Any, Any, Any]:
    """Load Qiskit V2 sampling dependencies or raise a clear local error."""
    try:
        from qiskit import QuantumCircuit
        from qiskit.circuit.library import StatePreparation
        from qiskit_aer.primitives import SamplerV2
    except ImportError as exc:
        raise QiskitLocalDependencyError("paper sampler requires local 'qiskit' and 'qiskit-aer' packages.") from exc
    return QuantumCircuit, StatePreparation, SamplerV2


def stabilize_probabilities(probabilities: list[float]) -> tuple[list[float], dict[str, float | str]]:
    """Drop only numerically negligible probability mass and renormalize exactly.

    The relative cutoff keeps the generic Qiskit isometry away from rotations
    with condition numbers above 1e6 in amplitude space.  It is independent of
    candidate identity and preserves the ordered blind request.
    """
    if not probabilities or any(not math.isfinite(value) or value < 0 for value in probabilities):
        raise ValueError("probabilities must be finite and non-negative")
    largest = max(probabilities)
    if largest <= 0:
        raise ValueError("probability vector has no positive mass")
    cutoff = largest * RELATIVE_PROBABILITY_CUTOFF
    retained = [value if value >= cutoff else 0.0 for value in probabilities]
    dropped_mass = math.fsum(value for value, value_retained in zip(probabilities, retained) if value_retained == 0.0)
    retained_mass = math.fsum(retained)
    if retained_mass <= 0:
        raise ValueError("probability stabilization removed all mass")
    stabilized = [value / retained_mass for value in retained]
    fidelity = math.fsum(math.sqrt(source * result) for source, result in zip(probabilities, stabilized)) ** 2
    return stabilized, {
        "method": STABILIZED_STATE_PREPARATION_METHOD,
        "version": STABILIZED_STATE_PREPARATION_VERSION,
        "dropped_probability_mass": dropped_mass,
        "state_fidelity": fidelity,
        "cutoff": RELATIVE_PROBABILITY_CUTOFF,
    }


def build_inverse_power_circuit(distances: list[float], exponent_a: float) -> tuple[Any, list[float], list[float]]:
    """Build a measured, zero-padded inverse-power state-preparation circuit.

    Returns ``(circuit, amplitudes, ideal_probabilities)`` for strictly positive distances.
    """
    circuit, amplitudes, probabilities, _ = build_inverse_power_circuit_with_metadata(distances, exponent_a)
    return circuit, amplitudes, probabilities


def build_inverse_power_circuit_with_metadata(
    distances: list[float], exponent_a: float, *, method: str = STABILIZED_STATE_PREPARATION_METHOD
) -> tuple[Any, list[float], list[float], dict[str, float | str]]:
    """Build the selected bounded state-preparation implementation."""
    probabilities = inverse_power_probabilities(distances, exponent_a)
    stabilized, metadata = stabilize_probabilities(probabilities)
    dimension = _next_power_of_two_dimension(len(distances))
    amplitudes = [math.sqrt(probability) for probability in stabilized] + [0.0] * (dimension - len(distances))
    # StatePreparation ultimately validates a floating-point unitary.  The
    # probability normalization above is mathematically sufficient, but an
    # independently rounded square-root vector can miss that check by a few
    # ulps for wide candidate ranges.  Renormalize the encoded vector only;
    # candidate weights, ordering, shots, exponent, and seed remain unchanged.
    amplitude_norm = math.sqrt(math.fsum(amplitude * amplitude for amplitude in amplitudes))
    if not math.isfinite(amplitude_norm) or amplitude_norm <= 0.0:
        raise ValueError("inverse-power amplitude vector has invalid norm.")
    amplitudes = [amplitude / amplitude_norm for amplitude in amplitudes]
    QuantumCircuit, StatePreparation, _ = _load_qiskit()
    qubits = dimension.bit_length() - 1
    circuit = QuantumCircuit(qubits)
    if method == STABILIZED_STATE_PREPARATION_METHOD:
        circuit.append(StatePreparation(amplitudes, normalize=True), range(qubits))
    elif method == RECURSIVE_CONTROLLED_RY_METHOD:
        _append_real_amplitude_state(circuit, amplitudes, list(range(qubits)), [])
        metadata = metadata | {"method": RECURSIVE_CONTROLLED_RY_METHOD, "version": "v1"}
    else:
        raise ValueError("unknown state-preparation method")
    circuit.measure_all()
    return circuit, amplitudes, stabilized, metadata


def _append_real_amplitude_state(
    circuit: Any, amplitudes: list[float], qubits: list[int], controls: list[tuple[int, int]]
) -> None:
    """Prepare a non-negative real state with controlled RY rotations only.

    Qiskit's generic ``StatePreparation`` delegates to isometry synthesis, which
    reconstructs numerically delicate one-qubit unitaries for the item75 vector.
    This recursive construction uses the same normalized amplitudes but only
    RY/CX-level operations, avoiding that unrelated decomposition instability.
    """
    if len(amplitudes) == 1:
        return
    midpoint = len(amplitudes) // 2
    left, right = amplitudes[:midpoint], amplitudes[midpoint:]
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    target = qubits[-1]
    angle = 2.0 * math.atan2(right_norm, left_norm)
    zero_controls = [qubit for qubit, value in controls if value == 0]
    for qubit in zero_controls:
        circuit.x(qubit)
    if controls:
        circuit.mcry(angle, [qubit for qubit, _ in controls], target, None, mode="noancilla")
    else:
        circuit.ry(angle, target)
    for qubit in reversed(zero_controls):
        circuit.x(qubit)
    if left_norm:
        _append_real_amplitude_state(circuit, [value / left_norm for value in left], qubits[:-1], controls + [(target, 0)])
    if right_norm:
        _append_real_amplitude_state(circuit, [value / right_norm for value in right], qubits[:-1], controls + [(target, 1)])


def _sample_counts(circuit: Any, shots: int, simulator_seed: int) -> tuple[dict[str, int], None, str]:
    """Sample through Aer SamplerV2 only, returning its runtime identifier.

    Inputs are a measured circuit, shot count, and deterministic simulator seed.
    Returns counts, unavailable transpiled depth, and the implementation that
    executed the circuit. Aer compiles internally so it does not trigger Qiskit
    preset-plugin discovery, which can transitively load external runtime hooks.
    """
    _, _, AerSamplerV2 = _load_qiskit()
    sampler = AerSamplerV2(default_shots=shots, seed=simulator_seed)
    # This is local circuit expansion, not preset transpilation, and avoids
    # Runtime plugin discovery.
    result = sampler.run([circuit.decompose(reps=10)], shots=shots).result()
    counts = result[0].data.meas.get_counts()
    sampler_class = AerSamplerV2
    runtime = f"{sampler_class.__module__.rsplit('.', 1)[0]}.{sampler_class.__qualname__}"
    return {bitstring: int(count) for bitstring, count in sorted(counts.items())}, None, runtime


def paper_inverse_power_amplitude_sampling(request: dict[str, Any]) -> dict[str, Any]:
    """Sample an inverse-power amplitude encoding and report its scientific metadata.

    Exact zeros use an explicit first-ordered zero bypass rather than inverse-power
    state preparation. Positive candidates are sampled without a classical argmin
    or minimum-set input. This is not an exact quantum minimum algorithm and makes
    no speedup claim.
    """
    validate_request(request)
    candidates = request["candidates"]
    if len(candidates) > MAX_QISKIT_LOCAL_CANDIDATES:
        raise ValueError(f"paper sampler supports at most {MAX_QISKIT_LOCAL_CANDIDATES} candidates; received {len(candidates)}.")
    metadata = request["metadata"]
    exponent_a = _require_exponent_a(metadata)
    simulator_seed = _require_metadata_seed(metadata, "simulator_seed", DEFAULT_SIMULATOR_SEED)
    transpiler_seed = _require_metadata_seed(metadata, "transpiler_seed", DEFAULT_TRANSPILER_SEED)
    distances = [float(candidate["distance"]) for candidate in candidates]
    zero_index = next((index for index, distance in enumerate(distances) if distance == 0.0), None)
    base = {"request_id": request["request_id"], "status": "ok", "method": "paper_inverse_power_amplitude_sampling", "provider": "local", "shots": request["shots"], "exponent_a": exponent_a, "runtime": None, "exact_quantum_minimum": False, "quantum_speedup_claimed": False, "hardware_submission": False}
    if zero_index is not None:
        return base | {"sampled_index": zero_index, "counts_by_bitstring": {}, "counts_by_candidate": {str(index): 0 for index in range(len(candidates))}, "amplitudes": [], "ideal_probabilities": [], "qubit_count": 0, "circuit_depth": 0, "transpiled_depth": None, "quantum_bypassed_zero_minimum": True}
    circuit, amplitudes, probabilities = build_inverse_power_circuit(distances, exponent_a)
    counts_by_bitstring, transpiled_depth, runtime = _sample_counts(circuit, request["shots"], simulator_seed)
    counts = [0] * len(candidates)
    for bitstring, count in counts_by_bitstring.items():
        index = int(bitstring.replace(" ", ""), 2)
        if index < len(candidates):
            counts[index] += count
    sampled_index = max(range(len(candidates)), key=lambda index: (counts[index], -index))
    return base | {"sampled_index": sampled_index, "counts_by_bitstring": counts_by_bitstring, "counts_by_candidate": {str(index): count for index, count in enumerate(counts)}, "amplitudes": amplitudes, "ideal_probabilities": probabilities, "qubit_count": circuit.num_qubits, "circuit_depth": circuit.depth(), "transpiled_depth": transpiled_depth, "quantum_bypassed_zero_minimum": False, "runtime": runtime}


def run_request(request: dict[str, Any], method: str) -> dict[str, Any]:
    """Run one supported local method without provider or credential access."""
    if method == "classical_reference":
        return classical_reference(request)
    if method == "paper_inverse_power_amplitude_sampling":
        return paper_inverse_power_amplitude_sampling(request)
    raise ValueError("method must be 'classical_reference' or 'paper_inverse_power_amplitude_sampling'.")
