// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 ePIC Collaboration

#include <algorithms/logger.h>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include <edm4eic/EDM4eicVersion.h>
#include <edm4eic/ReconstructedParticleCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <edm4hep/Vector3f.h>
#include <nlohmann/json.hpp>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <set>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "quantum_centauro/DirectCentauroJetMinimumSelector.h"
#include "quantum_centauro/DirectCentauroJetReconstruction.h"
#include "quantum_centauro/DirectCentauroJetReconstructionConfig.h"
#include "quantum_centauro/DirectCentauroQuantumSocketClient.h"

using eicrecon::DirectCentauroCandidate;
using eicrecon::DirectCentauroCandidateKind;
using eicrecon::DirectCentauroClassicalMinimumSelector;
using eicrecon::DirectCentauroJetOutputCollection;
using eicrecon::DirectCentauroJetReconstruction;
using eicrecon::DirectCentauroJetReconstructionConfig;

using DirectCentauroAlgorithm = DirectCentauroJetReconstruction<edm4eic::ReconstructedParticle>;

namespace {

std::string testSocketPath() {
  static std::atomic<unsigned> sequence{0};
  return "/tmp/direct_centauro_test_" + std::to_string(::getpid()) + "_" +
         std::to_string(sequence++) + ".sock";
}

std::string blindResponse(std::size_t index, const nlohmann::json& request,
                           std::string_view schemaVersion = "selector-response/v1",
                           std::string_view workerIdentity = "direct_centauro_aer_1234");

class ScriptedSocket final {
public:
  explicit ScriptedSocket(std::vector<std::string> replies) : m_path(testSocketPath()) {
    m_descriptor = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (m_descriptor < 0) throw std::runtime_error("could not create scripted socket");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, m_path.c_str(), sizeof(address.sun_path) - 1U);
    if (::bind(m_descriptor, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0 ||
        ::listen(m_descriptor, 1) != 0) {
      ::close(m_descriptor);
      throw std::runtime_error("could not bind scripted socket");
    }
    m_thread = std::thread([this, replies = std::move(replies)] {
      for (const auto& reply : replies) {
        const int connection = ::accept(m_descriptor, nullptr, nullptr);
        if (connection < 0) return;
        std::string request;
        char buffer[4096]{};
        ssize_t received = 0;
        do {
          received = ::recv(connection, buffer, sizeof(buffer), 0);
          if (received > 0) request.append(buffer, static_cast<std::size_t>(received));
        } while (received > 0 && request.find('\n') == std::string::npos &&
                 request.size() <= 64U * 1024U);
        if (!request.empty()) m_requests.push_back(request);
        if (reply == "__timeout__") {
          std::this_thread::sleep_for(std::chrono::milliseconds(1100));
        }
        auto actualReply = reply;
        if (reply.rfind("dynamic:", 0) == 0) {
          const auto requestJson = nlohmann::json::parse(request);
          actualReply = blindResponse(std::stoull(reply.substr(8)), requestJson);
        } else if (reply.rfind("dynamic-v2:", 0) == 0) {
          const auto requestJson = nlohmann::json::parse(request);
          actualReply = blindResponse(std::stoull(reply.substr(11)), requestJson,
                                      "selector-response/v2");
        } else if (reply.rfind("dynamic-escaped:", 0) == 0) {
          const auto requestJson = nlohmann::json::parse(request);
          actualReply = blindResponse(std::stoull(reply.substr(16)), requestJson,
                                      "selector-response/v1", "worker\"\\identity\n");
        } else if (reply.rfind("invalid-v2:", 0) == 0) {
          const auto requestJson = nlohmann::json::parse(request);
          auto response = nlohmann::json::parse(
              blindResponse(0U, requestJson, "selector-response/v2"));
          const auto defect = reply.substr(11);
          if (defect == "unknown_nested") response["preparation"]["unexpected"] = 0;
          if (defect == "forbidden_truth") response["classical_selected_index"] = 0;
          if (defect == "malformed_metadata") response["preparation"]["state_fidelity"] = 2.0;
          actualReply = response.dump() + "\n";
        }
        (void)::send(connection, actualReply.data(), actualReply.size(), MSG_NOSIGNAL);
        ::close(connection);
      }
    });
  }

  ~ScriptedSocket() {
    if (m_thread.joinable()) m_thread.join();
    ::close(m_descriptor);
    std::filesystem::remove(m_path);
  }

