// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2024 Derek Anderson, Zhongling Ji, Dmitry Kalinkin, John Lajoie

#include "quantum_centauro/DirectCentauroJetReconstruction.h"

#include <edm4eic/EDM4eicVersion.h>
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
#include <edm4eic/JetCollection.h>
#endif
#include <edm4eic/ReconstructedParticleCollection.h>
#include <edm4hep/Vector3f.h>
#include <edm4hep/utils/vector_utils.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <iomanip>
#include <limits>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace eicrecon {

namespace {

std::string leafSet(const std::vector<std::size_t>& leaves) {
  auto canonicalLeaves = leaves;
  std::sort(canonicalLeaves.begin(), canonicalLeaves.end());
  std::ostringstream value;
  value << '[';
  for (std::size_t index = 0; index < canonicalLeaves.size(); ++index) {
    if (index != 0U) {
      value << ',';
    }
    value << canonicalLeaves[index];
  }
  value << ']';
  return value.str();
}

} // namespace

template <typename InputT> void DirectCentauroJetReconstruction<InputT>::init() {
  // The declaration documents the public configuration contract; validate it before events arrive.
  if (!(this->m_cfg.rJet > 0.0F)) {
    throw std::runtime_error("DirectCentauroJetReconstruction requires rJet > 0.");
  }

  if (!(this->m_cfg.minCstPt >= 0.0)) {
    throw std::runtime_error("DirectCentauroJetReconstruction requires minCstPt >= 0.");
  }

  if (!(this->m_cfg.maxCstPt > this->m_cfg.minCstPt)) {
    throw std::runtime_error("DirectCentauroJetReconstruction requires maxCstPt > minCstPt.");
  }

  if (!(this->m_cfg.minJetPt >= 0.0)) {
    throw std::runtime_error("DirectCentauroJetReconstruction requires minJetPt >= 0.");
  }

  if (!(this->m_cfg.denominatorEpsilon > 0.0)) {
    throw std::runtime_error("DirectCentauroJetReconstruction requires denominatorEpsilon > 0.");
  }

  if (this->m_cfg.quantumMode != "classical" && this->m_cfg.quantumMode != "qiskit_shadow" &&
      this->m_cfg.quantumMode != "qiskit_active") {
    throw std::runtime_error("DirectCentauroJetReconstruction quantumMode must be classical, qiskit_shadow, or qiskit_active.");
  }
  if (this->m_cfg.qiskitMaxCandidates == 0U || this->m_cfg.qiskitMaxCandidates > 128U ||
      (this->m_cfg.qiskitMaxCandidates & (this->m_cfg.qiskitMaxCandidates - 1U)) != 0U) {
    throw std::runtime_error("DirectCentauroJetReconstruction qiskitMaxCandidates must be a power of two from 1 through 128.");
  }
  if (this->m_cfg.quantumMode != "classical" && this->m_cfg.quantumSocketPath.empty()) {
    throw std::runtime_error("DirectCentauroJetReconstruction quantumSocketPath is required outside classical mode.");
  }
  if (this->m_cfg.quantumTimeoutMilliseconds == 0U) {
    throw std::runtime_error("DirectCentauroJetReconstruction quantumTimeoutMilliseconds must be positive.");
  }
  if (this->m_cfg.quantumShots == 0U || !std::isfinite(this->m_cfg.quantumExponentA) ||
      this->m_cfg.quantumExponentA <= 0.0) {
    throw std::runtime_error("DirectCentauroJetReconstruction quantum shots and exponent must be positive.");
  }

  this->trace("Initialized direct Centauro reconstruction");
}

template <typename InputT>
std::optional<typename DirectCentauroJetReconstruction<InputT>::WorkingJet>
DirectCentauroJetReconstruction<InputT>::makeWorkingJet(double px, double py, double pz,
                                                        double energy,
                                                        std::size_t originalIndex) const {
  // A failed geometry update lets the event loop skip only this input particle.
  WorkingJet jet;

  jet.px     = px;
  jet.py     = py;
  jet.pz     = pz;
  jet.energy = energy;
  jet.constituentIndices.push_back(originalIndex);

  if (!updateGeometry(jet)) {
    return std::nullopt;
  }

  return jet;
}

