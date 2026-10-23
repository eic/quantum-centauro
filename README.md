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

## 1. Practical Application Proposal

Quantum Centauro could serve as an experimental hybrid selection service within the EIC jet-reconstruction workflow. Classical software would continue to construct candidate distances, maintain the clustering state, apply merge or beam decisions, and produce the final jets. A quantum service would receive candidate distances and return a proposed selection index.

A possible deployment would place a persistent quantum-processing service close to the reconstruction computing infrastructure, with low-latency communication and dedicated execution resources. Keeping the service available and reusing circuit structures where possible could reduce repeated initialization and compilation overhead. However, each new candidate list would still require input-dependent preparation and execution.

The most practical starting point is offline evaluation or a parallel diagnostic workflow, leaving the reference reconstruction unchanged. An active integration could then be considered if hardware measurements demonstrate acceptable latency, reliability, and reconstruction quality.

The current results support studying this integration: the three methods reconstruct the same final jets in the included ten-event sample. They do not establish a performance advantage or validate deployment on physical quantum hardware. The present implementation uses local Aer simulation; a hardware-backed service remains a proposed extension.

### When Would the Quantum Approach Become Preferable?

A long-term motivation for Quantum Centauro is to investigate whether quantum-assisted selection could become a competitive alternative to classical processing as high-energy physics experiments face increasing data rates and reconstruction workloads.

For the quantum approach to become preferable, a hardware-backed implementation would need to preserve the required reconstruction quality while delivering a measurable benefit over an optimized classical baseline: higher sustained throughput, lower end-to-end latency, or a lower total processing cost. This comparison must include data encoding, state preparation, communication, device scheduling, repeated measurements, result decoding, and any classica l validation or recovery, not only quantum circuit execution.

The key research target is therefore a performance crossover: a workload regime in which the benefit of quantum processing outweighs its integration overhead. Reusable circuit structures, efficient input loading, dedicated execution resources, and processing requests from independent events may help approach that regime, but their contribution must be demonstrated experimentally.

Increasing data volume alone does not guarantee such a crossover. More events can also be processed through classical parallelism, while larger candidate lists change the cost of both classical selection and quantum preparation.

The current results establish an experimental starting point, not evidence that this crossover has been reached. Identifying whether it exists, and under which hardware and workload conditions, is a central direction for future work.

## 2. Next Steps

1. **Identify the relevant reconstruction bottleneck.**
   Profile a persistent reconstruction workflow, separating candidate generation, classical selection, state preparation, communication, execution, and result decoding. Define latency and throughput requirements before choosing a deployment architecture.

2. **Strengthen the classical comparisons.**
   Compare the circuit-based selector against both an optimized exact minimum search and classical sampling from the same inverse-power probability distribution. This separates the effect of the sampling strategy from the value of its quantum implementation.

3. **Run a focused hardware experiment.**
   Test representative candidate lists on a physical quantum processor. Record preparation, execution, readout, communication, and queueing costs separately, together with selection accuracy. Explore reusable circuit structures and parameter updates without assuming that preparation is free.

4. **Assess selective integration.**
   If a useful operating regime emerges, investigate dispatching only suitable requests to the quantum service while retaining classical processing elsewhere. Evaluate latency limits and fallback behavior before considering use in the reconstruction’s critical path.

## 3. Challenges

- **Preparing changing inputs.**
  A ready quantum processor is not the same as a state already prepared for the next event. Candidate distances change throughout clustering, so input loading and state preparation remain part of the computation.

- **Competing with inexpensive classical selection.**
  The current prototype handles at most 128 candidates per request, where native classical minimum selection is already very fast. It also retains a classical reference selection for evaluation and fallback. Demonstrating acceleration would require accounting for all this work, rather than measuring circuit execution alone.

- **End-to-end latency and availability.**
  Physical proximity can reduce communication delay, but it does not eliminate device scheduling, repeated measurements, readout, or decoding. A practical service must satisfy latency and throughput requirements under sustained load, not only achieve a favorable average execution time.

- **Probabilistic correctness.**
  The selector favors small distances but does not guarantee an exact minimum. A valid response can still contain a nonminimum selection; operational fallback does not automatically detect that case. Hardware noise and finite sampling must therefore be evaluated at both the decision and reconstructed-jet levels.

- **Sequential dependencies.**
  Each clustering decision changes the next candidate list. Requests from independent events may be grouped, but the full decision sequence of one event generally cannot be prepared in advance.

- **Translating simulation into practical benefit.**
  Aer timings are not physical quantum-processor timings, and agreement on a small event sample is not general physics validation. A deployment case requires hardware evidence, broader reconstruction studies, and a demonstrable benefit relative to strong classical alternatives.

## License

Python material is GPL-3.0-only. The external C++ plugin retains LGPL-3.0-or-later notices. See `LICENSE`, `LICENSES/`, and `NOTICE`.