  [[nodiscard]] const std::string& path() const { return m_path; }
  [[nodiscard]] std::string firstRequest() {
    if (m_thread.joinable()) m_thread.join();
    return m_requests.empty() ? std::string{} : m_requests.front();
  }

private:
  int m_descriptor = -1;
  std::string m_path;
  std::thread m_thread;
  std::vector<std::string> m_requests;
};

std::string runQuantumMode(const std::string& mode, const std::string& socketPath,
                            const std::string& tracePath,
                            const std::size_t particleCount = 2U,
                            const bool failClosed = false) {
  DirectCentauroAlgorithm algorithm("DirectCentauroQuantumModeTest");
  DirectCentauroJetReconstructionConfig config;
  config.rJet = 1.0F;
  config.minCstPt = 0.0;
  config.maxCstPt = 100.0;
  config.minJetPt = 0.0;
  config.quantumMode = mode;
  config.quantumSocketPath = socketPath;
  config.quantumTracePath = tracePath;
  config.quantumFailClosed = failClosed;
  algorithm.applyConfig(config);
  algorithm.init();

  edm4hep::EventHeaderCollection headers;
  edm4eic::ReconstructedParticleCollection particles;
  for (std::size_t index = 0; index < particleCount; ++index) {
    auto particle = particles.create();
    particle.setEnergy(1.0F);
    particle.setMomentum(edm4hep::Vector3f{1.0F - static_cast<float>(index) * 0.1F, 0.0F, 0.0F});
  }
  DirectCentauroJetOutputCollection jets;
  algorithm.process({&headers, &particles}, {&jets});
  std::ifstream trace(tracePath);
  return {std::istreambuf_iterator<char>(trace), std::istreambuf_iterator<char>()};
}

std::string blindResponse(std::size_t index, const nlohmann::json& request,
                           std::string_view schemaVersion, std::string_view workerIdentity) {
  const auto requestId = request.at("request_id").get<std::string>();
  const auto candidateCount = request.at("candidates").size();
  nlohmann::json counts = nlohmann::json::object();
  std::vector<double> probabilities(candidateCount, 0.0);
  std::vector<double> amplitudes(candidateCount, 0.0);
  for (std::size_t candidate = 0; candidate < candidateCount; ++candidate) {
    counts[std::to_string(candidate)] = candidate == index ? 8 : 0;
  }
  if (index < candidateCount) {
    probabilities[index] = 1.0;
    amplitudes[index] = 1.0;
  }
  auto response = nlohmann::json{
      {"amplitudes", amplitudes}, {"circuit", {{"depth", 2}, {"qubits", candidateCount > 2U ? 2 : 1}}},
      {"counts_by_candidate", counts}, {"probabilities", probabilities}, {"request_id", requestId},
      {"request_sha256", eicrecon::directCentauroSha256(request.dump())}, {"schema_version", schemaVersion},
      {"selected_candidate_index", index}, {"status", "ok"},
      {"timings_ms", {{"sampling", 1.0}, {"state_preparation", 0.5}}},
       {"worker", {{"identity", workerIdentity}, {"implementation", "direct_centauro_aer"}, {"pid", 1234}, {"request_sequence", 9}}},
      {"zero_distance_bypass", false},
  };
  if (schemaVersion == "selector-response/v2") {
    response["timings_ms"] = {{"sampling", 1.0}, {"state_preparation", 0.5},
                              {"request_parsing_validation", 0.1},
                              {"response_assembly_serialization", 0.2}};
    response["preparation"] = {{"method", "stabilized_state_preparation"}, {"version", "v1"},
                               {"cutoff", 1e-12}, {"dropped_probability_mass", 1e-13},
                               {"state_fidelity", 1.0}};
  }
  return response.dump() + "\n";
}

} // namespace

/**
 * @brief Verifies finite-minimum selection, invalid-distance rejection, and tie ordering.
 *
 * The selector must return no candidate for empty input and preserve candidate
 * enumeration order on exact physical-distance ties. The case treats an
 * assertion failure as a test failure and uses only fixed-size inputs.
 */
