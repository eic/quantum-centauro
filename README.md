# Quantum Centauro

## Problem and approach

Centauro jet clustering offers a concrete setting to examine where a circuit-based selector might fit in a classical event-processing workflow. This repository is an experimental external JANA plugin for EICrecon: a C++ plugin handles event processing and clustering, while a local Python/Aer worker can select among candidate indices. It is a demonstrator, not a validated replacement for the established reconstruction.

## Demo

Start with [`notebooks/demo.ipynb`](notebooks/demo.ipynb), the single walkthrough. Run All uses the ten fixed events and their real jet constituents and four-momenta shipped in `notebooks/data/`; it compares FastJet Centauro, Direct classical, and Direct Aer active, and explains shadow mode as a diagnostic. Timings and outputs are reported for these events only. This is not a claim of universal equivalence, quantum hardware execution, or speedup.

To regenerate every table and figure, install the demo dependencies and open
the notebook. No plugin, EICrecon or Aer installation is needed for the demo:

```bash
python -m pip install -r notebooks/requirements.txt
jupyter lab notebooks/demo.ipynb
```

The plugin and `qc-run` are the execution layer; the notebook demonstrates
their measured results using only the data included in this repository.

## Recorded benchmark

[`benchmark/README.md`](benchmark/README.md) describes the measurement contract, and [`benchmark/results/2026-10-04/README.md`](benchmark/results/2026-10-04/README.md) describes the shipped measurements. They are recorded results, not new simulations produced by running the demo. Across 2,640 prepared Aer calls, the minimum was selected in 2,635 calls (not all calls). Operational and prepared-circuit timings have different scopes; neither establishes a general speedup or quantum advantage. Prepared-circuit timing excludes external circuit construction and decomposition, but includes state-preparation gates and execution.

## License

Python material is GPL-3.0-only. The external C++ plugin retains LGPL-3.0-or-later notices. See `LICENSE`, `LICENSES/`, and `NOTICE`.
