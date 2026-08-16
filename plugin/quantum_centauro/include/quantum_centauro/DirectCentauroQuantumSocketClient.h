// SPDX-License-Identifier: LGPL-3.0-or-later

#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <cerrno>
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <set>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "quantum_centauro/DirectCentauroJetMinimumSelector.h"

namespace eicrecon {

inline std::string directCentauroSha256(const std::string& input) {
  static constexpr std::uint32_t roundConstants[] = {
      0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
      0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
      0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
      0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
      0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
      0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
      0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
      0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U, 0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
  };
  const auto rotateRight = [](std::uint32_t value, unsigned shift) {
    return (value >> shift) | (value << (32U - shift));
  };
  std::vector<std::uint8_t> bytes(input.begin(), input.end());
  const std::uint64_t bitLength = static_cast<std::uint64_t>(bytes.size()) * 8U;
  bytes.push_back(0x80U);
  while ((bytes.size() + 8U) % 64U != 0U) bytes.push_back(0U);
  for (int shift = 56; shift >= 0; shift -= 8) bytes.push_back(static_cast<std::uint8_t>(bitLength >> shift));
  std::uint32_t state[] = {0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
                           0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U};
  for (std::size_t offset = 0; offset < bytes.size(); offset += 64U) {
    std::uint32_t words[64]{};
    for (std::size_t index = 0; index < 16U; ++index) {
      words[index] = (static_cast<std::uint32_t>(bytes[offset + index * 4U]) << 24U) |
                     (static_cast<std::uint32_t>(bytes[offset + index * 4U + 1U]) << 16U) |
                     (static_cast<std::uint32_t>(bytes[offset + index * 4U + 2U]) << 8U) |
                     static_cast<std::uint32_t>(bytes[offset + index * 4U + 3U]);
    }
    for (std::size_t index = 16U; index < 64U; ++index) {
      const auto small0 = rotateRight(words[index - 15U], 7U) ^ rotateRight(words[index - 15U], 18U) ^ (words[index - 15U] >> 3U);
      const auto small1 = rotateRight(words[index - 2U], 17U) ^ rotateRight(words[index - 2U], 19U) ^ (words[index - 2U] >> 10U);
      words[index] = words[index - 16U] + small0 + words[index - 7U] + small1;
    }
    std::uint32_t a = state[0], b = state[1], c = state[2], d = state[3], e = state[4], f = state[5], g = state[6], h = state[7];
    for (std::size_t index = 0; index < 64U; ++index) {
      const auto sum1 = rotateRight(e, 6U) ^ rotateRight(e, 11U) ^ rotateRight(e, 25U);
      const auto choice = (e & f) ^ (~e & g);
      const auto sum0 = rotateRight(a, 2U) ^ rotateRight(a, 13U) ^ rotateRight(a, 22U);
      const auto majority = (a & b) ^ (a & c) ^ (b & c);
      const auto temporary1 = h + sum1 + choice + roundConstants[index] + words[index];
      const auto temporary2 = sum0 + majority;
      h = g; g = f; f = e; e = d + temporary1; d = c; c = b; b = a; a = temporary1 + temporary2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
  }
  std::ostringstream digest;
  digest << std::hex << std::setfill('0');
  for (const auto value : state) digest << std::setw(8) << value;
  return digest.str();
}

struct DirectCentauroQuantumReply {
  bool valid = false;
  std::size_t index = 0;
  std::string reason;
  double latencyMilliseconds = 0.0;
  double statePreparationMilliseconds = 0.0;
  double samplingMilliseconds = 0.0;
  unsigned qubits = 0;
  unsigned shots = 0;
  double exponent = 0.0;
  unsigned long workerPid = 0;
  unsigned long requestSequence = 0;
  std::string workerIdentity;
  std::vector<unsigned> countsByCandidate;
  std::vector<double> amplitudes;
  std::vector<double> probabilities;
  bool timeout = false;
  bool serviceError = false;
  std::string failureStage = "none";
  int socketErrno = 0;
  std::size_t responseBytes = 0;
  bool frameComplete = false;
  std::string workerStatus = "none";
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
};

/** A bounded local Unix-socket client. The worker, not C++, owns Qiskit state. */
class DirectCentauroQuantumSocketClient {
public:
  [[nodiscard]] DirectCentauroQuantumReply request(const std::string& socketPath,
                                                    unsigned timeoutMilliseconds,
                                                    const std::vector<DirectCentauroCandidate>& candidates,
                                                    const std::string& event,
                                                    std::size_t iteration,
                                                    unsigned shots,
                                                    double exponent,
                                                    unsigned seed) const {
    const auto started = std::chrono::steady_clock::now();
    DirectCentauroQuantumReply reply;
    const auto finish = [&reply, &started](std::string reason) {
      reply.reason = std::move(reason);
      reply.failureStage = reply.reason;
      reply.latencyMilliseconds = std::chrono::duration<double, std::milli>(
                                   std::chrono::steady_clock::now() - started)
                                   .count();
      reply.timeout = reply.reason.find("timeout") != std::string::npos;
      reply.serviceError = !reply.valid && !reply.timeout;
      return reply;
    };
    if (socketPath.empty() || socketPath.size() >= sizeof(sockaddr_un::sun_path)) {
      return finish("invalid_socket_path");
    }
    if (candidates.empty()) {
      return finish("empty_candidate_list");
    }
    if (shots == 0U || !std::isfinite(exponent) || exponent <= 0.0) {
      return finish("invalid_selector_controls");
    }
    for (const auto& candidate : candidates) {
      const double distance = candidate.distance;
      if (!std::isfinite(distance) || distance < 0.0) {
        return finish("invalid_candidate_distance");
      }
    }
    const int descriptor = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (descriptor < 0) {
      return finish("socket_unavailable");
    }
    timeval timeout{static_cast<time_t>(timeoutMilliseconds / 1000U),
                    static_cast<suseconds_t>((timeoutMilliseconds % 1000U) * 1000U)};
    ::setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    ::setsockopt(descriptor, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1U);
    if (::connect(descriptor, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0) {
      reply.socketErrno = errno;
      ::close(descriptor);
      return finish(errno == EAGAIN || errno == EWOULDBLOCK ? "service_connect_timeout"
                                                             : "service_unavailable");
    }
    nlohmann::json request{
        {"schema_version", "selector-request/v1"},
        {"request_id", event + "-" + std::to_string(iteration)},
        {"event", event},
        {"iteration", iteration},
        {"candidates", nlohmann::json::array()},
        {"shots", shots},
        {"exponent_a", exponent},
        {"seed", seed},
    };
    for (std::size_t index = 0; index < candidates.size(); ++index) {
      const auto& candidate = candidates[index];
      request["candidates"].push_back({
          {"candidate_index", index},
          {"kind", candidate.kind == DirectCentauroCandidateKind::Pair ? "pair" : "beam"},
          {"i", candidate.i},
          {"j", candidate.kind == DirectCentauroCandidateKind::Pair
                    ? nlohmann::json(candidate.j)
                    : nlohmann::json(nullptr)},
          {"distance", candidate.distance},
      });
    }
    const auto serializationStarted = std::chrono::steady_clock::now();
    const auto canonicalRequest = request.dump();
    const auto requestText = canonicalRequest + "\n";
    reply.requestSerializationMilliseconds = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - serializationStarted).count();
    if (requestText.size() > 64U * 1024U) return finish("request_too_large");
    const auto transportStarted = std::chrono::steady_clock::now();
    std::size_t sent = 0;
    while (sent < requestText.size()) {
      const ssize_t written = ::send(descriptor, requestText.data() + sent,
                                     requestText.size() - sent, MSG_NOSIGNAL);
      if (written <= 0) {
        reply.socketErrno = errno;
        ::close(descriptor);
        return finish("service_write_failure");
      }
      sent += static_cast<std::size_t>(written);
    }
    std::string text;
    char response[4096]{};
    ssize_t received = 0;
    do {
      received = ::recv(descriptor, response, sizeof(response), 0);
      if (received > 0) text.append(response, static_cast<std::size_t>(received));
    } while (received > 0 && text.find('\n') == std::string::npos && text.size() <= 64U * 1024U);
    reply.responseBytes = text.size();
    if (received < 0) reply.socketErrno = errno;
    ::close(descriptor);
    if (received <= 0) {
      return finish(errno == EAGAIN || errno == EWOULDBLOCK ? "service_timeout"
                                                             : "service_empty_response");
    }
    const auto newline = text.find('\n');
    reply.frameComplete = newline != std::string::npos && newline + 1 == text.size();
    if (text.size() > 64U * 1024U || newline == std::string::npos || newline + 1 != text.size()) {
      return finish("malformed_response");
    }
    text.pop_back();
    if (text == "ERR") {
      reply.workerStatus = "err";
      return finish("malformed_response");
    }
    reply.transportWaitMilliseconds = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - transportStarted).count();
    const auto parsingStarted = std::chrono::steady_clock::now();
    try {
      const auto parsed = nlohmann::json::parse(text);
      if (!parsed.is_object()) return finish("malformed_response");
       const std::set<std::string> expectedV1{
           "schema_version", "request_id", "request_sha256", "status", "selected_candidate_index",
           "counts_by_candidate", "amplitudes", "probabilities", "circuit", "timings_ms",
           "zero_distance_bypass", "worker",
       };
       auto expectedV2 = expectedV1;
       expectedV2.insert("preparation");
      std::set<std::string> actual;
      for (auto iterator = parsed.begin(); iterator != parsed.end(); ++iterator) {
        actual.insert(iterator.key());
      }
       if (!parsed.contains("schema_version") || !parsed.at("schema_version").is_string()) return finish("malformed_response");
       const auto schemaVersion = parsed.at("schema_version").get<std::string>();
       const bool v1 = schemaVersion == "selector-response/v1";
       const bool v2 = schemaVersion == "selector-response/v2";
       if ((!v1 && !v2) || actual != (v1 ? expectedV1 : expectedV2) ||
            parsed.at("request_id") != request.at("request_id") || parsed.at("status") != "ok" ||
           !parsed.at("request_sha256").is_string() || parsed.at("request_sha256").get<std::string>() != directCentauroSha256(canonicalRequest) ||
           !parsed.at("selected_candidate_index").is_number_unsigned() ||
          !parsed.at("counts_by_candidate").is_object() || !parsed.at("amplitudes").is_array() ||
          !parsed.at("probabilities").is_array() || !parsed.at("circuit").is_object() ||
          !parsed.at("timings_ms").is_object() || !parsed.at("zero_distance_bypass").is_boolean() ||
          !parsed.at("worker").is_object()) {
        return finish("malformed_response");
      }
       reply.index = parsed.at("selected_candidate_index").get<std::size_t>();
      if (reply.index >= candidates.size()) return finish("out_of_range_index");
      const auto& counts = parsed.at("counts_by_candidate");
      const auto& amplitudes = parsed.at("amplitudes");
      const auto& probabilities = parsed.at("probabilities");
      const auto& circuit = parsed.at("circuit");
      const auto& timings = parsed.at("timings_ms");
       const auto& worker = parsed.at("worker");
       reply.responseSchemaVersion = schemaVersion;
       if (v2) {
         const auto& preparation = parsed.at("preparation");
         const std::set<std::string> preparationExpected{
             "method", "version", "cutoff", "dropped_probability_mass", "state_fidelity",
         };
         std::set<std::string> preparationActual;
         if (!preparation.is_object()) return finish("malformed_response");
         for (auto iterator = preparation.begin(); iterator != preparation.end(); ++iterator) preparationActual.insert(iterator.key());
         if (preparationActual != preparationExpected || !preparation.at("method").is_string() ||
             !preparation.at("version").is_string() || !preparation.at("cutoff").is_number() ||
             !preparation.at("dropped_probability_mass").is_number() || !preparation.at("state_fidelity").is_number()) {
           return finish("malformed_response");
         }
         reply.preparationMethod = preparation.at("method").get<std::string>();
         reply.preparationVersion = preparation.at("version").get<std::string>();
         reply.preparationCutoff = preparation.at("cutoff").get<double>();
         reply.droppedProbabilityMass = preparation.at("dropped_probability_mass").get<double>();
         reply.stateFidelity = preparation.at("state_fidelity").get<double>();
         if ((reply.preparationMethod != "stabilized_state_preparation" && reply.preparationMethod != "exact_zero_bypass") ||
             reply.preparationVersion != "v1" || !std::isfinite(reply.preparationCutoff) ||
             !std::isfinite(reply.droppedProbabilityMass) || !std::isfinite(reply.stateFidelity) ||
             reply.preparationCutoff < 0.0 || reply.preparationCutoff > 1.0 ||
             reply.droppedProbabilityMass < 0.0 || reply.droppedProbabilityMass > 1.0 ||
             reply.stateFidelity < 0.0 || reply.stateFidelity > 1.0 + 1e-9) return finish("malformed_response");
       } else {
         reply.preparationMethod = "historical_v1";
         reply.preparationVersion = "v1";
         reply.stateFidelity = 1.0;
       }
      const std::set<std::string> workerAllowed{
          "implementation", "shots", "exponent", "seed", "max_candidates", "qiskit", "qiskit_aer",
          "pid", "identity", "request_sequence",
      };
      std::set<std::string> workerActual;
      for (auto iterator = worker.begin(); iterator != worker.end(); ++iterator) workerActual.insert(iterator.key());
      if (!worker.contains("implementation") || !worker.at("implementation").is_string() ||
          !worker.contains("pid") || !worker.at("pid").is_number_unsigned() ||
          !worker.contains("identity") || !worker.at("identity").is_string() ||
          !worker.contains("request_sequence") || !worker.at("request_sequence").is_number_unsigned() ||
          !std::includes(workerAllowed.begin(), workerAllowed.end(), workerActual.begin(), workerActual.end())) {
        return finish("malformed_response");
      }
       if (worker.at("pid").get<unsigned long>() == 0UL || worker.at("identity").get<std::string>().empty() ||
           worker.at("request_sequence").get<unsigned long>() == 0UL) return finish("malformed_response");
       if ((worker.contains("shots") && (!worker.at("shots").is_number_unsigned() || worker.at("shots").get<unsigned>() != shots)) ||
           (worker.contains("exponent") && (!worker.at("exponent").is_number() ||
                                            !std::isfinite(worker.at("exponent").get<double>()) ||
                                            worker.at("exponent").get<double>() != exponent)) ||
           (worker.contains("seed") && (!worker.at("seed").is_number_unsigned() || worker.at("seed").get<unsigned>() != seed)) ||
           (worker.contains("max_candidates") && (!worker.at("max_candidates").is_number_unsigned() ||
                                                   worker.at("max_candidates").get<std::size_t>() < candidates.size()))) {
         return finish("malformed_response");
       }
      reply.workerPid = worker.at("pid").get<unsigned long>();
      reply.workerIdentity = worker.at("identity").get<std::string>();
      reply.requestSequence = worker.at("request_sequence").get<unsigned long>();
      if (counts.size() != candidates.size() || probabilities.size() != candidates.size() || amplitudes.size() < candidates.size()) {
        return finish("malformed_response");
      }
      reply.countsByCandidate.clear();
      reply.amplitudes.clear();
      reply.probabilities.clear();
      for (std::size_t index = 0; index < candidates.size(); ++index) {
         const auto key = std::to_string(index);
         if (!counts.contains(key) || !counts.at(key).is_number_unsigned() || !probabilities.at(index).is_number() ||
             !amplitudes.at(index).is_number()) return finish("malformed_response");
         reply.countsByCandidate.push_back(counts.at(key).get<unsigned>());
         reply.probabilities.push_back(probabilities.at(index).get<double>());
         reply.amplitudes.push_back(amplitudes.at(index).get<double>());
         if (!std::isfinite(reply.probabilities.back()) || reply.probabilities.back() < 0.0 ||
             !std::isfinite(reply.amplitudes.back())) return finish("malformed_response");
      }
       const std::set<std::string> timingExpectedV1{"state_preparation", "sampling"};
       auto timingExpectedV2 = timingExpectedV1;
       timingExpectedV2.insert("request_parsing_validation");
       timingExpectedV2.insert("response_assembly_serialization");
       std::set<std::string> timingActual;
       for (auto iterator = timings.begin(); iterator != timings.end(); ++iterator) timingActual.insert(iterator.key());
       if (!circuit.contains("qubits") || !circuit.contains("depth") ||
           !circuit.at("qubits").is_number_unsigned() || !circuit.at("depth").is_number_unsigned() ||
           timingActual != (v1 ? timingExpectedV1 : timingExpectedV2) ||
           !timings.at("state_preparation").is_number() || !timings.at("sampling").is_number() ||
           (v2 && (!timings.at("request_parsing_validation").is_number() || !timings.at("response_assembly_serialization").is_number()))) {
        return finish("malformed_response");
      }
      reply.qubits = circuit.at("qubits").get<unsigned>();
       reply.statePreparationMilliseconds = timings.at("state_preparation").get<double>();
        reply.samplingMilliseconds = timings.at("sampling").get<double>();
       if (!std::isfinite(reply.statePreparationMilliseconds) || reply.statePreparationMilliseconds < 0.0 ||
           !std::isfinite(reply.samplingMilliseconds) || reply.samplingMilliseconds < 0.0) return finish("malformed_response");
        if (v2) {
          reply.workerRequestParsingValidationMilliseconds = timings.at("request_parsing_validation").get<double>();
          reply.workerResponseAssemblyMilliseconds = timings.at("response_assembly_serialization").get<double>();
          if (!std::isfinite(reply.workerRequestParsingValidationMilliseconds) ||
              reply.workerRequestParsingValidationMilliseconds < 0.0 ||
              !std::isfinite(reply.workerResponseAssemblyMilliseconds) ||
              reply.workerResponseAssemblyMilliseconds < 0.0) return finish("malformed_response");
        }
      reply.shots = shots;
      reply.exponent = exponent;
    } catch (...) { return finish("malformed_response"); }
    reply.responseParsingValidationMilliseconds = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - parsingStarted).count();
    if (!std::isfinite(reply.statePreparationMilliseconds) || reply.statePreparationMilliseconds < 0.0 ||
        !std::isfinite(reply.samplingMilliseconds) || reply.samplingMilliseconds < 0.0 ||
        !std::isfinite(reply.requestSerializationMilliseconds) || reply.requestSerializationMilliseconds < 0.0 ||
        !std::isfinite(reply.transportWaitMilliseconds) || reply.transportWaitMilliseconds < 0.0 ||
        !std::isfinite(reply.responseParsingValidationMilliseconds) || reply.responseParsingValidationMilliseconds < 0.0 ||
        !std::isfinite(reply.workerRequestParsingValidationMilliseconds) || reply.workerRequestParsingValidationMilliseconds < 0.0 ||
        !std::isfinite(reply.workerResponseAssemblyMilliseconds) || reply.workerResponseAssemblyMilliseconds < 0.0) return finish("malformed_response");
    reply.valid = true;
    return finish({});
  }
};

} // namespace eicrecon