TEST_CASE("DirectCentauro classical selector chooses a deterministic finite minimum",
          "[DirectCentauro][DirectCentauroSelector]") {
  const DirectCentauroClassicalMinimumSelector selector;

  SECTION("empty candidates produce no selection") {
    const auto selection = selector.select({});

    REQUIRE_FALSE(selection.valid);
  }

  SECTION("the exact smallest finite non-negative distance is selected") {
    const std::vector<DirectCentauroCandidate> candidates{
        {DirectCentauroCandidateKind::Beam, 0, 0, 1.0},
        {DirectCentauroCandidateKind::Pair, 1, 2, 0.125},
        {DirectCentauroCandidateKind::Pair, 3, 4, 0.5},
    };

    const auto selection = selector.select(candidates);

    REQUIRE(selection.valid);
    REQUIRE(selection.candidateIndex == 1);
    REQUIRE(selection.candidate.kind == DirectCentauroCandidateKind::Pair);
    REQUIRE(selection.candidate.i == 1);
    REQUIRE(selection.candidate.j == 2);
    REQUIRE(selection.candidate.distance == 0.125);
  }

  SECTION("negative and non-finite distances are ignored") {
    const std::vector<DirectCentauroCandidate> candidates{
        {DirectCentauroCandidateKind::Pair, 0, 1, -0.25},
        {DirectCentauroCandidateKind::Pair, 1, 2, std::numeric_limits<double>::quiet_NaN()},
        {DirectCentauroCandidateKind::Pair, 2, 3, std::numeric_limits<double>::infinity()},
        {DirectCentauroCandidateKind::Beam, 3, 3, 1.0},
    };

    const auto selection = selector.select(candidates);

    REQUIRE(selection.valid);
    REQUIRE(selection.candidateIndex == 3);
    REQUIRE(selection.candidate.kind == DirectCentauroCandidateKind::Beam);
  }

  SECTION("an exact tie keeps the first enumerated candidate") {
    const std::vector<DirectCentauroCandidate> candidates{
        {DirectCentauroCandidateKind::Pair, 0, 1, 0.25},
        {DirectCentauroCandidateKind::Beam, 2, 2, 0.25},
    };

    const auto selection = selector.select(candidates);

    REQUIRE(selection.valid);
    REQUIRE(selection.candidateIndex == 0);
    REQUIRE(selection.candidate.kind == DirectCentauroCandidateKind::Pair);
    REQUIRE(selection.candidate.i == 0);
    REQUIRE(selection.candidate.j == 1);
  }
}

/**
 * @brief Verifies reconstruction, four-vector summing, source relations, and invalid-coordinate skipping.
 *
 * Controlled particles exercise empty, singleton, merged, separated, and
 * E - pz failure cases without changing the production clustering sequence.
 * Any assertion failure identifies a reconstruction-contract regression.
 */
TEST_CASE("DirectCentauro reconstructs deterministic jets and source relations",
          "[DirectCentauro][DirectCentauroAlgorithm]") {
  constexpr double tolerance = 1.0e-6;

  DirectCentauroAlgorithm algo("DirectCentauroTest");
  algo.level(algorithms::LogLevel::kDebug);

  DirectCentauroJetReconstructionConfig config;
  config.rJet     = 1.0F;
  config.minCstPt = 0.0;
  config.maxCstPt = 100.0;
  config.minJetPt = 0.0;
  algo.applyConfig(config);
  algo.init();

  edm4hep::EventHeaderCollection headers;
  edm4eic::ReconstructedParticleCollection particles;
  DirectCentauroJetOutputCollection jets;

  SECTION("empty input produces empty output") {
    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.empty());
  }

  SECTION("one valid particle produces one jet with its source relation") {
    auto particle = particles.create();
    particle.setEnergy(2.0F);
    particle.setMomentum(edm4hep::Vector3f{2.0F, 0.0F, 0.0F});

    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.size() == 1);
    const auto jet = jets.at(0);
    REQUIRE_THAT(jet.getEnergy(), Catch::Matchers::WithinAbs(2.0, tolerance));
    REQUIRE_THAT(jet.getMomentum().x, Catch::Matchers::WithinAbs(2.0, tolerance));
    REQUIRE_THAT(jet.getMomentum().y, Catch::Matchers::WithinAbs(0.0, tolerance));
    REQUIRE_THAT(jet.getMomentum().z, Catch::Matchers::WithinAbs(0.0, tolerance));
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    REQUIRE(jet.constituents_size() == 1);
    REQUIRE(jet.getConstituents(0).getObjectID() == particle.getObjectID());
#else
    REQUIRE(jet.particles_size() == 1);
    REQUIRE(jet.getParticles(0).getObjectID() == particle.getObjectID());
