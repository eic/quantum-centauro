# Architecture

The integration preserves a strict ownership boundary: C++ owns event state, candidate distances, clustering mutations, and EDM output. Python's only actionable reconstruction proposal is a validated candidate index through a local AF_UNIX JSONL exchange; its diagnostics also include counts, probabilities, timings, and metadata.

| Component | Delivered path | Responsibility |
| --- | --- | --- |
| Direct reconstruction | `integration/eicrecon/additions/src/algorithms/reco/DirectCentauroJetReconstruction.cc` | Builds candidates, applies merge/beam actions, and writes jets. |
| Selection and transport | `integration/eicrecon/additions/src/algorithms/reco/DirectCentauroJetMinimumSelector.h`, `integration/eicrecon/additions/src/algorithms/reco/DirectCentauroQuantumSocketClient.h` | Classical selection and validated local request/reply transport. |
| Factory | `integration/eicrecon/additions/src/factories/reco/DirectCentauroJetReconstruction_factory.h` | Binds event header, Breit particles, configuration, and output. |
| Local worker | `src/eic_quantum/services/direct_centauro_quantum_worker.py` | Runs local Aer and returns a bounded candidate-index proposal. |
| Contract | `src/eic_quantum/contracts/blind_selector.py` | Validates request and response schema, including wire digest binding. |

`qiskit_shadow` records the Qiskit choice while applying the C++ classical action. `qiskit_active` accepts only a schema-valid index whose response digest matches the exact sent UTF-8 payload, excluding its JSONL LF. The bare EICrecon configuration defaults `quantumFailClosed=false` and may use a classical fallback. `run-active` forces it to `true`; `run-shadow` defaults to `true` and permits only the exploratory `QUANTUM_FAIL_CLOSED=false` override. Oversize, invalid, timeout, and unavailable replies follow that configuration.

An exact-zero pair distance is a deliberate local C++ bypass: it applies the exact-zero candidate without a worker request or digest.

The delivered output contract is `EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets`. It preserves immediate Breit constituents and DirectCentauro jets, not a relation-closed or archival event record.

This is a local Aer and sibling-EICrecon integration only. It contains no remote-provider, hardware, fake-backend, grid, or speedup claim.

## Campaign Vocabulary

Accepted means a ledger row admitted only after validation. Progress is accepted progress recorded by hash-bound external ledgers, not a public aggregate. Raw ledgers are not bundled, so the repository alone cannot independently reproduce every count. The active comparison is partial: no exact constituent-partition match left `max()` with no delta values, causing an unadmitted `comparison_layer_type_error`; earlier accepted rows remain separate.
