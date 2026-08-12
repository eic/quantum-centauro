# Method

The local selector prepares and samples an inverse-distance-weighted candidate distribution. For positive finite distances, amplitudes are proportional to `d_i^-a`, so probabilities are proportional to `d_i^-2a`. The delivered default is exponent `a = 3.0`, 512 shots, seed `314159`, and at most 128 candidates.

`src/eic_quantum/payloads/quantum_min_search.py` performs stable state preparation. An exact-zero pair distance is handled as a deliberate local C++ bypass: no worker request or digest is produced and the exact-zero candidate is applied. Oversize, timeout, and invalid-reply handling follow the configured C++ fail-closed policy.

Finite-shot sampling is not an exact quantum minimum finder. This repository makes no quantum-speedup, hardware, universal-equivalence, or production-readiness claim.

## Mode Policy

The bare EICrecon configuration defaults `quantumFailClosed=false` and may classically fall back. `scripts/run-active` forces `true`; `scripts/run-shadow` defaults to `true` and offers `QUANTUM_FAIL_CLOSED=false` only for exploration. Oversize, timeout, and invalid-reply behavior follows that setting. Shadow applies the classical action; active applies a validated worker proposal. Exact-zero pair distance remains a local C++ bypass with no worker request or digest.

The method is motivated by J. J. Martinez de Lejarza, L. Cieri, and G. Rodrigo, *Quantum clustering and jet reconstruction at the LHC*, Phys. Rev. D 106, 036021 (2022), DOI `10.1103/PhysRevD.106.036021`, arXiv:2204.06496. Centauro context is from M. Arratia et al., *Asymmetric jet clustering in deep-inelastic scattering*, Phys. Rev. D 104, 034005 (2021), DOI `10.1103/PhysRevD.104.034005`, arXiv:2006.10751. Neither citation is EIC performance evidence.
