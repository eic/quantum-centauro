// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2023 Derek Anderson, Zhongling Ji, John Lajoie

#pragma once

#include <DD4hep/DD4hepUnits.h>

#include <string>

namespace eicrecon {

/**
 * @brief Configures direct Centauro jet reconstruction and its numerical guards.
 *
 * Momentum thresholds use the framework energy units. The radius sets the
 * scale of the Centauro coordinate-space pair distance, while
 * `denominatorEpsilon` protects the physical E - pz coordinate transform.
 * Algorithm initialization rejects invalpid limits with @c std::runtime_error
 * before event processing; this POD configuration has constant storage cost.
 */
struct DirectCentauroJetReconstructionConfig {
  /// Jet-radius parameter R that normalizes the pair distance.
  float rJet = 1.0F;
  /// Exclusive lower transverse-momentum threshold for input constituents.
  double minCstPt = 0.2 * dd4hep::GeV;
  /// Exclusive upper transverse-momentum threshold for input constituents.
  double maxCstPt = 100.0 * dd4hep::GeV;
  /// Inclusive lower transverse-momentum threshold for output jets.
  double minJetPt = 1.0 * dd4hep::GeV;
  /// Minimum permitted E - pz denominator for Centauro coordinates.
  double denominatorEpsilon = 1.0e-12;
  /// Output area while area estimation is not implemented by this prototype.
  float defaultJetArea = 0.0F;
  /// Candidate selector: classical, qiskit_shadow, or qiskit_active.
  std::string quantumMode = "classical";
  /// Local Unix-domain socket served by the persistent Aer worker.
  std::string quantumSocketPath;
  /// Per-request local worker timeout in milliseconds.
  unsigned quantumTimeoutMilliseconds = 1000U;
  /// Number of samples requested from the local selector worker.
  unsigned quantumShots = 512U;
  /// Positive inverse-power amplitude exponent sent in the blind request.
  double quantumExponentA = 3.0;
  /// Deterministic local sampler seed sent in the blind request.
  unsigned quantumSeed = 314159U;
  /// Power-of-two candidate-list bound in [1, 128].
  unsigned qiskitMaxCandidates = 128U;
  /// Optional JSONL iteration trace written by the C++ owner of clustering state.
  std::string quantumTracePath;
  /// Operational local-worker failures always retain the deterministic classical action.
  /// Invalid reconstruction configuration still fails during initialization.
  std::string quantumFallbackPolicy = "classical";
  /// Retained for configuration compatibility; true is rejected during initialization.
  bool quantumFailClosed = false;
};

} // namespace eicrecon
