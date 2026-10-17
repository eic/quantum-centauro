# Benchmark

## Recorded results

The [`2026-10-04 snapshot`](results/2026-10-04/README.md) contains completed measurements for ten fixed events: 900 operational process rows across FastJet Centauro, Direct classical, and Direct Aer active, plus 2,640 prepared Aer calls in each of two scenarios. The prepared minimum was selected in 2,635/2,640 calls per scenario—not every call. Opening the notebook does not run simulations.

FastJet Centauro and Direct Centauro are different implementations; the Direct plugin has no FastJet active-area estimator. Direct active/classical partition agreement is a diagnostic, not evidence of universal equivalence or physics validation. Shadow mode is also diagnostic: it queries the worker while retaining the classical choice.

## Timing scope

Operational wall time measures a fresh EICrecon process, including startup, input loading, event selection/skipping, and output writing. Active mode uses a worker; worker startup/readiness is recorded separately. This is an end-to-end operational cost comparison, not isolated clustering time.

Prepared-circuit measurements replay saved candidate inputs after external circuit construction and decomposition. Aer run/result still includes state-preparation gates and circuit execution. These timings do not include live event construction, clustering, candidate enumeration, IPC, or I/O; they are not comparable as an end-to-end speedup claim. The recorded measurements establish neither quantum hardware performance nor general speedup.

The bundled snapshot is the evidence for these historical measurements. Running `benchmark/run.py` or `benchmark/prepared.py` creates new experiments and requires inputs and the relevant EICrecon/Aer environment; it does not regenerate the snapshot. Use `python benchmark/run.py --help` or `python benchmark/prepared.py --help` for independent usage details.