#endif
  }

  SECTION("nearby particles merge with summed four-momentum and both source relations") {
    auto first = particles.create();
    first.setEnergy(1.0F);
    first.setMomentum(edm4hep::Vector3f{1.0F, 0.0F, 0.0F});

    auto second = particles.create();
    second.setEnergy(2.0F);
    second.setMomentum(edm4hep::Vector3f{2.0F, 0.0F, 0.0F});

    // Both have (x, y) = (2 px / E, 2 py / E) = (2, 0), so d_pair = 0 < 1.
    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.size() == 1);
    const auto jet = jets.at(0);
    REQUIRE_THAT(jet.getEnergy(), Catch::Matchers::WithinAbs(3.0, tolerance));
    REQUIRE_THAT(jet.getMomentum().x, Catch::Matchers::WithinAbs(3.0, tolerance));
    REQUIRE_THAT(jet.getMomentum().y, Catch::Matchers::WithinAbs(0.0, tolerance));
    REQUIRE_THAT(jet.getMomentum().z, Catch::Matchers::WithinAbs(0.0, tolerance));
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    REQUIRE(jet.constituents_size() == 2);
    REQUIRE(jet.getConstituents(0).getObjectID() == first.getObjectID());
    REQUIRE(jet.getConstituents(1).getObjectID() == second.getObjectID());
#else
    REQUIRE(jet.particles_size() == 2);
    REQUIRE(jet.getParticles(0).getObjectID() == first.getObjectID());
    REQUIRE(jet.getParticles(1).getObjectID() == second.getObjectID());
#endif
  }

  SECTION("separated particles produce pT-descending single-constituent jets") {
    auto lowerPt = particles.create();
    lowerPt.setEnergy(2.0F);
    lowerPt.setMomentum(edm4hep::Vector3f{-2.0F, 0.0F, 0.0F});

    auto higherPt = particles.create();
    higherPt.setEnergy(3.0F);
    higherPt.setMomentum(edm4hep::Vector3f{3.0F, 0.0F, 0.0F});

    // Their coordinates are (-2, 0) and (2, 0), so d_pair = 4^2 / R^2 = 16 > 1.
    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.size() == 2);
    const auto leading    = jets.at(0);
    const auto subleading = jets.at(1);
    REQUIRE_THAT(leading.getMomentum().x, Catch::Matchers::WithinAbs(3.0, tolerance));
    REQUIRE_THAT(subleading.getMomentum().x, Catch::Matchers::WithinAbs(-2.0, tolerance));
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    REQUIRE(leading.constituents_size() == 1);
    REQUIRE(subleading.constituents_size() == 1);
    REQUIRE(leading.getConstituents(0).getObjectID() == higherPt.getObjectID());
    REQUIRE(subleading.getConstituents(0).getObjectID() == lowerPt.getObjectID());
#else
    REQUIRE(leading.particles_size() == 1);
    REQUIRE(subleading.particles_size() == 1);
    REQUIRE(leading.getParticles(0).getObjectID() == higherPt.getObjectID());
    REQUIRE(subleading.getParticles(0).getObjectID() == lowerPt.getObjectID());
#endif
  }

  SECTION("a particle with invalid E minus pz is skipped") {
    auto invalid = particles.create();
    invalid.setEnergy(1.0F);
    invalid.setMomentum(edm4hep::Vector3f{1.0F, 0.0F, 1.0F});

    auto valid = particles.create();
    valid.setEnergy(2.0F);
    valid.setMomentum(edm4hep::Vector3f{-2.0F, 0.0F, 0.0F});

    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.size() == 1);
    const auto jet = jets.at(0);
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    REQUIRE(jet.constituents_size() == 1);
    REQUIRE(jet.getConstituents(0).getObjectID() == valid.getObjectID());
#else
    REQUIRE(jet.particles_size() == 1);
    REQUIRE(jet.getParticles(0).getObjectID() == valid.getObjectID());
#endif
  }
}

/**
 * @brief Verifies exclusive constituent and inclusive output-jet pT thresholds.
 *
 * Boundary values encode the physical acceptance policy used before and after
 * sequential recombination; assertion failures identify acceptance regressions.
 */
