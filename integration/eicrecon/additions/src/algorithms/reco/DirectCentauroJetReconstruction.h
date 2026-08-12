// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2024 Derek Anderson, Zhongling Ji, Dmitry Kalinkin, John Lajoie

#pragma once

#include <algorithms/algorithm.h>
#include <edm4eic/EDM4eicVersion.h>
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
#include <edm4eic/JetCollection.h>
#else
#include <edm4eic/ReconstructedParticleCollection.h>
#endif
#include <edm4hep/EventHeaderCollection.h>

#include <cstddef>
#include <optional>
#include <string_view>
#include <vector>

#include "DirectCentauroJetMinimumSelector.h"
#include "DirectCentauroQuantumSocketClient.h"
#include "DirectCentauroJetReconstructionConfig.h"
#include "algorithms/interfaces/WithPodConfig.h"

namespace eicrecon {

#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
using DirectCentauroJetOutputCollection = edm4eic::JetCollection;
#else
using DirectCentauroJetOutputCollection = edm4eic::ReconstructedParticleCollection;
#endif

/** @brief Defines the framework input and output collections for direct Centauro reconstruction. */
template <typename InputT>
using DirectCentauroJetReconstructionAlgorithm = algorithms::Algorithm<
    algorithms::Input<edm4hep::EventHeaderCollection, typename InputT::collection_type>,
    algorithms::Output<DirectCentauroJetOutputCollection>>;

/**
 * @brief Reconstructs jets by direct Centauro sequential recombination.
 *
 * Input particles are mapped into Centauro coordinates, then repeatedly merged
 * or finalized according to the exhaustive classical minimum. Invalid input
 * coordinates are skipped; invalid configuration or internal clustering state
 * raises @c std::runtime_error.
 *
 * @tparam InputT EDM input particle type.
 */
template <typename InputT>
class DirectCentauroJetReconstruction
    : public DirectCentauroJetReconstructionAlgorithm<InputT>,
      public WithPodConfig<DirectCentauroJetReconstructionConfig> {
public:
  /**
     * @brief Constructs a named reconstruction algorithm.
     * @param name Framework instance name used for configuration and logging.
     */
  explicit DirectCentauroJetReconstruction(std::string_view name)
      : DirectCentauroJetReconstructionAlgorithm<InputT>{
            name,
            {"eventHeaderCollection", "inputReconstructedParticles"},
            {"outputReconstructedParticles"},
            "Performs direct Centauro jet reconstruction with an exhaustive classical minimum "
            "selector."} {}

  /**
     * @brief Validates reconstruction configuration before event processing.
     * @throws std::runtime_error If a radius, momentum limit, or denominator guard is invalid.
     * @complexity Constant.
     */
  void init() final;

  /**
     * @brief Reconstructs direct Centauro jets from one event's particles.
     * @param input Event header and reconstructed-particle collections.
     * @param output Jet collection populated with selected jets and source relations.
     * @throws std::runtime_error If an internal merge or selected candidate is invalid.
     * @note Particles outside configured pT bounds or with invalid E - pz are skipped.
     * @complexity Cubic in the number of accepted constituents due to exhaustive candidate rebuilding.
     */
  void process(
      const typename DirectCentauroJetReconstructionAlgorithm<InputT>::Input& input,
      const typename DirectCentauroJetReconstructionAlgorithm<InputT>::Output& output) const final;

private:
  /**
     * @brief Mutable clustering state for a four-vector and its source constituents.
     *
     * The `x` and `y` values are Centauro coordinates derived from the physical
     * four-vector and are used only for pair-distance evaluation.
     */
  struct WorkingJet {
    /// Cartesian momentum components and energy of the current jet.
    double px     = 0.0;
    double py     = 0.0;
    double pz     = 0.0;
    double energy = 0.0;

    /// Derived transverse momentum, azimuth, and Centauro radial coordinate.
    double pt     = 0.0;
    double phi    = 0.0;
    double etabar = 0.0;

    /// Cartesian Centauro coordinates used to evaluate pair distances.
    double x = 0.0;
    double y = 0.0;

    /// Original input indices retained as output source relations.
    std::vector<std::size_t> constituentIndices;
  };

  /**
     * @brief Creates initial clustering state from one input four-vector.
     * @param px Cartesian x momentum.
     * @param py Cartesian y momentum.
     * @param pz Cartesian z momentum.
     * @param energy Particle energy.
     * @param originalIndex Position in the input collection for the source relation.
     * @return Working state with valid Centauro geometry, or @c std::nullopt for invalid geometry.
     * @note Invalid input is reported to the caller as a skipped constituent.
     * @complexity Constant.
     */
  [[nodiscard]] std::optional<WorkingJet>
  makeWorkingJet(double px, double py, double pz, double energy, std::size_t originalIndex) const;

  /**
     * @brief Updates kinematic and Centauro coordinates after a four-vector change.
     * @param jet Working jet whose four-vector is read and derived fields are overwritten.
     * @return @c true for finite coordinates with E - pz above the configured guard.
     * @note Returns @c false instead of throwing for invalid numerical geometry.
     * @complexity Constant.
     */
  [[nodiscard]] bool updateGeometry(WorkingJet& jet) const;

  /**
     * @brief Calculates the normalized Centauro distance between two active jets.
     * @param first First jet's Centauro coordinates.
     * @param second Second jet's Centauro coordinates.
     * @return Squared coordinate separation divided by R squared, or infinity if invalid.
     * @note Infinity prevents non-finite geometry from becoming a merge candidate.
     * @complexity Constant.
     */
  [[nodiscard]] double pairDistance(const WorkingJet& first, const WorkingJet& second) const;

  /**
     * @brief Enumerates all pair merges and beam finalizations for active jets.
     * @param activeJets Current clustering state.
     * @return Ordered pair candidates followed by each jet's unit beam candidate.
     * @complexity Quadratic in `activeJets.size()`.
     */
  [[nodiscard]] std::vector<DirectCentauroCandidate>
  buildCandidates(const std::vector<WorkingJet>& activeJets) const;

  /**
     * @brief Adds two jet four-vectors and combines their source constituent indices.
     * @param first First input jet.
     * @param second Second input jet.
     * @return Merged jet with updated Centauro geometry.
     * @throws std::runtime_error If the merged four-vector has invalid geometry.
     * @complexity Linear in the total number of constituent indices.
     */
  [[nodiscard]] WorkingJet merge(const WorkingJet& first, const WorkingJet& second) const;

  DirectCentauroClassicalMinimumSelector m_selector;
  DirectCentauroQuantumSocketClient m_quantumClient;
};

} // namespace eicrecon
