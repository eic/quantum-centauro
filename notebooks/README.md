# Demo notebook

Open [`demo.ipynb`](demo.ipynb) and run all cells. It uses the ten fixed events and real jet constituents and four-momenta shipped in `data/`, comparing FastJet Centauro, Direct classical, and Direct Aer active. Shadow mode is explained as a diagnostic: the worker is queried while the classical choice is retained.

Install the packages listed in [`requirements.txt`](requirements.txt), then
open the notebook in Jupyter. No EICrecon, plugin or Aer installation is needed
to run the demo. It reports recorded measurements and event-level comparisons;
it does not run new simulations. Its finite sample does not establish universal
equivalence, quantum hardware execution, or speedup. See the
[benchmark contract](../benchmark/README.md) and
[recorded measurements](../benchmark/results/2026-10-04/README.md) for timing scope.
