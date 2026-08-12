# Quantum Centauro

`eic/quantum-centauro` is a source-only, local-Qiskit-Aer candidate selector for the DirectCentauro EICrecon integration. Follow [the integration guide](integration/eicrecon/README.md) for the addition categories and tracked changes, then install this package and run the worker plus one reconstruction wrapper with the same `RUN_DIR`.

| Status | Scope | Evidence status |
| --- | --- | --- |
| `PASS WITH LIMITATIONS` | Local Qiskit Aer plus sibling EICrecon only | Shadow `7080/7080` complete; hard `600/600` complete; E2E `40/40` complete; active `56/300` partial |

The active comparison stopped after an unadmitted comparison-layer `TypeError`. There is no final aggregate. These are progress-coverage records, not aggregate performance statistics.

## Layout

```text
parent/
├── EICrecon/                 # recipient-maintained sibling checkout
└── quantum-centauro/         # this repository
```

| Path | Purpose |
| --- | --- |
| `src/eic_quantum/` | Local Aer worker, payload preparation, and request/response validation |
| `integration/eicrecon/` | Copy-ready C++ additions and the only detailed integration guide |
| `scripts/` | Worker, shadow, and active runtime wrappers |
| `tests/python/` | Exact-wire digest unit test |
| `docs/` | Architecture, method, scope, and evidence boundaries |

## Prerequisites

- Python 3.10 or newer with local `qiskit==2.4.1`, `qiskit-aer==0.17.2`, and `pytest` available.
- A compatible, built sibling `EICrecon/` checkout at the pinned base revision recorded in `integration/eicrecon/README.md`. Acquire it from [the official EICrecon repository](https://github.com/eic/EICrecon); the recipient owns its build, environment, and fixture acquisition.
- External input files named in `examples/input_files.txt`.
- An AF_UNIX-capable local filesystem. No remote providers, QPUs, fake backends, grid services, or network runtime are used.

## Install And Integrate

1. Clone this repository beside the recipient-maintained EICrecon checkout:

   ```bash
   git clone <quantum-centauro-repository-url> quantum-centauro
   cd quantum-centauro
   ```

2. Copy the integration additions into the sibling checkout:

   ```bash
   cp -a integration/eicrecon/additions/. ../EICrecon/
   ```

3. Apply the exact two tracked EICrecon modifications from [the integration guide](integration/eicrecon/README.md).
4. Build the modified sibling with its established EICrecon environment.
5. Install this local package:

   ```bash
   python3 -m pip install .
   ```

## Run

Set recipient-provided paths. `RUN_DIR` must be a new or empty absolute directory; wrappers refuse existing mode outputs and lifecycle targets.

```bash
export RUN_DIR=/absolute/path/to/run
export PYTHON_BIN=python3
export EICRECON_BIN=/absolute/path/to/eicrecon
export EICRECON_TIMEOUT_MILLISECONDS=1000
export INPUT_DIR=/absolute/path/to/inputs
export INPUT_BASENAME="$(sed -n '1p' examples/input_files.txt)"
```

Terminal 1: this command blocks while serving. Keep it running.

```bash
bash scripts/run-worker
```

Terminal 2: after the worker socket is ready, use the **same** `RUN_DIR`.

```bash
bash scripts/run-shadow
# or
bash scripts/run-active
```

The wrappers fix `--max-candidates 128`, `--shots 512`, exponent `3.0`, seed `314159`, and `-Pnthreads=1`. `run-shadow` defaults to `QUANTUM_FAIL_CLOSED=true`; its only exploratory override is `QUANTUM_FAIL_CLOSED=false`. `run-active` is always fail-closed.

| Mode | C++ behavior |
| --- | --- |
| `qiskit_shadow` | Requests and records the local Aer choice, then applies the classical candidate action. |
| `qiskit_active` | Applies only a validated candidate index bound to the exact request bytes. Oversize, invalid, mismatched, timeout, and unavailable replies follow the fail-closed configuration. |

## Output Contract

Shadow writes `RUN_DIR/shadow.edm4eic.root` and `RUN_DIR/shadow.trace.jsonl`; active writes corresponding `active` files. Each wrapper requests exactly:

```text
EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets
```

This preserves the event header, immediate Breit constituents, and DirectCentauro jets. It is not a relation-closed or archival event export. The worker response binds `request_sha256` to SHA-256 of the exact UTF-8 JSON payload bytes, excluding the JSONL LF delimiter.

## Tests

Run the focused protocol test without starting the worker:

```bash
PYTHONPATH=src python3 -m pytest tests/python/test_wire_request_digest.py
```

Use `bash -n scripts/run-worker scripts/run-shadow scripts/run-active` for wrapper syntax. `shellcheck` is optional when installed.

## Limitations

- C++ owns event state, candidate distances, clustering mutation, and EDM output. Python's only actionable reconstruction proposal is a bounded, validated candidate index; diagnostics also carry counts, probabilities, timings, and metadata.
- The method is inverse-distance sampling, not an exact quantum minimum finder.
- No claim is made for quantum speedup, hardware execution, remote execution, production readiness, universal FastJet equivalence, or multithread qualification.
- Evidence is accepted progress recorded by hash-bound external ledgers; raw ledgers are not bundled, so each count is not independently reproducible from this repository alone. Active is partial and the final aggregate is absent. See `evidence/provenance.json` and `docs/project-status.md`. The active failure occurred when no exact constituent-partition match left `max()` with no delta values, causing an unadmitted `comparison_layer_type_error`; earlier accepted rows remain separate.

## Citations

- J. J. Martinez de Lejarza, L. Cieri, and G. Rodrigo, *Quantum clustering and jet reconstruction at the LHC*, Phys. Rev. D 106, 036021 (2022), DOI `10.1103/PhysRevD.106.036021`, arXiv:2204.06496.
- M. Arratia et al., *Asymmetric jet clustering in deep-inelastic scattering*, Phys. Rev. D 104, 034005 (2021), DOI `10.1103/PhysRevD.104.034005`, arXiv:2006.10751.

## License And Provenance

Local Python material is GPL-3.0; see `LICENSE`. The copied EICrecon additions retain their `LGPL-3.0-or-later` SPDX identifiers and `LICENSES/LGPL-3.0.txt`. `NOTICE` records the source provenance and modifications.
