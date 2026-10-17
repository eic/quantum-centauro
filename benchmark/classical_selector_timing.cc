// Isolated timing of the exact production selector; no pipeline or Python overhead.
#include "quantum_centauro/DirectCentauroJetMinimumSelector.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <vector>

extern "C" double classical_selector_ns(
    const int* kinds, const std::uint64_t* first, const std::uint64_t* second,
    const double* distances, std::size_t count, std::size_t calls,
    std::uint64_t* selected_index, std::uint64_t* checksum) {
  if (count == 0 || calls == 0) return -1.0;
  std::vector<eicrecon::DirectCentauroCandidate> candidates;
  candidates.reserve(count);
  for (std::size_t i = 0; i < count; ++i) {
    candidates.push_back({kinds[i] == 0 ? eicrecon::DirectCentauroCandidateKind::Pair
                                      : eicrecon::DirectCentauroCandidateKind::Beam,
                          static_cast<std::size_t>(first[i]),
                          static_cast<std::size_t>(second[i]), distances[i]});
  }
  eicrecon::DirectCentauroClassicalMinimumSelector selector;
  std::uint64_t sum = 0;
  eicrecon::DirectCentauroSelection result;
  const auto start = std::chrono::steady_clock::now();
  for (std::size_t i = 0; i < calls; ++i) {
    // Prevent the compiler from moving a repeated selection outside the loop.
    std::atomic_signal_fence(std::memory_order_seq_cst);
    result = selector.select(candidates);
    sum += result.candidateIndex;
  }
  const auto stop = std::chrono::steady_clock::now();
  *selected_index = result.candidateIndex;
  *checksum = sum;
  if (!result.valid) return -1.0;
  return std::chrono::duration<double, std::nano>(stop - start).count() / calls;
}
