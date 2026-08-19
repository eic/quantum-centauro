# Quantum Centauro

Quantum Centauro is a source-only, experimental external JANA plugin installed beside EICrecon. It does not patch or vendor EICrecon.

It demonstrates a bounded Direct Centauro selection boundary: C++ owns event state, action application, and EDM output; a local Python selector returns only a blind candidate-index proposal. Classical reconstruction remains authoritative. Native compatibility is not certified, and upstream external-plugin support is deprecated.

## How to use

The two official workflows run the external plugin directly inside an already-prepared [eic-shell](https://eic.github.io/EICrecon/#/get-started/eic-shell). They build against the EICrecon bundled by the active eic-shell. A normal workload needs only an existing input ROOT file.

### Install or update once inside eic-shell

Run `eic-shell` if it is globally installed; otherwise use the workspace launcher. **Create `.venv-eic` inside eic-shell and never reuse a host-created virtual environment.** Its Python ABI and Qiskit installation must match the shell environment.

On the host:

```bash
WORK="$HOME/eic"
"$WORK/eic-shell"
```

Inside eic-shell, use the actual workspace paths and prepare the bundled detector/EICrecon environment:

```bash
WORK="$HOME/eic"
REPO="$WORK/quantum-centauro"
QC_PREFIX="$WORK/install/quantum-centauro"
source /opt/detector/epic-main/bin/thisepic.sh epic_craterlake
command -v eicrecon
```

For a **first install**, run inside eic-shell:

```bash
python3 -m venv "$REPO/.venv-eic"
"$REPO/.venv-eic/bin/python" -m pip install "$REPO"
cmake -S "$REPO" -B "$WORK/build/quantum-centauro" -DCMAKE_INSTALL_PREFIX="$QC_PREFIX"
cmake --build "$WORK/build/quantum-centauro"
cmake --install "$WORK/build/quantum-centauro"
```

For a **subsequent update**, preserve a valid eic-shell-created virtual environment; do not delete or recreate it just to update the checkout:

```bash
"$REPO/.venv-eic/bin/python" -m pip install --upgrade "$REPO"
cmake -S "$REPO" -B "$WORK/build/quantum-centauro" -DCMAKE_INSTALL_PREFIX="$QC_PREFIX"
cmake --build "$WORK/build/quantum-centauro"
cmake --install "$WORK/build/quantum-centauro"
```

Export the installed prefix and pass the fast gates before a workload:

```bash
export QUANTUM_CENTAURO_PREFIX="$QC_PREFIX"
source "$REPO/.venv-eic/bin/activate"
command -v qc-run
test -f "$QUANTUM_CENTAURO_PREFIX/plugins/quantum_centauro.so" && test ! -L "$QUANTUM_CENTAURO_PREFIX/plugins/quantum_centauro.so"
qc-run --help
```

Expected results: `command -v` prints the installed command path, the plugin test exits with status 0, and help starts with `usage: qc-run`. Plugin discovery is used by EICrecon through `QUANTUM_CENTAURO_PREFIX`, then `EICrecon_MY`, then `JANA_PLUGIN_PATH`. For its managed worker and EICrecon processes, `qc-run` constructs the needed child-process environment without mutating the parent shell.

> **Advanced custom EICrecon:** this is not the normal eic-shell path. If you deliberately use a custom installation, source its `eicrecon-this.sh` before configuring the plugin and add `-DCMAKE_PREFIX_PATH="$EIC_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"` to the one-line CMake configure command above.

### Official mode A: one terminal

This is the normal workflow: `qc-run` owns the local Aer worker and stops it after both modes finish. Start on the host, then run each remaining command inside eic-shell:

```bash
WORK="$HOME/eic"
"$WORK/eic-shell"
WORK="$HOME/eic"
REPO="$WORK/quantum-centauro"
QC_PREFIX="$WORK/install/quantum-centauro"
source /opt/detector/epic-main/bin/thisepic.sh epic_craterlake
source "$REPO/.venv-eic/bin/activate"
export QUANTUM_CENTAURO_PREFIX="$QC_PREFIX"
INPUT="REPLACE_WITH_ABSOLUTE_PATH_TO_EXISTING_ROOT_FILE"
test -f "$INPUT"
qc-run "$INPUT" --event-index 6
```

Replace `INPUT` once with the absolute path to your existing regular `.root` file; do not continue until `test -f "$INPUT"` succeeds. `INPUT` is the only mandatory workload argument. Defaults are event index `0`, one event, `shadow` then `active`, a 5000 ms selector timeout, automatic collision-safe `./runs/qc-*`, deterministic Aer settings (512 shots, exponent 3.0, seed 314159, 128 candidates), and classical fallback when the selector cannot provide a valid decision.

Safe optional overrides remain one command:

```bash
qc-run "$INPUT" --event-index 6 --timeout-milliseconds 10000 --plugin-prefix "$QC_PREFIX" --eicrecon "$(command -v eicrecon)"
```

On success, expect `Starting local worker in ...`, `Running shadow reconstruction...`, `Completed shadow reconstruction.`, corresponding active messages, and `Completed shadow and active reconstruction. Run directory: ...`. The worker's readiness and request activity are in `worker.log`. The generated directory contains at least:

```text
runs/qc-YYYYMMDD-HHMMSS/
├── direct-centauro-worker.lifecycle.json
├── worker.log
├── shadow.edm4eic.root
├── shadow.reconstruction.log
├── shadow.trace.jsonl
├── active.edm4eic.root
├── active.reconstruction.log
└── active.trace.jsonl
```

EICrecon uses that private directory as its working directory, so detector cache links, if created, stay there instead of in the checkout or caller directory.

### Official mode B: two terminals

Use this mode for learning or debugging: **Terminal A owns the worker; Terminal B owns EICrecon.** Both terminals must use the exact same fresh `RUN_DIR`. This example uses `$HOME/eic/runs/qc-shadow-manual-001`; increment the suffix before retrying if it already exists. Shadow and active must use separate directories and workers so their sockets, lifecycle receipts, outputs, and traces remain distinct.

#### Shadow — Terminal A (worker)

On the host, enter eic-shell, then run the following inside it. Each executable command is one line to avoid paste-whitespace failures.

```bash
WORK="$HOME/eic"
"$WORK/eic-shell"
WORK="$HOME/eic"
REPO="$WORK/quantum-centauro"
source "$REPO/.venv-eic/bin/activate"
RUN_DIR="$WORK/runs/qc-shadow-manual-001"
test ! -e "$RUN_DIR"
qc-worker --run-dir "$RUN_DIR"
```

Current output starts with `Starting local worker in ...` and `Worker ready at ... Keep this terminal open; press Ctrl-C after reconstruction finishes.` Each selector request prints `Processed selector request #...`; `Ctrl-C` finishes with `Worker stopped cleanly.`

#### Shadow — Terminal B (EICrecon)

In a second terminal, enter eic-shell and prepare the detector, package, plugin, input, and the **same** `RUN_DIR`:

```bash
WORK="$HOME/eic"
"$WORK/eic-shell"
WORK="$HOME/eic"
REPO="$WORK/quantum-centauro"
QC_PREFIX="$WORK/install/quantum-centauro"
source /opt/detector/epic-main/bin/thisepic.sh epic_craterlake
source "$REPO/.venv-eic/bin/activate"
export QUANTUM_CENTAURO_PREFIX="$QC_PREFIX"
INPUT="REPLACE_WITH_ABSOLUTE_PATH_TO_EXISTING_ROOT_FILE"
test -f "$INPUT"
RUN_DIR="$WORK/runs/qc-shadow-manual-001"
test -d "$RUN_DIR"
test -f "$QUANTUM_CENTAURO_PREFIX/plugins/quantum_centauro.so" && test ! -L "$QUANTUM_CENTAURO_PREFIX/plugins/quantum_centauro.so"
qc-reconstruct shadow "$INPUT" --run-dir "$RUN_DIR" --event-index 6
```

Terminal B prints `Running shadow reconstruction...`, `This can take a few minutes.`, and `Detailed reconstruction log: ...` before EICrecon starts. On success it prints `Completed shadow reconstruction.`, the ROOT and trace paths, the log path, and the reminder that Terminal A remains running. If it appears to remain at `Running`, inspect the exact log path already printed; successful current packages explicitly print all output paths.

After Terminal B completes, press `Ctrl-C` in Terminal A. From either eic-shell terminal, these read-only gates prove shutdown and artifacts without `jq`:

```bash
test ! -S "$RUN_DIR/direct-centauro-worker.sock" && printf '%s\n' 'worker socket removed'
"$REPO/.venv-eic/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["state"])' "$RUN_DIR/direct-centauro-worker.lifecycle.json"
test -f "$RUN_DIR/shadow.edm4eic.root" && test -f "$RUN_DIR/shadow.trace.jsonl" && test -f "$RUN_DIR/shadow.reconstruction.log"
```

Expected output is `worker socket removed` followed by `stopped`.

#### Active — use a new worker and directory

In **Terminal A**, after entering eic-shell and activating `$REPO/.venv-eic` as above:

```bash
RUN_DIR="$WORK/runs/qc-active-manual-001"
test ! -e "$RUN_DIR"
qc-worker --run-dir "$RUN_DIR"
```

In **Terminal B**, after its detector, virtual-environment, prefix, and `INPUT` setup above:

```bash
RUN_DIR="$WORK/runs/qc-active-manual-001"
test -d "$RUN_DIR"
test -f "$QUANTUM_CENTAURO_PREFIX/plugins/quantum_centauro.so" && test ! -L "$QUANTUM_CENTAURO_PREFIX/plugins/quantum_centauro.so"
qc-reconstruct active "$INPUT" --run-dir "$RUN_DIR" --event-index 6
```

After completion, press `Ctrl-C` in Terminal A. Check the stopped lifecycle, active artifacts, and active trace read-only:

```bash
test ! -S "$RUN_DIR/direct-centauro-worker.sock" && printf '%s\n' 'worker socket removed'
"$REPO/.venv-eic/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["state"])' "$RUN_DIR/direct-centauro-worker.lifecycle.json"
test -f "$RUN_DIR/active.edm4eic.root" && test -f "$RUN_DIR/active.trace.jsonl" && test -f "$RUN_DIR/active.reconstruction.log"
"$REPO/.venv-eic/bin/python" -c 'import json,sys; line=open(sys.argv[1], encoding="utf-8").readline(); trace=json.loads(line); print({key: trace.get(key) for key in ("final_source", "fallback", "decision_reason_code")})' "$RUN_DIR/active.trace.jsonl"
```

### Troubleshooting

- `qc-run: command not found`: activate `$REPO/.venv-eic` **inside eic-shell** with `source "$REPO/.venv-eic/bin/activate"`.
- `ModuleNotFoundError`: the virtual environment was created with host Python. Recreate it inside eic-shell and install the package there; never reuse the host environment.
- Plugin not discoverable: export `QUANTUM_CENTAURO_PREFIX="$QC_PREFIX"`, then test `test -f "$QC_PREFIX/plugins/quantum_centauro.so" && test ! -L "$QC_PREFIX/plugins/quantum_centauro.so"`.
- `INPUT must name an existing regular .root file`: set `INPUT` to a real absolute `.root` file and require `test -f "$INPUT"` before either workload.
- `unrecognized arguments:` after pasting: pasted backslashes plus spaces were treated as arguments. Copy the documented one-line executable commands with no trailing backslash.
- Terminal B appears at `Running`: inspect the exact `Detailed reconstruction log: ...` path it prints. Current success explicitly prints ROOT, trace, and log outputs.
- Terminal A waits silently: update the installed `.venv-eic` package. The current package prints readiness and request activity, and `Ctrl-C` is clean.

### Advanced host + Singularity path

`scripts/run-local-aer` is a secondary host-plus-container workflow, not an official direct eic-shell mode. It requires host `bash`, Singularity, the source checkout, and a host Python with the pinned Qiskit/Aer runtime dependencies; package installation is optional because the wrapper injects the checkout's `src` directory into `PYTHONPATH`. Set `EIC_CONTAINER_BIND_ROOT`, `EIC_CONTAINER_SIF`, `EICRECON_PREFIX`, and `QUANTUM_CENTAURO_PREFIX`; the SIF, EICrecon prefix, plugin prefix, `INPUT`, and fresh `RUN_DIR` must be absolute paths below the bind root. The EICrecon prefix must contain `bin/eicrecon-this.sh` and executable `bin/eicrecon`; the plugin must be a non-symlink regular file at `plugins/quantum_centauro.so`. For this wrapper, `INPUT_BASENAME` must also be an exact entry in [`examples/input_files.txt`](examples/input_files.txt).

```bash
WORK="$HOME/eic"
REPO="$WORK/quantum-centauro"
export EIC_CONTAINER_BIND_ROOT="$WORK"
export EIC_CONTAINER_SIF="$WORK/containers/eicrecon.sif"
export EICRECON_PREFIX="$WORK/install/eicrecon"
export QUANTUM_CENTAURO_PREFIX="$WORK/install/quantum-centauro"
INPUT="REPLACE_WITH_ABSOLUTE_PATH_TO_EXISTING_ROOT_FILE"
test -f "$INPUT"
INPUT_DIR="$(dirname -- "$INPUT")"
INPUT_BASENAME="$(basename -- "$INPUT")"
RUN_DIR="$WORK/runs/local-aer-manual-001"
test ! -e "$RUN_DIR"
bash "$REPO/scripts/run-local-aer" --run-dir "$RUN_DIR" --input-dir "$INPUT_DIR" --input-basename "$INPUT_BASENAME" --event-index 6 --python-bin "$REPO/.venv-host/bin/python"
```

Use a host virtual environment such as `$REPO/.venv-host` only for this advanced path; it is intentionally distinct from `.venv-eic`. The wrapper preflights before creating `RUN_DIR` or starting the worker. Local integration is proven, but the EICrecon/JANA ABI and host/container matrix is not universally certified. This is an experimental bounded selector integration, not a claim of quantum advantage, detector validation, native plugin compatibility, or throughput improvement.

## Modes

| Mode | Behavior |
| --- | --- |
| `classical` | C++ performs selection without IPC. |
| `shadow` | Sends a blind candidate list to the local selector for diagnostics, then applies the classical action. |
| `active` | Applies only a schema-valid, request-digest-bound candidate index. Operational worker or protocol failures retain the already computed classical choice. |

Python receives only a blind candidate list and returns only a candidate index; diagnostics are non-actionable. An exact-zero pair distance is resolved locally by C++.

Each iteration trace records `final_source` (`classical` or `quantum`), `fallback`, and `decision_reason_code`. Stable codes include `quantum_response_valid`, `shadow_diagnostic`, `zero_distance_bypass`, `local_oversize_guard`, and bounded socket/protocol reason codes such as `service_timeout`, `malformed_response`, and `out_of_range_index`.

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
| `scripts/` | Local Aer launcher plus recipient worker and reconstruction wrappers |
| `tests/python/test_wire_request_digest.py` | Manual protocol sanity test |
| `examples/input_files.txt` | Approved input-basename list for the advanced wrapper |
| `NOTICE`, `LICENSE`, `LICENSES/` | Provenance and GPL/LGPL license texts |

## Source sanity check

The retained Python protocol test is a manual source sanity check and does not require EICrecon:

```bash
cd "$REPO"
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src "$REPO/.venv-eic/bin/python" -m pytest -p no:cacheprovider tests/python/test_wire_request_digest.py
```

## License/Releases

Python and scripts are [GPL-3.0-only](LICENSE); plugin sources retain LGPL-3.0-or-later SPDX headers with the [LGPL text](LICENSES/LGPL-3.0.txt). See [Releases](https://github.com/eic/quantum-centauro/releases) for versioned historical deliverables.