TEST_CASE("DirectCentauro applies constituent and final-jet pT boundaries",
          "[DirectCentauro][DirectCentauroCuts]") {
  edm4hep::EventHeaderCollection headers;

  SECTION("constituent pT must be strictly inside the configured range") {
    DirectCentauroAlgorithm algo("DirectCentauroConstituentCutsTest");
    DirectCentauroJetReconstructionConfig config;
    config.rJet     = 1.0F;
    config.minCstPt = 1.0;
    config.maxCstPt = 3.0;
    config.minJetPt = 0.0;
    algo.applyConfig(config);
    algo.init();

    edm4eic::ReconstructedParticleCollection particles;
    auto atMinimum = particles.create();
    atMinimum.setEnergy(1.0F);
    atMinimum.setMomentum(edm4hep::Vector3f{1.0F, 0.0F, 0.0F});

    auto insideRange = particles.create();
    insideRange.setEnergy(2.0F);
    insideRange.setMomentum(edm4hep::Vector3f{0.0F, 2.0F, 0.0F});

    auto atMaximum = particles.create();
    atMaximum.setEnergy(3.0F);
    atMaximum.setMomentum(edm4hep::Vector3f{-3.0F, 0.0F, 0.0F});

    DirectCentauroJetOutputCollection jets;
    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.size() == 1);
    const auto jet = jets.at(0);
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    REQUIRE(jet.constituents_size() == 1);
    REQUIRE(jet.getConstituents(0).getObjectID() == insideRange.getObjectID());
#else
    REQUIRE(jet.particles_size() == 1);
    REQUIRE(jet.getParticles(0).getObjectID() == insideRange.getObjectID());
#endif
  }

  SECTION("a jet exactly at minimum pT is retained while a lower-pT jet is removed") {
    DirectCentauroAlgorithm algo("DirectCentauroJetCutTest");
    DirectCentauroJetReconstructionConfig config;
    config.rJet     = 1.0F;
    config.minCstPt = 0.0;
    config.maxCstPt = 100.0;
    config.minJetPt = 2.0;
    algo.applyConfig(config);
    algo.init();

    edm4eic::ReconstructedParticleCollection particles;
    auto belowMinimum = particles.create();
    belowMinimum.setEnergy(1.5F);
    belowMinimum.setMomentum(edm4hep::Vector3f{-1.5F, 0.0F, 0.0F});

    auto atMinimum = particles.create();
    atMinimum.setEnergy(2.0F);
    atMinimum.setMomentum(edm4hep::Vector3f{2.0F, 0.0F, 0.0F});

    DirectCentauroJetOutputCollection jets;
    algo.process({&headers, &particles}, {&jets});

    REQUIRE(jets.size() == 1);
    const auto jet = jets.at(0);
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    REQUIRE(jet.constituents_size() == 1);
    REQUIRE(jet.getConstituents(0).getObjectID() == atMinimum.getObjectID());
#else
    REQUIRE(jet.particles_size() == 1);
    REQUIRE(jet.getParticles(0).getObjectID() == atMinimum.getObjectID());
#endif
  }
}

/**
 * @brief Verifies that invalid numerical and kinematic configuration fails before event processing.
 *
 * Initialization throws rather than permitting undefined Centauro geometry or
 * inverted momentum acceptance ranges. Assertion failures identify a missing
 * pre-event validation failure.
 */
TEST_CASE("DirectCentauro rejects invalid configuration during initialization",
          "[DirectCentauro][DirectCentauroConfig]") {
  SECTION("jet radius must be positive") {
    DirectCentauroAlgorithm algo("DirectCentauroInvalidRadiusTest");
    DirectCentauroJetReconstructionConfig config;
    config.rJet = 0.0F;
    algo.applyConfig(config);

    REQUIRE_THROWS_AS(algo.init(), std::runtime_error);
  }

  SECTION("minimum constituent pT must be non-negative") {
    DirectCentauroAlgorithm algo("DirectCentauroInvalidMinimumConstituentPtTest");
    DirectCentauroJetReconstructionConfig config;
    config.minCstPt = -1.0;
    algo.applyConfig(config);

    REQUIRE_THROWS_AS(algo.init(), std::runtime_error);
  }

  SECTION("maximum constituent pT must exceed the minimum") {
    DirectCentauroAlgorithm algo("DirectCentauroInvalidMaximumConstituentPtTest");
    DirectCentauroJetReconstructionConfig config;
    config.maxCstPt = config.minCstPt;
    algo.applyConfig(config);

    REQUIRE_THROWS_AS(algo.init(), std::runtime_error);
  }

  SECTION("minimum jet pT must be non-negative") {
    DirectCentauroAlgorithm algo("DirectCentauroInvalidMinimumJetPtTest");
    DirectCentauroJetReconstructionConfig config;
    config.minJetPt = -1.0;
    algo.applyConfig(config);

    REQUIRE_THROWS_AS(algo.init(), std::runtime_error);
  }

  SECTION("the E minus pz denominator epsilon must be positive") {
    DirectCentauroAlgorithm algo("DirectCentauroInvalidDenominatorEpsilonTest");
    DirectCentauroJetReconstructionConfig config;
    config.denominatorEpsilon = 0.0;
    algo.applyConfig(config);

    REQUIRE_THROWS_AS(algo.init(), std::runtime_error);
  }
}

