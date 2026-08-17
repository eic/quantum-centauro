# Quantum Centauro External Plugin

Quantum Centauro is a standalone, source-only external JANA plugin package installed beside EICrecon. It does **not** patch, vendor, or require a list of changes to EICrecon.

It demonstrates a bounded Direct Centauro selection boundary: EICrecon/C++ owns event state and output, while a local Python selector can return a blind candidate-index proposal. Classical reconstruction remains authoritative; this experimental source package makes no production, detector-wide equivalence, performance-advantage, or quantum-speedup claim.

This source-only experimental deliverable does not certify native configure, build, native tests, plugin loading, linkage, or factory visibility.

## Evidence at a glance

| Historical record | Accepted records | Meaning |
| --- | ---: | --- |
| Shadow selector evaluations | 7,080 | Local diagnostic selector evaluations |
| Hard method measurements | 600 | Method-level measurements |
| Complete ten-event processes | 40 | Complete ten-event processes |
| Active event runs | 300 | Active-mode event runs |
| **Administrative total** | **8,020** | Heterogeneous accepted campaign records |
| Smoke controls | 10 | Separate controls; excluded from the total |

The 8,020 total is an administrative count of heterogeneous accepted campaign records, not 8,020 detector events, shots, or one homogeneous statistical sample. SHA-256 confirms a downloaded file's identity, not scientific validity or plugin certification.

Detailed aggregate evidence is an immutable [v0.2.0 Release asset](https://github.com/eic/quantum-centauro/releases/tag/v0.2.0); historical PDFs and notebooks remain immutable [v0.1.0 Release assets](https://github.com/eic/quantum-centauro/releases/tag/v0.1.0), not canonical-tree files.

## Architecture and modes

| Mode | Behavior |
| --- | --- |
| `classical` | C++ performs the selection without IPC. |
| `shadow` | Sends a blind candidate list to the local selector for diagnostics, then applies the classical action. |
| `active` | Applies only a schema-valid, request-digest-bound candidate index; it is fail-closed. |

C++ owns candidate distances, action application, and EDM output. Python receives a blind candidate list and returns only a candidate index; diagnostics are non-actionable. An exact-zero pair distance is resolved locally by C++.

## Build, discover, load, and run

These are intended recipient procedures, not certified integration results. A compatible exported EICrecon/JANA ABI, CMake 3.24+, a C++20 compiler, and `nlohmann_json` are required; the local worker also needs Python 3.10+, `qiskit==2.4.1`, and `qiskit-aer==0.17.2`.

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH=/opt/eicrecon-install -DCMAKE_INSTALL_PREFIX=/opt/quantum-centauro
cmake --build build
cmake --install build
export EICrecon_MY=/opt/quantum-centauro
```

Current EICrecon source still searches `$EICrecon_MY/plugins`, although external-plugin support was deprecated in upstream [PR #1995](https://github.com/eic/EICrecon/pull/1995). Discovery does not auto-load the plugin, so pass it explicitly:

```bash
eicrecon -Pplugins=quantum_centauro -L
```

Use two terminals with the same fresh `RUN_DIR`; `INPUT_BASENAME` must be listed in `examples/input_files.txt`.

```bash
# Terminal 1
RUN_DIR=/absolute/new-run PYTHON_BIN=python3 bash scripts/run-worker

# Terminal 2: use shadow or active
RUN_DIR=/absolute/new-run INPUT_DIR=/absolute/input INPUT_BASENAME=<approved-input> EICRECON_BIN=eicrecon EICRECON_TIMEOUT_MILLISECONDS=1000 bash scripts/run-reconstruction shadow
```

The wrapper always passes `-Pplugins=quantum_centauro`, uses one EICrecon thread, and fixes the local selector at 128 candidates, 512 shots, exponent 3.0, and seed 314159.

## Limits and manual sanity check

Known EICrecon/JANA export and ABI compatibility blockers remain recipient-environment concerns. No IBM/QPU, remote provider, OSG, token distribution, or executed scientific workload is claimed.

The retained Python protocol test is the manual source sanity check and does not need EICrecon:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -m pytest -p no:cacheprovider tests/python/test_wire_request_digest.py
```

## Repository map and licensing

| Path | Purpose |
| --- | --- |
| `CMakeLists.txt` | External plugin build and install target |
| `plugin/quantum_centauro/` | External JANA plugin source and headers |
| `src/eic_quantum/` | Retained local Python protocol and worker implementation |
| `scripts/` | Recipient worker and reconstruction wrappers |
| `tests/python/test_wire_request_digest.py` | Manual protocol sanity test |
| `examples/input_files.txt` | Approved input-basename list |
| `NOTICE`, `LICENSE`, `LICENSES/` | Provenance and GPL/LGPL license texts |

Python and scripts are [GPL-3.0-only](LICENSE); plugin sources retain LGPL-3.0-or-later SPDX headers with the [LGPL text](LICENSES/LGPL-3.0.txt). See [Releases](https://github.com/eic/quantum-centauro/releases) for immutable historical deliverables.
