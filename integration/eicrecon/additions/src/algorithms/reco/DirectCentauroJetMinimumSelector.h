// SPDX-License-Identifier: LGPL-3.0-or-later

#pragma once

#include <cmath>
#include <cstddef>
#include <limits>
#include <vector>

namespace eicrecon {

/**
 * @brief Identifies whether a clustering candidate merges a pair or finalizes a jet.
 *
 * Pair candidates represent a physical Centauro-coordinate recombination;
 * beam candidates finalize one jet. Invalid candidates are ignored by the
 * selector rather than causing a clustering failure.
 */
enum class DirectCentauroCandidateKind { Pair, Beam };

/**
 * @brief Represents one candidate considered by a direct Centauro clustering step.
 *
 * Pair candidates reference two active jets; beam candidates reference the one
 * active jet to be finalized. Their distance is dimensionless in Centauro
 * coordinate space. A non-finite or negative distance is invalid and is
 * ignored by selection; this value type has constant storage cost.
 */
struct DirectCentauroCandidate {
  /// Candidate action selected by the clustering loop.
  DirectCentauroCandidateKind kind = DirectCentauroCandidateKind::Beam;
  /// Index of the first active jet, or the finalized jet for a beam candidate.
  std::size_t i = 0;
  /// Index of the second active jet; equal to @ref i for a beam candidate.
  std::size_t j = 0;
  /// Pair or beam distance; non-finite and negative values are invalid.
  double distance = std::numeric_limits<double>::infinity();
};

/**
 * @brief Stores the valid minimum candidate and its position in the input sequence.
 *
 * An invalid selection reports that no physically usable finite distance was
 * available; it does not throw and has constant storage cost.
 */
struct DirectCentauroSelection {
  /// Whether a finite, non-negative candidate was found.
  bool valid = false;
  /// Position of @ref candidate in the candidate sequence when @ref valid is true.
  std::size_t candidateIndex = 0;
  /// The selected candidate, or its default value when no candidate is valid.
  DirectCentauroCandidate candidate{};
};

/**
 * @brief Selects the deterministic classical minimum for a clustering iteration.
 *
 * Invalid distances are ignored. Exact ties retain the first enumerated
 * candidate, preserving the clustering order supplied by the caller.
 */
class DirectCentauroClassicalMinimumSelector {
public:
  /**
     * @brief Finds the first candidate with the smallest finite, non-negative distance.
     * @param candidates Ordered pair and beam candidates for one clustering iteration.
     * @return A valid selection, or an invalid selection if no candidate is usable.
     * @note Invalid distances are skipped rather than reported as errors.
     * @complexity Linear in `candidates.size()`.
     */
  [[nodiscard]] DirectCentauroSelection
  select(const std::vector<DirectCentauroCandidate>& candidates) const {
    DirectCentauroSelection result;

    for (std::size_t index = 0; index < candidates.size(); ++index) {
      const auto& candidate = candidates[index];

      if (!std::isfinite(candidate.distance) || candidate.distance < 0.0) {
        continue;
      }

      // A strict comparison preserves the first candidate on exact ties.
      if (!result.valid || candidate.distance < result.candidate.distance) {
        result.valid          = true;
        result.candidateIndex = index;
        result.candidate      = candidate;
      }
    }

    return result;
  }
};

} // namespace eicrecon
