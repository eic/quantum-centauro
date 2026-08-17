# Quantum Centauro

Quantum Centauro is a source-only, experimental external JANA plugin installed beside EICrecon. It does not patch or vendor EICrecon.

It demonstrates a bounded Direct Centauro selection boundary: C++ owns event state, action application, and EDM output; a local Python selector returns only a blind candidate-index proposal. Classical reconstruction remains authoritative. Native compatibility is not certified, and upstream external-plugin support is deprecated.

## How to use

**Prerequisites and status.** Use a compatible EIC environment, CMake 3.24+, a C++20 compiler, `nlohmann_json`, Python 3.10+, and a locally available allowlisted input ROOT file. This is an **uncertified recipient procedure**: no compatible EICrecon SHA or container, configure/build result, ABI/linkage result, plugin loading result, or factory visibility is certified. Upstream recommends [eic-shell](https://eic.github.io/EICrecon/#/get-started/eic-shell) for the EIC environment.

1. **Create a workspace and clone both repositories.**

   ```bash
   WORK="$HOME/eic-quantum"
   EIC_PREFIX="$WORK/install/eicrecon"
   QC_PREFIX="$WORK/install/quantum-centauro"
   mkdir -p "$WORK"
   git clone https://github.com/eic/EICrecon.git "$WORK/EICrecon"
   git clone https://github.com/eic/quantum-centauro.git "$WORK/quantum-centauro"
   ```

2. **Enter a compatible EIC environment, then build and install EICrecon to a workspace-local prefix.** Do not treat these commands as a certified SHA or container recipe.

   ```bash
   # Start the compatible environment; upstream recommends eic-shell.
   eic-shell

   mkdir -p "$EIC_PREFIX"
   cmake -S "$WORK/EICrecon" -B "$WORK/build/eicrecon" \
     -DCMAKE_INSTALL_PREFIX="$EIC_PREFIX"
   cmake --build "$WORK/build/eicrecon"
   cmake --install "$WORK/build/eicrecon"
   ```

3. **Create the Python environment, install Quantum Centauro, then configure, build, and install the plugin.**

   ```bash
   python3 -m venv "$WORK/.venv"
   "$WORK/.venv/bin/python" -m pip install --upgrade pip
   "$WORK/.venv/bin/python" -m pip install "$WORK/quantum-centauro"

   mkdir -p "$QC_PREFIX"
   cmake -S "$WORK/quantum-centauro" -B "$WORK/build/quantum-centauro" \
     -DCMAKE_PREFIX_PATH="$EIC_PREFIX" \
     -DCMAKE_INSTALL_PREFIX="$QC_PREFIX"
   cmake --build "$WORK/build/quantum-centauro"
   cmake --install "$WORK/build/quantum-centauro"
   ```

4. **Source EICrecon, configure plugin discovery, and explicitly activate the plugin.** `EICrecon_MY` supports discovery of `$EICrecon_MY/plugins`; it does **not** auto-load this plugin. Preserve any existing `JANA_PLUGIN_PATH` entries and load Quantum Centauro explicitly.

   ```bash
   source "$EIC_PREFIX/bin/eicrecon-this.sh"
   export EICrecon_MY="$QC_PREFIX"
   export JANA_PLUGIN_PATH="$QC_PREFIX/plugins${JANA_PLUGIN_PATH:+:$JANA_PLUGIN_PATH}"
   eicrecon -Pplugins=quantum_centauro -L
   ```

   **Troubleshooting gate:** stop here unless `-L` shows both `GeneratedDirectCentauroJets` and `ReconstructedDirectCentauroJets`. Do not proceed to a worker or reconstruction run when either factory is absent.

5. **Provide an allowlisted input.** The repository does not distribute input ROOT files. Set `INPUT_BASENAME` to one line copied exactly from [`examples/input_files.txt`](examples/input_files.txt), and ensure that file exists directly beneath `INPUT_DIR`.

   ```bash
   INPUT_DIR="/absolute/path/to/input-directory"
   INPUT_BASENAME="pythia8NCDIS_10x275_minQ2=10_beamEffects_xAngle=-0.025_hiDiv_1.0171.edm4hep.root"
   test -f "$INPUT_DIR/$INPUT_BASENAME"
   ```

6. **Run `shadow` first, in two terminals.** Create the parent directory before setting a fresh, non-root absolute `RUN_DIR`; use the same values in both terminals. A `RUN_DIR` cannot be reused once the worker socket, lifecycle file, output, or trace exists.

   ```bash
   # Prepare once, then use the saved value in both terminals.
   RUN_PARENT="$WORK/runs"
   mkdir -p "$RUN_PARENT"
   RUN_DIR="$RUN_PARENT/shadow-$(date +%Y%m%d-%H%M%S)"
   printf '%s\n' "$RUN_DIR" > "$WORK/.quantum-centauro-shadow-run-dir"
   ```

   ```bash
   # Terminal 1: start the local worker.
   RUN_DIR="$(<"$WORK/.quantum-centauro-shadow-run-dir")"
   RUN_DIR="$RUN_DIR" PYTHON_BIN="$WORK/.venv/bin/python" \
     bash "$WORK/quantum-centauro/scripts/run-worker"
   ```

   ```bash
   # Terminal 2: while the worker is running.
   RUN_DIR="$(<"$WORK/.quantum-centauro-shadow-run-dir")"
   RUN_DIR="$RUN_DIR" \
   INPUT_DIR="$INPUT_DIR" \
   INPUT_BASENAME="$INPUT_BASENAME" \
   EICRECON_BIN=eicrecon \
   EICRECON_TIMEOUT_MILLISECONDS=1000 \
     bash "$WORK/quantum-centauro/scripts/run-reconstruction" shadow
   ```

7. **Only after a successful shadow run, repeat for `active`.** Stop the shadow worker, create a **new** absolute `RUN_DIR`, restart the worker with that new directory, and run the reconstruction command with `active`. Do not reuse the shadow directory or its worker.

   ```bash
   RUN_PARENT="$WORK/runs"
   mkdir -p "$RUN_PARENT"
   RUN_DIR="$RUN_PARENT/active-$(date +%Y%m%d-%H%M%S)"
   printf '%s\n' "$RUN_DIR" > "$WORK/.quantum-centauro-active-run-dir"
   ```

   In each terminal, repeat its step 6 command with `RUN_DIR="$(<"$WORK/.quantum-centauro-active-run-dir")"`; keep the same `PYTHON_BIN`, `INPUT_DIR`, `INPUT_BASENAME`, `EICRECON_BIN`, and `EICRECON_TIMEOUT_MILLISECONDS=1000`, and change only the final reconstruction mode from `shadow` to `active`.

   Expected outputs are `$RUN_DIR/shadow.edm4eic.root` and `$RUN_DIR/shadow.trace.jsonl` for shadow, or `$RUN_DIR/active.edm4eic.root` and `$RUN_DIR/active.trace.jsonl` for active. The worker also creates `$RUN_DIR/direct-centauro-worker.lifecycle.json` while it runs.

## Modes

| Mode | Behavior |
| --- | --- |
| `classical` | C++ performs selection without IPC. |
| `shadow` | Sends a blind candidate list to the local selector for diagnostics, then applies the classical action. |
| `active` | Applies only a schema-valid, request-digest-bound candidate index; it is fail-closed. |

Python receives only a blind candidate list and returns only a candidate index; diagnostics are non-actionable. An exact-zero pair distance is resolved locally by C++.

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

Detailed aggregate evidence is published in the [v0.2.0 Release](https://github.com/eic/quantum-centauro/releases/tag/v0.2.0); historical PDFs and notebooks remain in the [v0.1.0 Release](https://github.com/eic/quantum-centauro/releases/tag/v0.1.0), not in the canonical source tree.

## Limits

This experimental external-plugin path does not patch EICrecon and is not certified for native compatibility. EICrecon/JANA export and ABI compatibility remain recipient-environment concerns; upstream external-plugin support was deprecated in [PR #1995](https://github.com/eic/EICrecon/pull/1995). No IBM/QPU, remote provider, OSG, production deployment, speedup, token distribution, or executed scientific workload is claimed.

## Repository map

| Path | Purpose |
| --- | --- |
| `CMakeLists.txt` | External plugin build and install target |
| `plugin/quantum_centauro/` | External JANA plugin source and headers |
| `src/eic_quantum/` | Retained local Python protocol and worker implementation |
| `scripts/` | Recipient worker and reconstruction wrappers |
| `tests/python/test_wire_request_digest.py` | Manual protocol sanity test |
| `examples/input_files.txt` | Approved input-basename list |
| `NOTICE`, `LICENSE`, `LICENSES/` | Provenance and GPL/LGPL license texts |

## Source sanity check

The retained Python protocol test is a manual source sanity check and does not require EICrecon:

```bash
cd "$WORK/quantum-centauro"
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
  "$WORK/.venv/bin/python" -m pytest -p no:cacheprovider tests/python/test_wire_request_digest.py
```

## License/Releases

Python and scripts are [GPL-3.0-only](LICENSE); plugin sources retain LGPL-3.0-or-later SPDX headers with the [LGPL text](LICENSES/LGPL-3.0.txt). See [Releases](https://github.com/eic/quantum-centauro/releases) for versioned historical deliverables.
