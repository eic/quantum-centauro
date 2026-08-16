// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 ePIC Collaboration

#include <JANA/JApplicationFwd.h>
#include <edm4eic/ReconstructedParticle.h>

#include "extensions/jana/JOmniFactoryGeneratorT.h"
#include "quantum_centauro/DirectCentauroJetReconstruction_factory.h"

extern "C" void InitPlugin(JApplication* app) {
  InitJANAPlugin(app);

  using namespace eicrecon;

  app->Add(
      new JOmniFactoryGeneratorT<
          DirectCentauroJetReconstruction_factory<edm4eic::ReconstructedParticle>>(
          "GeneratedDirectCentauroJets",
          {"EventHeader", "GeneratedBreitFrameParticles"},
          {"GeneratedDirectCentauroJets"},
          {.rJet = 0.8},
          app));

  app->Add(
      new JOmniFactoryGeneratorT<
          DirectCentauroJetReconstruction_factory<edm4eic::ReconstructedParticle>>(
          "ReconstructedDirectCentauroJets",
          {"EventHeader", "ReconstructedBreitFrameParticles"},
          {"ReconstructedDirectCentauroJets"},
          {.rJet = 0.8},
          app));
}
