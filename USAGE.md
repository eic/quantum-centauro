# How to use Quantum Centauro

There are two ways to use this repository:

| I want to… | Start here | What happens |
| --- | --- | --- |
| Understand the methods and compare their results | [Run the demo](#1-run-the-demo-recommended) | Recreates figures and tables from the included data. |
| Run the selector inside EICrecon | [Run a new reconstruction](#2-run-a-new-reconstruction-optional) | Processes your own compatible ROOT input using the plugin and local Aer worker. |

**For a first visit, the demo is enough.** You can also open
[`notebooks/demo.ipynb`](notebooks/demo.ipynb) directly on GitHub to view its
saved figures and tables without installing anything.

## 1. Run the demo

You need Git and Python 3.10 or newer. The following commands use a Linux/macOS terminal. Run them in order:

```bash
git clone https://github.com/eic/quantum-centauro.git
cd quantum-centauro
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r notebooks/requirements.txt
jupyter lab notebooks/demo.ipynb
```

If you already have the repository, skip cloning and open a terminal in its root directory. On Windows, create the environment with `py -m venv .venv` and activate it in PowerShell with `.venv\Scripts\Activate.ps1` instead.

In JupyterLab:

1. Open `demo.ipynb` if it is not already open.
2. Select the **Python 3** kernel if prompted.
3. Choose **Run → Run All Cells** and wait for execution to finish.

You will see:

- An explanation of FastJet Centauro, Direct classical and Direct Aer.
- The same event reconstructed by the three methods, shown side by side.
- A numerical comparison of jet constituents and momenta across ten events.
- The recorded classical and active decisions for event 4.
- Selection-quality results and timing comparisons.

To inspect another event, change `EVENT = 4` in the first code cell to an integer from 0 to 9, then run all cells again. This changes the selected event display and table; the decision-sequence section explicitly stays on event 4, and the aggregate comparisons still use all ten events.

All data needed by the demo are included in `notebooks/data/` and `benchmark/results/`. Run All does not reconstruct new events, run Aer or contact quantum hardware. It analyzes recorded results. No credentials or ROOT input are required.

To return later, activate `.venv` again and run the same `jupyter lab` command from the repository root.

## 2. Run a new reconstruction

This is for users who already have a working Linux EICrecon environment, such as a configured eic-shell session. It is not required for the demo.
Keep the checkout accessible in that environment and run the following steps from its root directory.

### Requirements

- EICrecon available as `eicrecon`, with its compatible development libraries.
- JANA and `nlohmann_json` development packages, CMake 3.24 or newer and a C++20 compiler. CMake must be able to discover those packages.
- The detector environment initialized: `DETECTOR_PATH` and `DETECTOR_CONFIG` must identify a valid detector configuration compatible with your input.
- A compatible EDM4eic ROOT input. For a direct reclustering example, it should contain `EventHeader` and `ReconstructedBreitFrameParticles`.

The repository includes compact CSV results for the demo, not a ROOT event file. Those tables cannot be passed to `qc-run`. The example below uses `input.root` as the name of an input you supply in the repository root; it is
not a bundled file or a download command.

### Install the Python package and build the plugin

Create this environment inside the EICrecon session; do not reuse a Python environment created with a different host/container interpreter.

```bash
python3 -m venv .venv-eic
source .venv-eic/bin/activate
python -m pip install .

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PWD/install"
cmake --build build --parallel 2
cmake --install build
```

The Python installation provides `qc-run`, Qiskit and Aer. The separate CMake step installs `install/plugins/quantum_centauro.so`. Installing the Python package alone does not build the plugin or install EICrecon.

### Process one event

After placing your compatible input at `input.root`, run:

```bash
qc-run input.root --event-index 0 --plugin-prefix ./install --run-dir ./runs/example-01
```

`--event-index` is zero-based: `0` selects the first event. This command:

1. Starts a local Aer worker and checks that it is ready.
2. Processes the selected event in shadow mode: queries the worker but keeps the classical selection.
3. Processes the same event in active mode: applies valid worker proposals, with classical fallback for operational failures.
4. Stops its worker when finished.

It does not run FastJet or an entire benchmark campaign. It uses a local CPU simulator, not a physical quantum processor. Current defaults are 512 shots, exponent 3, seed 314159 and a maximum of 128 candidates per worker request.
A valid sampled proposal is not guaranteed to be a minimum; fallback is not a general correctness guarantee.

### Find the results

Outputs go into `runs/example-01/`:

| File | Purpose |
| --- | --- |
| `shadow.edm4eic.root`, `active.edm4eic.root` | Output particle and reconstructed-jet collections. |
| `shadow.trace.jsonl`, `active.trace.jsonl` | Recorded clustering decisions and event summaries. |
| `shadow.reconstruction.log`, `active.reconstruction.log` | EICrecon output and diagnostic messages. |
| `worker.log` | Local worker diagnostics. |

Use a new run directory for every invocation; existing directories are rejected rather than overwritten. If you omit `--run-dir`, `qc-run` creates a new directory under `runs/` automatically. Results from new runs do not replace
the recorded data displayed by the demo.

For the supported command-line options:

```bash
qc-run --help
```

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `jupyter` or a Python module is missing | Activate the demo environment and install `notebooks/requirements.txt` there. Launch Jupyter from that same environment. |
| The notebook cannot locate its data | Keep the repository folders together and launch Jupyter from the repository root. |
| `qc-run` is not found | Activate `.venv-eic` and install the package with `python -m pip install .`. |
| CMake cannot find EICrecon or JANA | Use the configured EICrecon development environment. Installing Python dependencies cannot supply these native libraries. |
| The detector environment is not prepared | Initialize the detector setup in your EICrecon session before running the command. |
| The plugin cannot be found | Complete `cmake --install build` and pass `--plugin-prefix ./install`. |
| A run directory already exists or its socket path is too long | Choose a fresh, short run-directory name; if necessary, use a shorter checkout location. |
| Reconstruction fails | Read the reported reconstruction log and `worker.log`; confirm the input collections and software versions are compatible. |

For the scope of the recorded timing comparisons, see the
[benchmark guide](benchmark/README.md).
