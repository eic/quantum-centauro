// SPDX-License-Identifier: LGPL-3.0-or-later

#include <edm4eic/EDM4eicVersion.h>
#include <edm4eic/ReconstructedParticleCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <edm4hep/Vector3f.h>

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "algorithms/reco/DirectCentauroJetReconstruction.h"
#include "algorithms/reco/DirectCentauroJetReconstructionConfig.h"

namespace {

using Algorithm = eicrecon::DirectCentauroJetReconstruction<edm4eic::ReconstructedParticle>;

struct Particle {
  std::size_t sourceIndex;
  float px;
  float py;
  float pz;
  float energy;
};

std::string jsonArray(const std::vector<std::size_t>& values) {
  std::string result = "[";
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0U) result += ',';
    result += std::to_string(values[index]);
  }
  return result + ']';
}

} // namespace

int main(int argc, char* argv[]) {
  if (argc != 7) {
    std::cerr << "usage: direct_centauro_quantum_harness INPUT EVENT_ID MODE SOCKET TRACE RESULT\n";
    return 2;
  }
  const std::string inputPath = argv[1];
  const std::string eventId = argv[2];
  const std::string mode = argv[3];
  const std::string socketPath = argv[4];
  const std::string tracePath = argv[5];
  const std::string resultPath = argv[6];

  std::ifstream input(inputPath);
  if (!input) throw std::runtime_error("could not open harness input");
  std::vector<Particle> particles;
  Particle particle{};
  while (input >> particle.sourceIndex >> particle.px >> particle.py >> particle.pz >> particle.energy) {
    particles.push_back(particle);
  }
  if (!input.eof()) throw std::runtime_error("malformed harness input");

  Algorithm algorithm("DirectCentauroQuantumHarness");
  eicrecon::DirectCentauroJetReconstructionConfig config;
  config.rJet = 1.0F;
  config.minCstPt = 0.0;
  config.maxCstPt = 100.0;
  config.minJetPt = 0.0;
  config.quantumMode = mode;
  config.quantumSocketPath = socketPath;
  config.quantumTimeoutMilliseconds = 1000U;
  config.qiskitMaxCandidates = 128U;
  config.quantumTracePath = tracePath;
  algorithm.applyConfig(config);
  algorithm.init();

  edm4hep::EventHeaderCollection headers;
  edm4eic::ReconstructedParticleCollection inputParticles;
  std::vector<std::size_t> sourceIndices;
  for (const auto& value : particles) {
    auto output = inputParticles.create();
    output.setEnergy(value.energy);
    output.setMomentum(edm4hep::Vector3f{value.px, value.py, value.pz});
    sourceIndices.push_back(value.sourceIndex);
  }
  eicrecon::DirectCentauroJetOutputCollection jets;
  algorithm.process({&headers, &inputParticles}, {&jets});

  std::ofstream result(resultPath);
  if (!result) throw std::runtime_error("could not write harness result");
  result << std::setprecision(9) << "{\"event_id\":\"" << eventId << "\",\"mode\":\""
         << mode << "\",\"jets\":[";
  for (std::size_t jetIndex = 0; jetIndex < jets.size(); ++jetIndex) {
    if (jetIndex != 0U) result << ',';
    const auto jet = jets.at(jetIndex);
    std::vector<std::size_t> leaves;
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 9, 0)
    for (std::size_t index = 0; index < jet.constituents_size(); ++index) {
      leaves.push_back(sourceIndices.at(jet.getConstituents(index).getObjectID().index));
    }
#else
    for (std::size_t index = 0; index < jet.particles_size(); ++index) {
      leaves.push_back(sourceIndices.at(jet.getParticles(index).getObjectID().index));
    }
#endif
    std::sort(leaves.begin(), leaves.end());
    const auto momentum = jet.getMomentum();
    result << "{\"leaves\":" << jsonArray(leaves) << ",\"px\":" << momentum.x
           << ",\"py\":" << momentum.y << ",\"pz\":" << momentum.z
           << ",\"energy\":" << jet.getEnergy() << '}';
  }
  result << "]}\n";
}
