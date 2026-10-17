# Jet reconstruction data

These small tables contain recorded reconstruction outputs for the same ten
fixed benchmark events, using the first measured block. They are sufficient to
regenerate the demo's jet displays, comparisons and representative decision
sequence without running detector reconstruction.

| File | Contents |
| --- | --- |
| `particles.csv` | Input particles in the Breit frame; shared across the three methods after checking the arrays agree. |
| `jets.csv` | Independently recorded jet four-momenta for FastJet Centauro, Direct classical and Direct Aer active. |
| `jet_constituents.csv` | Actual relations from jets to input-particle indices. Jet matching uses these sets, not output order. |
| `reconstruction_steps.csv` | Classical and active decisions for event 4, including candidate counts, ties, actions and worker use. |
| `reconstruction_summary.json` | Export checks and per-event comparison details. The notebook also calculates the comparisons directly from the tables. |

Event indices 0–9 identify the ten benchmark inputs, not ten random draws from
a representative physics sample. Momenta and energies are in GeV. A particle
absent from the stored final jets is marked unassigned in the display; this
does not label it as a ghost. Direct jet area values are not an area estimator
and are not compared as measurements.