TEST_CASE("DirectCentauro quantum modes retain bounded local fallback semantics",
          "[DirectCentauro][DirectCentauroQuantum]") {
  const auto tracePath = std::filesystem::temp_directory_path() /
                         ("direct_centauro_trace_" + std::to_string(::getpid()) + ".jsonl");
  const auto cleanup = [&tracePath] { std::filesystem::remove(tracePath); };

  SECTION("classical mode performs no IPC") {
    const auto trace = runQuantumMode("classical", "", tracePath.string());
    REQUIRE(trace.find("\"sampled_index\":null") != std::string::npos);
    REQUIRE(trace.find("\"fallback\":false") != std::string::npos);
    cleanup();
  }

  SECTION("shadow records a valid nonminimum index but executes the classical candidate") {
    ScriptedSocket server({"dynamic:1", "dynamic:0"});
    const auto trace = runQuantumMode("qiskit_shadow", server.path(), tracePath.string());
    REQUIRE(trace.find("\"sampled_index\":1,\"applied_index\":0") != std::string::npos);
    REQUIRE(trace.find("\"qiskit_response_valid\":true,\"qiskit_applied\":false") != std::string::npos);
    REQUIRE(trace.find("\"fallback_count\":0") != std::string::npos);
    cleanup();
  }

  SECTION("active executes the valid returned candidate index") {
    ScriptedSocket server({"dynamic:1", "dynamic:0"});
    const auto trace = runQuantumMode("qiskit_active", server.path(), tracePath.string());
    REQUIRE(trace.find("\"sampled_index\":1,\"applied_index\":1") != std::string::npos);
    REQUIRE(trace.find("\"qiskit_applied\":true") != std::string::npos);
    REQUIRE(trace.find("\"preparation_method\":\"stabilized_state_preparation\"") !=
            std::string::npos);
    REQUIRE(trace.find("\"request_serialization_ms\":") != std::string::npos);
    REQUIRE(trace.find("\"worker_request_parsing_validation_ms\":0.1") !=
            std::string::npos);
    cleanup();
  }

  SECTION("trace JSONL preserves an escaped worker identity") {
    ScriptedSocket server({"dynamic-escaped:1", "dynamic-escaped:0"});
    const auto trace = runQuantumMode("qiskit_shadow", server.path(), tracePath.string());
    std::istringstream lines(trace);
    std::string line;
    std::size_t iterationCount = 0U;
    while (std::getline(lines, line)) {
      const auto record = nlohmann::json::parse(line);
      if (record.at("record_type") == "iteration") {
        REQUIRE(record.at("worker_identity") == "worker\"\\identity\n");
        ++iterationCount;
      }
    }
    REQUIRE(iterationCount == 2U);
    cleanup();
  }

  SECTION("unavailable malformed and out-of-range replies fall back with explicit reasons") {
    const auto unavailable = runQuantumMode("qiskit_active", testSocketPath(), tracePath.string());
    REQUIRE(unavailable.find("\"fallback_reason\":\"service_unavailable\"") != std::string::npos);
    cleanup();
    ScriptedSocket malformed({"ERR\n", "ERR\n"});
    const auto malformedTrace = runQuantumMode("qiskit_active", malformed.path(), tracePath.string());
    REQUIRE(malformedTrace.find("\"fallback_reason\":\"malformed_response\"") != std::string::npos);
    REQUIRE(malformedTrace.find("\"fallback_count\":2") != std::string::npos);
    cleanup();
    ScriptedSocket outOfRange({"dynamic:99", "dynamic:99"});
    const auto outOfRangeTrace = runQuantumMode("qiskit_active", outOfRange.path(), tracePath.string());
    REQUIRE(outOfRangeTrace.find("\"fallback_reason\":\"out_of_range_index\"") != std::string::npos);
    cleanup();
  }

  SECTION("a local response timeout falls back without applying a quantum index") {
    ScriptedSocket server({"__timeout__", "__timeout__"});
    const auto trace = runQuantumMode("qiskit_active", server.path(), tracePath.string());
    REQUIRE(trace.find("\"fallback_reason\":\"service_timeout\"") != std::string::npos);
    REQUIRE(trace.find("\"timeout\":true") != std::string::npos);
    cleanup();
  }

  SECTION("oversize candidate lists bypass IPC and count the local guard") {
    const auto trace = runQuantumMode("qiskit_shadow", testSocketPath(), tracePath.string(), 8U);
    REQUIRE(trace.find("\"fallback_reason\":\"local_oversize_guard\"") != std::string::npos);
    REQUIRE(trace.find("\"fallback_count\":") != std::string::npos);
    cleanup();
  }

  SECTION("active fail-closed rejects unavailable, malformed, and oversize requests") {
    REQUIRE_THROWS_AS(
        runQuantumMode("qiskit_active", testSocketPath(), tracePath.string(), 2U, true),
        std::runtime_error);
    cleanup();
    ScriptedSocket malformed({"ERR\n"});
    REQUIRE_THROWS_AS(
        runQuantumMode("qiskit_active", malformed.path(), tracePath.string(), 2U, true),
        std::runtime_error);
    cleanup();
    REQUIRE_THROWS_AS(
        runQuantumMode("qiskit_active", testSocketPath(), tracePath.string(), 8U, true),
        std::runtime_error);
    cleanup();
  }

  SECTION("candidate capacity defaults to 128 and rejects larger or non-power-of-two values") {
    DirectCentauroJetReconstructionConfig config;
    REQUIRE(config.qiskitMaxCandidates == 128U);
    config.qiskitMaxCandidates = 128U;
    DirectCentauroAlgorithm algorithm("DirectCentauroQuantumCapacityTest");
    algorithm.applyConfig(config);
    REQUIRE_NOTHROW(algorithm.init());
    config.qiskitMaxCandidates = 256U;
    DirectCentauroAlgorithm tooLarge("DirectCentauroTooLargeQuantumCapacityTest");
    tooLarge.applyConfig(config);
    REQUIRE_THROWS_AS(tooLarge.init(), std::runtime_error);
    config.qiskitMaxCandidates = 48U;
    DirectCentauroAlgorithm invalid("DirectCentauroInvalidQuantumCapacityTest");
    invalid.applyConfig(config);
    REQUIRE_THROWS_AS(invalid.init(), std::runtime_error);
  }

  SECTION("invalid quantum mode fails during initialization") {
    DirectCentauroAlgorithm algorithm("DirectCentauroInvalidQuantumModeTest");
    DirectCentauroJetReconstructionConfig config;
    config.quantumMode = "invalid";
    algorithm.applyConfig(config);
    REQUIRE_THROWS_AS(algorithm.init(), std::runtime_error);
  }
}

