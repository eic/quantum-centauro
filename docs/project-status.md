# Project Status

**Overall status: `PASS WITH LIMITATIONS`.** The records below are accepted progress recorded by hash-bound external ledgers, not aggregate statistics. Accepted means admitted only after validation; raw ledgers are external, so each count is not independently reproducible from this repository alone.

| Coverage | Status |
| --- | --- |
| Shadow | `7080/7080` complete |
| Hard | `600/600` complete |
| E2E | `40/40` complete |
| Active | `56/300` partial |
| Final aggregate | Absent |

Active comparison stopped after no exact constituent-partition match left `max()` with no delta values, causing an unadmitted comparison-layer `TypeError`. Earlier accepted rows remain separate. No final aggregate result is claimed. The delivered public source supports only local Qiskit Aer plus sibling EICrecon; it does not establish quantum speedup, a hardware/provider path, production readiness, or universal FastJet equivalence.