template <typename InputT>
bool DirectCentauroJetReconstruction<InputT>::updateGeometry(WorkingJet& jet) const {
  // x and y are equivalent to etabar times the azimuthal unit vector.
  jet.pt  = std::hypot(jet.px, jet.py);
  jet.phi = std::atan2(jet.py, jet.px);

  const double denominator = jet.energy - jet.pz;

  if (!std::isfinite(jet.pt) || !std::isfinite(jet.phi) || !std::isfinite(denominator) ||
      denominator <= this->m_cfg.denominatorEpsilon) {
    return false;
  }

  jet.etabar = 2.0 * jet.pt / denominator;

  // Because x = etabar * cos(phi) and y = etabar * sin(phi),
  // these equivalent expressions avoid trigonometric calls in the distance calculation.
  jet.x = 2.0 * jet.px / denominator;
  jet.y = 2.0 * jet.py / denominator;

  return std::isfinite(jet.etabar) && std::isfinite(jet.x) && std::isfinite(jet.y);
}

template <typename InputT>
double DirectCentauroJetReconstruction<InputT>::pairDistance(const WorkingJet& first,
                                                             const WorkingJet& second) const {
  // Invalid arithmetic is made noncompetitive rather than changing clustering order.
  const double dx = first.x - second.x;
  const double dy = first.y - second.y;
  const double radiusSquared =
      static_cast<double>(this->m_cfg.rJet) * static_cast<double>(this->m_cfg.rJet);
  const double distance = (dx * dx + dy * dy) / radiusSquared;

  if (!std::isfinite(distance) || distance < 0.0) {
    return std::numeric_limits<double>::infinity();
  }

  return distance;
}

template <typename InputT>
std::vector<DirectCentauroCandidate> DirectCentauroJetReconstruction<InputT>::buildCandidates(
    const std::vector<WorkingJet>& activeJets) const {
  // Enumeration order is part of deterministic tie handling in the selector.
  std::vector<DirectCentauroCandidate> candidates;

  const std::size_t size      = activeJets.size();
  const std::size_t pairCount = size > 1 ? size * (size - 1) / 2 : 0;
  candidates.reserve(pairCount + size);

  for (std::size_t i = 0; i < size; ++i) {
    for (std::size_t j = i + 1; j < size; ++j) {
      candidates.push_back(
          {DirectCentauroCandidateKind::Pair, i, j, pairDistance(activeJets[i], activeJets[j])});
    }

    candidates.push_back({DirectCentauroCandidateKind::Beam, i, i, 1.0});
  }

  return candidates;
}

template <typename InputT>
typename DirectCentauroJetReconstruction<InputT>::WorkingJet
DirectCentauroJetReconstruction<InputT>::merge(const WorkingJet& first,
                                               const WorkingJet& second) const {
  // Keep source indices in merge order so output relations remain reproducible.
  WorkingJet result;

  result.px     = first.px + second.px;
  result.py     = first.py + second.py;
  result.pz     = first.pz + second.pz;
  result.energy = first.energy + second.energy;

  result.constituentIndices = first.constituentIndices;
  result.constituentIndices.insert(result.constituentIndices.end(),
                                   second.constituentIndices.begin(),
                                   second.constituentIndices.end());

  if (!updateGeometry(result)) {
    throw std::runtime_error(
        "DirectCentauroJetReconstruction produced an invalid merged four-vector.");
  }

  return result;
}