TEST_CASE("DirectCentauro SHA-256 matches standard known vectors", "[DirectCentauro][SHA256]") {
  REQUIRE(eicrecon::directCentauroSha256("") ==
          "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  REQUIRE(eicrecon::directCentauroSha256("abc") ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST_CASE("DirectCentauro sends only the v1 blind selector DTO over AF_UNIX",
          "[DirectCentauro][DirectCentauroQuantum][BlindContract]") {
  const std::vector<DirectCentauroCandidate> candidates{
      {DirectCentauroCandidateKind::Pair, 0, 1, 0.5},
      {DirectCentauroCandidateKind::Beam, 0, 0, 1.0},
      {DirectCentauroCandidateKind::Beam, 1, 1, 2.0},
  };
  ScriptedSocket server({"dynamic:0"});
  eicrecon::DirectCentauroQuantumSocketClient client;

  const auto reply = client.request(server.path(), 1000U, candidates, "4:7", 2U, 64U,
                                    3.0, 17U);

  REQUIRE(reply.valid);
  REQUIRE(reply.index == 0U);
  REQUIRE(reply.qubits == 2U);
  REQUIRE(reply.shots == 64U);
  REQUIRE(reply.workerPid == 1234UL);
  REQUIRE(reply.requestSequence == 9UL);
  REQUIRE(reply.workerIdentity == "direct_centauro_aer_1234");
  REQUIRE(reply.responseSchemaVersion == "selector-response/v1");
  REQUIRE(reply.preparationMethod == "historical_v1");
  const auto request = nlohmann::json::parse(server.firstRequest());
  REQUIRE(request.at("schema_version") == "selector-request/v1");
  REQUIRE(request.at("request_id") == "4:7-2");
  REQUIRE(request.at("shots") == 64U);
  REQUIRE(request.at("exponent_a") == 3.0);
  REQUIRE(request.at("seed") == 17U);
  REQUIRE(request.at("candidates").at(0).at("candidate_index") == 0U);
  REQUIRE_FALSE(request.contains("classical_selected_index"));
  REQUIRE_FALSE(request.contains("minimum_indices"));
  REQUIRE_FALSE(request.contains("classical_argmin"));
  REQUIRE_FALSE(request.contains("final_partition"));
}

TEST_CASE("DirectCentauro validates strict response v2 metadata and preserves v1 compatibility",
          "[DirectCentauro][DirectCentauroQuantum][ResponseContract]") {
  const std::vector<DirectCentauroCandidate> candidates{
      {DirectCentauroCandidateKind::Pair, 0, 1, 0.5},
      {DirectCentauroCandidateKind::Beam, 0, 0, 1.0},
      {DirectCentauroCandidateKind::Beam, 1, 1, 2.0},
  };
  const auto mismatchedDigestV2 = [] {
    return nlohmann::json{
        {"schema_version", "selector-response/v2"}, {"request_id", "4:7-2"},
        {"request_sha256", std::string(64U, '0')}, {"status", "ok"},
        {"selected_candidate_index", 0}, {"counts_by_candidate", {{"0", 60}, {"1", 4}, {"2", 0}}},
        {"amplitudes", {0.9, 0.4, 0.1}}, {"probabilities", {0.81, 0.16, 0.03}},
        {"circuit", {{"depth", 7}, {"qubits", 2}}}, {"timings_ms", {{"sampling", 1.0}, {"state_preparation", 0.5}, {"request_parsing_validation", 0.1}, {"response_assembly_serialization", 0.2}}},
        {"zero_distance_bypass", false}, {"worker", {{"identity", "direct_centauro_aer_1234"}, {"implementation", "direct_centauro_aer"}, {"pid", 1234}, {"request_sequence", 9}}},
        {"preparation", {{"method", "stabilized_state_preparation"}, {"version", "v1"}, {"cutoff", 1e-12}, {"dropped_probability_mass", 1e-13}, {"state_fidelity", 1.0}}},
    };
  };
  const auto request = [&candidates](const nlohmann::json& value) {
    ScriptedSocket server({value.dump() + "\n"});
    eicrecon::DirectCentauroQuantumSocketClient client;
    return client.request(server.path(), 1000U, candidates, "4:7", 2U, 64U, 3.0, 17U);
  };

  ScriptedSocket acceptedServer({"dynamic-v2:0"});
  eicrecon::DirectCentauroQuantumSocketClient acceptedClient;
  const auto accepted = acceptedClient.request(acceptedServer.path(), 1000U, candidates, "4:7", 2U, 64U,
                                              3.0, 17U);
  REQUIRE(accepted.valid);
  REQUIRE(accepted.responseSchemaVersion == "selector-response/v2");
  REQUIRE(accepted.preparationMethod == "stabilized_state_preparation");
  REQUIRE(accepted.preparationVersion == "v1");
  REQUIRE(std::abs(accepted.preparationCutoff - 1e-12) < 1e-18);
  REQUIRE(std::abs(accepted.droppedProbabilityMass - 1e-13) < 1e-19);
  REQUIRE(std::abs(accepted.stateFidelity - 1.0) < 1e-12);

  SECTION("a mismatched request digest is rejected") {
    REQUIRE_FALSE(request(mismatchedDigestV2()).valid);
  }

  const auto requestInvalidV2 = [&candidates](const std::string& defect) {
    ScriptedSocket server({"invalid-v2:" + defect});
    eicrecon::DirectCentauroQuantumSocketClient client;
    return client.request(server.path(), 1000U, candidates, "4:7", 2U, 64U, 3.0, 17U);
  };
  REQUIRE_FALSE(requestInvalidV2("unknown_nested").valid);
  REQUIRE_FALSE(requestInvalidV2("forbidden_truth").valid);
  REQUIRE_FALSE(requestInvalidV2("malformed_metadata").valid);
}
