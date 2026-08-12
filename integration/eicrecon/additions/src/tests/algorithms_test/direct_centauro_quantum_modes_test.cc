// SPDX-License-Identifier: LGPL-3.0-or-later

#include <edm4eic/ReconstructedParticleCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <edm4hep/Vector3f.h>

#include <atomic>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "algorithms/reco/DirectCentauroJetReconstruction.h"
#include "algorithms/reco/DirectCentauroJetReconstructionConfig.h"

namespace {
using Algorithm = eicrecon::DirectCentauroJetReconstruction<edm4eic::ReconstructedParticle>;

std::string socketPath() {
  static std::atomic<unsigned> sequence{0};
  return "/tmp/direct_centauro_modes_" + std::to_string(::getpid()) + "_" +
         std::to_string(sequence++) + ".sock";
}

std::string response(std::size_t index, const nlohmann::json& request);

class ScriptedSocket {
public:
  explicit ScriptedSocket(std::vector<std::string> replies) : m_path(socketPath()) {
    m_descriptor = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (m_descriptor < 0) throw std::runtime_error("socket creation failed");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, m_path.c_str(), sizeof(address.sun_path) - 1U);
    if (::bind(m_descriptor, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0 ||
        ::listen(m_descriptor, 1) != 0) throw std::runtime_error("socket setup failed");
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
        } while (received > 0 && request.find('\n') == std::string::npos);
        auto actualReply = reply;
        const auto requestJson = nlohmann::json::parse(request);
        if (reply.rfind("dynamic:", 0) == 0) {
          actualReply = response(std::stoull(reply.substr(8)), requestJson);
        } else if (reply.rfind("dynamic-mismatched-digest:", 0) == 0) {
          actualReply = response(std::stoull(reply.substr(std::string{"dynamic-mismatched-digest:"}.size())), requestJson);
          auto responseJson = nlohmann::json::parse(actualReply);
          responseJson["request_sha256"] = "0";
          actualReply = responseJson.dump() + "\n";
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
private:
  int m_descriptor;
  std::string m_path;
  std::thread m_thread;
};

std::string run(const std::string& mode, const std::string& socket, const std::string& trace,
                std::size_t count = 2U, bool failClosed = false) {
  Algorithm algorithm("DirectCentauroQuantumModesTest");
  eicrecon::DirectCentauroJetReconstructionConfig config;
  config.minCstPt = 0.0;
  config.maxCstPt = 100.0;
  config.minJetPt = 0.0;
  config.quantumMode = mode;
  config.quantumSocketPath = socket;
  config.quantumTracePath = trace;
  config.quantumFailClosed = failClosed;
  algorithm.applyConfig(config);
  algorithm.init();
  edm4hep::EventHeaderCollection headers;
  edm4eic::ReconstructedParticleCollection particles;
  for (std::size_t index = 0; index < count; ++index) {
    auto particle = particles.create();
    particle.setEnergy(1.0F);
    particle.setMomentum(edm4hep::Vector3f{1.0F - static_cast<float>(index) * 0.1F, 0.0F, 0.0F});
  }
  eicrecon::DirectCentauroJetOutputCollection jets;
  algorithm.process({&headers, &particles}, {&jets});
  std::ifstream input(trace);
  return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

std::string response(std::size_t index, const nlohmann::json& request) {
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
  return nlohmann::json{
      {"amplitudes", amplitudes}, {"circuit", {{"depth", 2}, {"qubits", 1}}},
      {"counts_by_candidate", counts}, {"probabilities", probabilities}, {"request_id", requestId},
        {"request_sha256", eicrecon::directCentauroSha256(request.dump())}, {"schema_version", "selector-response/v2"},
      {"selected_candidate_index", index}, {"status", "ok"},
       {"timings_ms", {{"sampling", 1.0}, {"state_preparation", 0.5}, {"request_parsing_validation", 0.1}, {"response_assembly_serialization", 0.2}}},
       {"worker", {{"identity", "direct_centauro_aer_1234"}, {"implementation", "direct_centauro_aer"}, {"pid", 1234}, {"request_sequence", 1}}},
       {"preparation", {{"method", "stabilized_state_preparation"}, {"version", "v1"}, {"cutoff", 1e-12}, {"dropped_probability_mass", 0.0}, {"state_fidelity", 1.0}}},
      {"zero_distance_bypass", false},
  }.dump() + "\n";
}

} // namespace

int main() {
  try {
    const auto trace = (std::filesystem::temp_directory_path() /
                        ("direct_centauro_modes_" + std::to_string(::getpid()) + ".jsonl"))
                           .string();
    auto clear = [&trace] { std::filesystem::remove(trace); };
    require(run("classical", "", trace).find("\"sampled_index\":null") != std::string::npos,
            "classical mode attempted IPC");
    clear();
    { ScriptedSocket server({"dynamic-mismatched-digest:1", "dynamic-mismatched-digest:0"});
      require(run("qiskit_active", server.path(), trace).find("malformed_response") != std::string::npos,
              "mismatched request digest was accepted"); }
    clear();
    { ScriptedSocket server({"dynamic:1", "dynamic:0"});
      require(run("qiskit_shadow", server.path(), trace).find("\"sampled_index\":1,\"applied_index\":0") != std::string::npos,
              "shadow did not preserve the classical selection"); }
    clear();
    { ScriptedSocket server({"dynamic:1", "dynamic:0"});
      const auto result = run("qiskit_active", server.path(), trace);
      require(result.find("\"sampled_index\":1,\"applied_index\":1") != std::string::npos &&
              result.find("\"preparation_method\":\"stabilized_state_preparation\"") != std::string::npos &&
              result.find("\"request_serialization_ms\":") != std::string::npos &&
              result.find("\"worker_request_parsing_validation_ms\":0.1") != std::string::npos,
               "active did not execute the valid returned index"); }
    clear();
    require(run("qiskit_active", socketPath(), trace).find("service_unavailable") != std::string::npos,
            "unavailable service did not fall back");
    clear();
    bool failClosed = false;
    try { (void)run("qiskit_active", socketPath(), trace, 2U, true); } catch (const std::runtime_error&) { failClosed = true; }
    require(failClosed, "fail-closed active mode admitted an unavailable service");
    clear();
    { ScriptedSocket server({"ERR\n", "ERR\n"});
      const auto result = run("qiskit_active", server.path(), trace);
      require(result.find("malformed_response") != std::string::npos && result.find("\"fallback_count\":2") != std::string::npos,
               "malformed reply fallback was not counted"); }
    clear();
    failClosed = false;
    try { ScriptedSocket server({"ERR\n"}); (void)run("qiskit_active", server.path(), trace, 2U, true); }
    catch (const std::runtime_error&) { failClosed = true; }
    require(failClosed, "fail-closed active mode admitted a malformed response");
    clear();
    { ScriptedSocket server({"dynamic:99", "dynamic:99"});
      require(run("qiskit_active", server.path(), trace).find("out_of_range_index") != std::string::npos,
              "out-of-range reply did not fall back"); }
    clear();
    require(run("qiskit_shadow", socketPath(), trace, 8U).find("local_oversize_guard") != std::string::npos,
            "oversize request reached IPC");
    clear();
    failClosed = false;
    try { (void)run("qiskit_active", socketPath(), trace, 8U, true); }
    catch (const std::runtime_error&) { failClosed = true; }
    require(failClosed, "fail-closed active mode admitted an oversize request");
    clear();
    Algorithm invalid("DirectCentauroInvalidModeTest");
    eicrecon::DirectCentauroJetReconstructionConfig config;
    config.quantumMode = "invalid";
    invalid.applyConfig(config);
    bool threw = false;
    try { invalid.init(); } catch (const std::runtime_error&) { threw = true; }
    require(threw, "invalid mode was accepted");
    std::cout << "direct_centauro_quantum_modes_test: PASS\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "direct_centauro_quantum_modes_test: FAIL: " << error.what() << '\n';
    return 1;
  }
}