template <typename InputT>
void DirectCentauroJetReconstruction<InputT>::process(
    const typename DirectCentauroJetReconstructionAlgorithm<InputT>::Input& input,
    const typename DirectCentauroJetReconstructionAlgorithm<InputT>::Output& output) const {
  // The declaration documents the event contract; this is the exhaustive clustering implementation.
  const auto [headers, inputCollection] = input;
  auto [jetCollection]                  = output;
  const std::string eventLabel = headers->empty()
                                     ? "unknown"
                                     : std::to_string(headers->at(0).getRunNumber()) + ":" +
                                           std::to_string(headers->at(0).getEventNumber());

  std::vector<WorkingJet> activeJets;
  std::vector<WorkingJet> finalJets;
  activeJets.reserve(inputCollection->size());
  finalJets.reserve(inputCollection->size());

  for (std::size_t index = 0; index < inputCollection->size(); ++index) {
    const auto particle  = inputCollection->at(index);
    const auto& momentum = particle.getMomentum();
    const double pt      = edm4hep::utils::magnitudeTransverse(momentum);

    if (!(pt > this->m_cfg.minCstPt && pt < this->m_cfg.maxCstPt)) {
      continue;
    }

    auto workingJet =
        makeWorkingJet(momentum.x, momentum.y, momentum.z, particle.getEnergy(), index);

    if (!workingJet.has_value()) {
      this->warning("Skipping input particle {} because its Centauro coordinates are invalid.",
                    index);
      continue;
    }

    activeJets.push_back(std::move(*workingJet));
  }

  if (activeJets.empty()) {
    this->trace("  Empty particle list.");
    return;
  }

  this->trace("  Number of direct Centauro input particles: {}", activeJets.size());

  std::size_t iteration = 0;
  std::size_t fallbackCount = 0;
  std::size_t oversizeCount = 0;
  std::size_t serviceErrorCount = 0;
  std::size_t timeoutCount = 0;
  std::size_t zeroDistanceBypassCount = 0;
  std::size_t nontrivialRequestCount = 0;
  std::size_t qiskitAppliedCount = 0;
  std::optional<std::size_t> firstIndexDivergence;
  while (!activeJets.empty()) {
    const auto candidates = buildCandidates(activeJets);
    const auto firstZero = std::find_if(candidates.begin(), candidates.end(),
                                        [](const auto& candidate) { return candidate.distance == 0.0; });
    const bool hasZeroDistance = firstZero != candidates.end();
    std::optional<std::size_t> quantumIndex;
    bool fallback = false;
    bool zeroDistanceBypass = false;
    bool timeout = false;
    bool serviceError = false;
    std::string fallbackReason;
    double latencyMilliseconds = 0.0;
    double statePreparationMilliseconds = 0.0;
    double samplingMilliseconds = 0.0;
    unsigned qubits = 0U;
    unsigned shots = 0U;
    double exponent = 0.0;
    unsigned long workerPid = 0UL;
    std::string failureStage = "none";
    int socketErrno = 0;
    std::size_t responseBytes = 0U;
    bool frameComplete = false;
    std::string workerStatus = "none";
    unsigned long requestSequence = 0UL;
    std::string workerIdentity;
    std::string responseSchemaVersion = "none";
    std::string preparationMethod = "none";
    std::string preparationVersion = "none";
    double preparationCutoff = 0.0;
    double droppedProbabilityMass = 0.0;
    double stateFidelity = 0.0;
    double requestSerializationMilliseconds = 0.0;
    double transportWaitMilliseconds = 0.0;
    double responseParsingValidationMilliseconds = 0.0;
    double workerRequestParsingValidationMilliseconds = 0.0;
    double workerResponseAssemblyMilliseconds = 0.0;
    bool qiskitRequestSent = false;
    bool qiskitResponseValid = false;
    bool qiskitApplied = false;
    if (this->m_cfg.quantumMode != "classical") {
      if (hasZeroDistance) {
        zeroDistanceBypass = true;
        ++zeroDistanceBypassCount;
      } else if (candidates.size() > this->m_cfg.qiskitMaxCandidates) {
        if (this->m_cfg.quantumFailClosed) {
          throw std::runtime_error(
              "DirectCentauroJetReconstruction quantum fail-closed: local_oversize_guard");
        }
        fallback = true;
        fallbackReason = "local_oversize_guard";
        ++oversizeCount;
      } else {
        ++nontrivialRequestCount;
        qiskitRequestSent = true;
        const auto reply = m_quantumClient.request(this->m_cfg.quantumSocketPath,
                                                     this->m_cfg.quantumTimeoutMilliseconds,
                                                     candidates, eventLabel, iteration,
                                                     this->m_cfg.quantumShots,
                                                     this->m_cfg.quantumExponentA,
                                                     this->m_cfg.quantumSeed);
        latencyMilliseconds = reply.latencyMilliseconds;
        statePreparationMilliseconds = reply.statePreparationMilliseconds;
        samplingMilliseconds = reply.samplingMilliseconds;
        qubits = reply.qubits;
        shots = reply.shots;
        exponent = reply.exponent;
        workerPid = reply.workerPid;
        failureStage = reply.failureStage;
        socketErrno = reply.socketErrno;
        responseBytes = reply.responseBytes;
        frameComplete = reply.frameComplete;
        workerStatus = reply.workerStatus;
        requestSequence = reply.requestSequence;
        workerIdentity = reply.workerIdentity;
        responseSchemaVersion = reply.responseSchemaVersion;
        preparationMethod = reply.preparationMethod;
        preparationVersion = reply.preparationVersion;
        preparationCutoff = reply.preparationCutoff;
        droppedProbabilityMass = reply.droppedProbabilityMass;
        stateFidelity = reply.stateFidelity;
        requestSerializationMilliseconds = reply.requestSerializationMilliseconds;
        transportWaitMilliseconds = reply.transportWaitMilliseconds;
        responseParsingValidationMilliseconds = reply.responseParsingValidationMilliseconds;
        workerRequestParsingValidationMilliseconds = reply.workerRequestParsingValidationMilliseconds;
        workerResponseAssemblyMilliseconds = reply.workerResponseAssemblyMilliseconds;
        if (!reply.valid) {
          if (this->m_cfg.quantumFailClosed) {
            throw std::runtime_error("DirectCentauroJetReconstruction quantum fail-closed: " + reply.reason);
          }
          fallback = true;
          fallbackReason = reply.reason;
          timeout = reply.timeout;
          serviceError = reply.serviceError;
          timeoutCount += timeout ? 1U : 0U;
          serviceErrorCount += serviceError ? 1U : 0U;
        } else {
          qiskitResponseValid = true;
          quantumIndex = reply.index;
        }
      }
      if (fallback) {
        ++fallbackCount;
      }
    }
    // Classical selection is evaluation/fallback state. It is deliberately not
    // available while the blind worker request is constructed or dispatched.
    const auto classicalSelection = m_selector.select(candidates);
    if (!classicalSelection.valid) {
      throw std::runtime_error(
          "DirectCentauroJetReconstruction could not select a finite minimum candidate.");
    }
    auto selection = classicalSelection;
    if (zeroDistanceBypass) {
      selection.valid = true;
      selection.candidateIndex = static_cast<std::size_t>(std::distance(candidates.begin(), firstZero));
      selection.candidate = *firstZero;
    } else if (qiskitResponseValid && this->m_cfg.quantumMode == "qiskit_active") {
      selection.valid = true;
      selection.candidateIndex = *quantumIndex;
      selection.candidate = candidates[*quantumIndex];
      ++qiskitAppliedCount;
      qiskitApplied = true;
    }
    if (quantumIndex.has_value() && *quantumIndex != classicalSelection.candidateIndex &&
        !firstIndexDivergence.has_value()) {
      firstIndexDivergence = iteration;
    }
    if (!this->m_cfg.quantumTracePath.empty()) {
      const auto& action = selection.candidate;
      const double minimum = classicalSelection.candidate.distance;
      std::optional<double> secondMinimum;
      std::vector<std::size_t> minimumSet;
      for (std::size_t candidateIndex = 0; candidateIndex < candidates.size(); ++candidateIndex) {
        const auto distance = candidates[candidateIndex].distance;
        if (std::isfinite(distance) && distance >= 0.0) {
          if (distance == minimum) minimumSet.push_back(candidateIndex);
          if (distance > minimum && (!secondMinimum.has_value() || distance < *secondMinimum)) secondMinimum = distance;
        }
      }
      std::ofstream trace(this->m_cfg.quantumTracePath, std::ios::app);
      if (!trace) {
        throw std::runtime_error("DirectCentauroJetReconstruction could not open quantumTracePath.");
      }
      trace << "{\"record_type\":\"iteration\",\"mode\":\"" << this->m_cfg.quantumMode
            << "\",\"event\":\"" << eventLabel << "\",\"iteration\":" << iteration
             << ",\"candidate_count\":" << candidates.size() << ",\"candidates\":[";
      for (std::size_t candidateIndex = 0; candidateIndex < candidates.size(); ++candidateIndex) {
        if (candidateIndex != 0U) trace << ',';
        const auto& item = candidates[candidateIndex];
        trace << "{\"kind\":\"" << (item.kind == DirectCentauroCandidateKind::Pair ? "pair" : "beam")
              << "\",\"i\":" << item.i << ",\"j\":" << item.j << ",\"distance\":";
        if (std::isfinite(item.distance)) trace << std::setprecision(std::numeric_limits<double>::max_digits10) << item.distance; else trace << "null";
        trace << '}';
      }
      trace << "],\"minimum\":" << minimum << ",\"second_minimum\":";
      if (secondMinimum.has_value()) trace << *secondMinimum; else trace << "null";
      trace << ",\"gap\":";
      if (secondMinimum.has_value()) trace << *secondMinimum - minimum; else trace << "null";
      trace << ",\"minimum_set\":[";
      for (std::size_t minimumIndex = 0; minimumIndex < minimumSet.size(); ++minimumIndex) {
        if (minimumIndex != 0U) trace << ',';
        trace << minimumSet[minimumIndex];
      }
      trace << ']'
             << ",\"classical_index\":" << classicalSelection.candidateIndex
             << ",\"sampled_index\":";
      if (quantumIndex.has_value()) {
        trace << *quantumIndex;
      } else {
        trace << "null";
      }
       trace << ",\"applied_index\":" << selection.candidateIndex
              << ",\"qiskit_request_sent\":" << (qiskitRequestSent ? "true" : "false")
              << ",\"qiskit_response_valid\":" << (qiskitResponseValid ? "true" : "false")
              << ",\"qiskit_applied\":" << (qiskitApplied ? "true" : "false")
             << ",\"zero_distance_bypass\":" << (zeroDistanceBypass ? "true" : "false")
             << ",\"oversize_guard\":" << (fallbackReason == "local_oversize_guard" ? "true" : "false")
             << ",\"timeout\":" << (timeout ? "true" : "false")
             << ",\"service_error\":" << (serviceError ? "true" : "false")
             << ",\"state_preparation_ms\":" << statePreparationMilliseconds
             << ",\"sampling_ms\":" << samplingMilliseconds
             << ",\"total_ipc_latency_ms\":" << latencyMilliseconds
              << ",\"shots\":" << shots << ",\"exponent\":" << exponent << ",\"seed\":" << this->m_cfg.quantumSeed
             << ",\"qubits\":" << qubits
               << ",\"worker_pid\":" << workerPid << ",\"worker_request_sequence\":" << requestSequence
               << ",\"failure_stage\":\"" << failureStage << "\",\"socket_errno\":" << socketErrno
               << ",\"response_bytes\":" << responseBytes << ",\"frame_complete\":" << (frameComplete ? "true" : "false")
                << ",\"worker_status\":\"" << workerStatus << "\""
                << ",\"response_schema_version\":\"" << responseSchemaVersion << "\""
                << ",\"preparation_method\":\"" << preparationMethod << "\""
                << ",\"preparation_version\":\"" << preparationVersion << "\""
                << ",\"preparation_cutoff\":" << preparationCutoff
                << ",\"dropped_probability_mass\":" << droppedProbabilityMass
                << ",\"state_fidelity\":" << stateFidelity
                << ",\"request_serialization_ms\":" << requestSerializationMilliseconds
                << ",\"transport_wait_ms\":" << transportWaitMilliseconds
                << ",\"response_parsing_validation_ms\":" << responseParsingValidationMilliseconds
                << ",\"worker_request_parsing_validation_ms\":" << workerRequestParsingValidationMilliseconds
                << ",\"worker_response_assembly_serialization_ms\":" << workerResponseAssemblyMilliseconds
                << ",\"worker_identity\":";
        if (workerIdentity.empty()) {
          trace << "null";
        } else {
          trace << nlohmann::json(workerIdentity).dump();
        }
       trace << ",\"fallback\":" << (fallback ? "true" : "false")
            << ",\"fallback_reason\":";
      if (fallback) {
        trace << "\"" << fallbackReason << "\"";
      } else {
        trace << "null";
      }
      trace << ",\"action\":{\"kind\":\""
            << (action.kind == DirectCentauroCandidateKind::Pair ? "pair" : "beam")
            << "\",\"i\":" << action.i << ",\"j\":" << action.j
            << ",\"left_leaves\":" << leafSet(activeJets[action.i].constituentIndices)
            << ",\"right_leaves\":" << leafSet(activeJets[action.j].constituentIndices)
            << "}}\n";
    }

    const auto& candidate = selection.candidate;

    if (candidate.kind == DirectCentauroCandidateKind::Pair) {
      const std::size_t i = candidate.i;
      const std::size_t j = candidate.j;

      if (i >= activeJets.size() || j >= activeJets.size() || i >= j) {
        throw std::runtime_error(
            "DirectCentauroJetReconstruction received an invalid pair selection.");
      }

      WorkingJet merged = merge(activeJets[i], activeJets[j]);

      activeJets.erase(activeJets.begin() + static_cast<std::ptrdiff_t>(j));
      activeJets.erase(activeJets.begin() + static_cast<std::ptrdiff_t>(i));
      activeJets.push_back(std::move(merged));
    } else {
      const std::size_t i = candidate.i;

      if (i >= activeJets.size()) {
        throw std::runtime_error(
            "DirectCentauroJetReconstruction received an invalid beam selection.");
      }

      finalJets.push_back(std::move(activeJets[i]));
      activeJets.erase(activeJets.begin() + static_cast<std::ptrdiff_t>(i));
    }
    ++iteration;
  }

  std::stable_sort(
      finalJets.begin(), finalJets.end(),
      [](const WorkingJet& left, const WorkingJet& right) { return left.pt > right.pt; });

  std::size_t outputIndex = 0;

  for (const auto& jet : finalJets) {
    if (jet.pt < this->m_cfg.minJetPt) {
      continue;
    }

    this->trace("  direct Centauro jet {}: pt = {}, phi = {}, constituents = {}", outputIndex,
                jet.pt, jet.phi, jet.constituentIndices.size());

#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    edm4eic::MutableJet jetOutput = jetCollection->create();
    jetOutput.setType(0U);
    jetOutput.setArea(this->m_cfg.defaultJetArea);
    jetOutput.setEnergy(jet.energy);
    jetOutput.setMomentum(edm4hep::Vector3f(static_cast<float>(jet.px), static_cast<float>(jet.py),
                                            static_cast<float>(jet.pz)));
#else
    auto jetOutput = jetCollection->create();
    jetOutput.setEnergy(jet.energy);
    jetOutput.setMomentum(edm4hep::Vector3f(static_cast<float>(jet.px), static_cast<float>(jet.py),
                                            static_cast<float>(jet.pz)));
#endif

    for (const std::size_t constituentIndex : jet.constituentIndices) {
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
      jetOutput.addToConstituents(inputCollection->at(constituentIndex));
#else
      jetOutput.addToParticles(inputCollection->at(constituentIndex));
#endif
    }

    ++outputIndex;
  }
  if (!this->m_cfg.quantumTracePath.empty()) {
    std::ofstream trace(this->m_cfg.quantumTracePath, std::ios::app);
    if (!trace) {
      throw std::runtime_error("DirectCentauroJetReconstruction could not open quantumTracePath.");
    }
    trace << "{\"record_type\":\"summary\",\"mode\":\"" << this->m_cfg.quantumMode
           << "\",\"event\":\"" << eventLabel << "\",\"fallback_count\":" << fallbackCount
           << ",\"oversize_guard_count\":" << oversizeCount
           << ",\"service_error_count\":" << serviceErrorCount
           << ",\"timeout_count\":" << timeoutCount
           << ",\"zero_distance_bypass_count\":" << zeroDistanceBypassCount
            << ",\"nontrivial_request_count\":" << nontrivialRequestCount
            << ",\"qiskit_applied_count\":" << qiskitAppliedCount
            << ",\"selector_claim\":";
    if (this->m_cfg.quantumMode == "qiskit_active" && nontrivialRequestCount > 0U &&
        nontrivialRequestCount == qiskitAppliedCount && fallbackCount == 0U) {
      trace << "\"Qiskit-selected at every nontrivial selector call\"";
    } else {
      trace << "null";
    }
    trace << ",\"first_candidate_index_divergence_iteration\":";
    if (firstIndexDivergence.has_value()) {
      trace << *firstIndexDivergence;
    } else {
      trace << "null";
    }
    trace << ",\"canonical_final_partition\":[";
    for (std::size_t index = 0; index < finalJets.size(); ++index) {
      if (index != 0U) {
        trace << ',';
      }
      trace << leafSet(finalJets[index].constituentIndices);
    }
    trace << "],\"jet_count\":" << outputIndex
          << ",\"first_canonical_structure_divergence_iteration\":null,\"max_p4_delta_vs_classical\":null}\n";
  }
}

template class DirectCentauroJetReconstruction<edm4eic::ReconstructedParticle>;

} // namespace eicrecon
