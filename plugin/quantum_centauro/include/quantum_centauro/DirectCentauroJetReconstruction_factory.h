// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2024 Zhongling Ji, Derek Anderson

#pragma once

#include <cstdint>
#include <memory>

#include "quantum_centauro/DirectCentauroJetReconstruction.h"
#include "quantum_centauro/DirectCentauroJetReconstructionConfig.h"
#include "extensions/jana/JOmniFactory.h"

namespace eicrecon {

/**
 * @brief Adapts direct Centauro reconstruction to JANA's configured event factory lifecycle.
 *
 * The factory exposes configuration fields as JANA parameters and forwards event
 * collections to the algorithm. Configuration failures propagate from algorithm
 * initialization; event failures propagate from processing.
 *
 * @tparam InputT EDM input particle type.
 */
template <typename InputT>
class DirectCentauroJetReconstruction_factory
    : public JOmniFactory<DirectCentauroJetReconstruction_factory<InputT>,
                          DirectCentauroJetReconstructionConfig> {
public:
  /// Concrete reconstruction algorithm owned by this factory.
  using Algo = eicrecon::DirectCentauroJetReconstruction<InputT>;
  /// JANA factory base specialized with this factory and its configuration.
  using FactoryT = JOmniFactory<DirectCentauroJetReconstruction_factory<InputT>,
                                DirectCentauroJetReconstructionConfig>;

private:
  /// Algorithm configured once and reused for each event.
  std::unique_ptr<Algo> m_algo;

  /// Event metadata and reconstructed-particle inputs forwarded to the algorithm.
  typename FactoryT::template PodioInput<edm4hep::EventHeader> m_eventHeaderInput{this};
  typename FactoryT::template PodioInput<InputT> m_input{this};

#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
  /// Jet output for EDM4eic versions that provide edm4eic::Jet.
  typename FactoryT::template PodioOutput<edm4eic::Jet> m_output{this};
#else
  /// Reconstructed-particle output for older EDM4eic versions.
  typename FactoryT::template PodioOutput<edm4eic::ReconstructedParticle> m_output{this};
#endif

  /// Configurable Centauro pair-distance radius.
  typename FactoryT::template ParameterRef<float> m_rJet{this, "rJet", FactoryT::config().rJet};

  /// Configurable exclusive lower constituent-pT bound.
  typename FactoryT::template ParameterRef<double> m_minCstPt{this, "minCstPt",
                                                              FactoryT::config().minCstPt};

  /// Configurable exclusive upper constituent-pT bound.
  typename FactoryT::template ParameterRef<double> m_maxCstPt{this, "maxCstPt",
                                                              FactoryT::config().maxCstPt};

  /// Configurable inclusive lower output-jet-pT bound.
  typename FactoryT::template ParameterRef<double> m_minJetPt{this, "minJetPt",
                                                              FactoryT::config().minJetPt};

  /// Configurable lower E - pz numerical guard.
  typename FactoryT::template ParameterRef<double> m_denominatorEpsilon{
      this, "denominatorEpsilon", FactoryT::config().denominatorEpsilon};

  /// Configurable placeholder output area while area estimation is unavailable.
  typename FactoryT::template ParameterRef<float> m_defaultJetArea{
      this, "defaultJetArea", FactoryT::config().defaultJetArea};

  typename FactoryT::template ParameterRef<std::string> m_quantumMode{
      this, "quantumMode", FactoryT::config().quantumMode};
  typename FactoryT::template ParameterRef<std::string> m_quantumSocketPath{
      this, "quantumSocketPath", FactoryT::config().quantumSocketPath};
  typename FactoryT::template ParameterRef<unsigned> m_quantumTimeoutMilliseconds{
       this, "quantumTimeoutMilliseconds", FactoryT::config().quantumTimeoutMilliseconds};
  typename FactoryT::template ParameterRef<unsigned> m_quantumShots{
      this, "quantumShots", FactoryT::config().quantumShots};
  typename FactoryT::template ParameterRef<double> m_quantumExponentA{
      this, "quantumExponentA", FactoryT::config().quantumExponentA};
  typename FactoryT::template ParameterRef<unsigned> m_quantumSeed{
      this, "quantumSeed", FactoryT::config().quantumSeed};
  typename FactoryT::template ParameterRef<unsigned> m_qiskitMaxCandidates{
      this, "qiskitMaxCandidates", FactoryT::config().qiskitMaxCandidates};
  typename FactoryT::template ParameterRef<std::string> m_quantumTracePath{
      this, "quantumTracePath", FactoryT::config().quantumTracePath};
  typename FactoryT::template ParameterRef<bool> m_quantumFailClosed{
      this, "quantumFailClosed", FactoryT::config().quantumFailClosed};

public:
  /**
     * @brief Constructs, configures, and initializes the event reconstruction algorithm.
     * @throws std::runtime_error If the configured algorithm parameters are invalid.
     * @complexity Constant, excluding framework setup.
     */
  void Configure() {
    m_algo = std::make_unique<Algo>(this->GetPrefix());
    m_algo->level(static_cast<algorithms::LogLevel>(this->logger()->level()));
    m_algo->applyConfig(FactoryT::config());
    m_algo->init();
  }

  /**
     * @brief Reconstructs jets for the framework's current event.
     * @param runNumber Framework run number; unused by this algorithm.
     * @param eventNumber Framework event number; unused by this algorithm.
     * @throws std::runtime_error If clustering encounters invalid internal state.
     * @complexity Cubic in the accepted constituent count.
     */
  void Process(int32_t /* runNumber */, uint64_t /* eventNumber */) {
    m_algo->process({m_eventHeaderInput(), m_input()}, {m_output().get()});
  }
};

} // namespace eicrecon
