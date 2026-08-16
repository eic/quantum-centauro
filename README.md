# Quantum Centauro External Plugin

This tree is an external JANA plugin **source implementation** for the pinned EICrecon revision `fcea66d38d21bf91cd510af3400f76dd8891a8a7`. Native configure, build, CTest, plugin load, linkage, and factory visibility are **not certified** for this migrated external plugin.

## Status

Classical reconstruction remains authoritative. The local selector is bounded and local-only; it does not establish a quantum advantage or replace the classical reconstruction path.

The verified native blocker is exact: pinned EICrecon `fcea66d38d21bf91cd510af3400f76dd8891a8a7` fails against nightly JANA because `JComponentManager::GetSources()` is absent. The fallback installed EICrecon export is separately broken because `services/io/podio/datamodel_glue_legacy.h` is missing. No native build or load result is claimed while either blocker remains.

Historical campaign facts record 7,080 shadow, 600 hard, 40 end-to-end, and 300 active accepted full measurements (8,020 total), plus 10 separate smoke controls. The intended (not yet published) v0.2.0 upload is `quantum-centauro-local-evidence-v0.2.0.tar.gz` with standalone `manifest.json` and `SHA256SUMS`. It binds ten compact aggregate outputs to exact hashes; it is historical campaign evidence, not validation or native certification of this migrated source tree. See `evidence/receipt.json`.

## Recipient Integration Procedure (Uncertified)

These commands describe the intended recipient procedure only; they were not successfully run for this source tree. They require an EICrecon installation with compatible exported CMake packages, JANA dependencies, and ABI; CMake 3.24 or newer; a C++20 compiler; and nlohmann_json. Running the worker also requires Python 3.10 or newer with `qiskit==2.4.1` and `qiskit-aer==0.17.2`. The recipient owns the EICrecon installation and ABI compatibility.

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH=/opt/eicrecon-install -DCMAKE_INSTALL_PREFIX=/opt/quantum-centauro
cmake --build build
cmake --install build
```

If native integration succeeds in a compatible recipient environment, the intended install location is `/opt/quantum-centauro/plugins/quantum_centauro.so`. Tell EICrecon to add that directory through its external-plugin prefix convention:

```bash
export EICrecon_MY=/opt/quantum-centauro
```

If the recipient's installed `eicrecon` supports JANA's factory-list mode, a no-event load and factory/config check is:

```bash
eicrecon -Pplugins=quantum_centauro -L | grep -E 'GeneratedDirectCentauroJets|ReconstructedDirectCentauroJets'
```

This command is an intended recipient check, not a certified load or factory-visibility result.

## Run Locally

Use two terminals with the same fresh `RUN_DIR`. The worker and wrapper reject unsafe paths, unapproved basenames, missing input/socket, and existing output or trace files. `INPUT_BASENAME` must be listed in `examples/input_files.txt`.

Terminal 1 starts the local worker:

```bash
RUN_DIR=/absolute/new-run PYTHON_BIN=python3 bash scripts/run-worker
```

Terminal 2 reconstructs in shadow or active mode:

```bash
RUN_DIR=/absolute/new-run INPUT_DIR=/absolute/input INPUT_BASENAME=<approved-input> EICRECON_BIN=eicrecon EICRECON_TIMEOUT_MILLISECONDS=1000 bash scripts/run-reconstruction shadow
RUN_DIR=/absolute/new-run INPUT_DIR=/absolute/input INPUT_BASENAME=<approved-input> EICRECON_BIN=eicrecon EICRECON_TIMEOUT_MILLISECONDS=1000 bash scripts/run-reconstruction active
```

`run-reconstruction` always passes `-Pplugins=quantum_centauro`, uses the `Reco:ReconstructedDirectCentauroJets:` prefix, caps candidates at `128`, sets `512` shots, exponent `3.0`, seed `314159`, and `-Pnthreads=1`.

## Modes And Ownership

| Mode | Selection behavior |
| --- | --- |
| `shadow` | Sends the candidate-index request to the local Qiskit worker for diagnostics but applies the classical action. It defaults to fail-closed; `QUANTUM_FAIL_CLOSED=false` is an explicitly exploratory override. |
| `active` | Applies only a schema-valid candidate index whose `request_sha256` matches the exact UTF-8 request JSON bytes, excluding the JSONL newline. It is always fail-closed. |

EICrecon/C++ owns event state, candidate distances, action application, and EDM output. Python receives only a blind candidate list and returns the only actionable proposal, a candidate index. Diagnostics are non-actionable. An exact-zero pair distance bypasses the worker and is applied locally by C++.

## Outputs And Limits

The wrapper writes `<mode>.edm4eic.root` and `<mode>.trace.jsonl` under `RUN_DIR`. It deliberately requests only `EventHeader`, `ReconstructedBreitFrameParticles`, and `ReconstructedDirectCentauroJets`. This bounded demo output is not relation-closed and may leave dangling PODIO references if treated as an archival file.

No IBM/QPU, remote provider, OSG, token distribution, network execution, production adoption, performance advantage, or quantum-speedup claim is made by this repository. The retained local worker and Qiskit/Aer references are source implementation only, not an executed workload claim. One EICrecon thread is required by the scientific wrappers for reproducible ordering.

## Tests

The standalone CMake test target is an intended recipient check when a compatible EICrecon dependency is installed; it was not run for this migrated source tree:

```bash
cmake -S . -B build -DBUILD_TESTING=ON -DCMAKE_PREFIX_PATH=/opt/eicrecon-install
cmake --build build
ctest --test-dir build --output-on-failure
```

The focused Python protocol test is independent of EICrecon:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -m pytest -p no:cacheprovider tests/python/test_wire_request_digest.py
```

CI configuration may run the focused Python protocol test and shellcheck. It intentionally does not certify C++ coverage because it does not provision a compatible installed EICrecon dependency.

## Evidence, Provenance, And License

`evidence/receipt.json` distinguishes immutable historical v0.1.0 release evidence from the intended v0.2.0 compact aggregate package and current-tree validation. The v0.2.0 package integrity checks prove the exact packaged aggregate files and their hash bindings, not a new scientific workload or external-plugin native result. Raw ledgers remain external and unbundled, so repository contents alone cannot independently reproduce accepted counts.

Python and scripts are GPL-3.0-only (`LICENSE`). The seven plugin source files retain LGPL-3.0-or-later SPDX headers (`LICENSES/LGPL-3.0.txt`); provenance is in `NOTICE`.

Historical PDF and notebook deliverables belong only to the immutable [v0.1.0 Release](https://github.com/eic/quantum-centauro/releases/tag/v0.1.0); they are intentionally absent from this compact source repository.
