# EICrecon Integration

For sibling EICrecon revision `fcea66d38d21bf91cd510af3400f76dd8891a8a7`, copy the additions below, apply the two tracked diffs exactly, then build with the recipient's normal EICrecon environment. Acquire the prerequisite from the [official EICrecon repository](https://github.com/eic/EICrecon); the recipient owns the build, environment, and fixture acquisition. This guide is the canonical integration narrative.

## Copy Additions

From this repository root:

```bash
cp -a integration/eicrecon/additions/. ../EICrecon/
```

| Addition | Why it exists |
| --- | --- |
| `src/algorithms/reco/DirectCentauroJetMinimumSelector.h` | Classical deterministic minimum selection. |
| `src/algorithms/reco/DirectCentauroJetReconstruction.cc` | DirectCentauro candidate loop, mutation, and output production. |
| `src/algorithms/reco/DirectCentauroJetReconstruction.h` | Algorithm interface and working state. |
| `src/algorithms/reco/DirectCentauroJetReconstructionConfig.h` | DirectCentauro and quantum-mode configuration. |
| `src/algorithms/reco/DirectCentauroQuantumSocketClient.h` | AF_UNIX JSONL client and exact-wire digest validation. |
| `src/factories/reco/DirectCentauroJetReconstruction_factory.h` | JANA/PODIO factory binding. |
| `src/tests/algorithms_test/direct_centauro_quantum_harness.cc` | Socket fixture driver. |
| `src/tests/algorithms_test/direct_centauro_quantum_modes_test.cc` | Quantum mode and digest behavior test executable. |
| `src/tests/algorithms_test/reco_DirectCentauroJetReconstruction.cc` | Catch2 reconstruction coverage. |

All nine additions retain `LGPL-3.0-or-later` SPDX identifiers. The public adaptations set the cap to 128 and ensure exact-payload digest validation and coverage.

## Tracked Changes

Apply these only after confirming the base revision above.

```diff
diff --git a/src/global/reco/reco.cc b/src/global/reco/reco.cc
index ac860f18..44ba8a29 100644
--- a/src/global/reco/reco.cc
+++ b/src/global/reco/reco.cc
@@ -33,6 +33,7 @@
 #include "factories/reco/InclusiveKinematicsReconstructed_factory.h"
 #include "factories/reco/InclusiveKinematicsTruth_factory.h"
 #include "factories/reco/JetReconstruction_factory.h"
+#include "factories/reco/DirectCentauroJetReconstruction_factory.h"
 #include "factories/reco/LambdaReconstruction_factory.h"
 #include "factories/reco/MC2ReconstructedParticle_factory.h"
@@ -280,6 +281,27 @@ void InitPlugin(JApplication* app) {
        {"ReconstructedCentauroJets"},
        {.rJet = 0.8, .jetAlgo = "plugin_algorithm", .jetContribAlgo = "Centauro"}, app));

+  // Experimental direct Centauro implementation. It runs beside the FastJet reference and
+  // writes distinct output collections. Jet area is not calculated in this prototype.
+  app->Add(
+      new JOmniFactoryGeneratorT<
+          DirectCentauroJetReconstruction_factory<edm4eic::ReconstructedParticle>>(
+          "GeneratedDirectCentauroJets",
+          {"EventHeader", "GeneratedBreitFrameParticles"},
+          {"GeneratedDirectCentauroJets"},
+          {.rJet = 0.8},
+          app));
+
+  app->Add(
+      new JOmniFactoryGeneratorT<
+          DirectCentauroJetReconstruction_factory<edm4eic::ReconstructedParticle>>(
+          "ReconstructedDirectCentauroJets",
+          {"EventHeader", "ReconstructedBreitFrameParticles"},
+          {"ReconstructedDirectCentauroJets"},
+          {.rJet = 0.8},
+          app));
+
    //Full correction for MCParticles --> MCParticlesHeadOnFrame
    app->Add(new JOmniFactoryGeneratorT<UndoAfterBurnerMCParticles_factory>(
        "MCParticlesHeadOnFrameNoBeamFX", {"MCParticles"}, {"MCParticlesHeadOnFrameNoBeamFX"},
@@ -299,4 +321,4 @@ void InitPlugin(JApplication* app) {
       "SecondaryVerticesHelix", {"PrimaryVertices", "ReconstructedParticles"},
       {"SecondaryVerticesHelix"}, {}, app));
 }
-} // extern "C"
+} // extern "C"
\ No newline at end of file
```

```diff
diff --git a/src/tests/algorithms_test/CMakeLists.txt b/src/tests/algorithms_test/CMakeLists.txt
index e6fcaba3..8b3a8179 100644
--- a/src/tests/algorithms_test/CMakeLists.txt
+++ b/src/tests/algorithms_test/CMakeLists.txt
@@ -22,6 +22,7 @@ add_executable(
    particle_flow_TrackProtoClusterMatchPromoter.cc
    pid_MergeParticleID.cc
    pid_lut_PIDLookup.cc
+  reco_DirectCentauroJetReconstruction.cc
    reco_ClustersToParticles.cc)
@@ -47,4 +48,10 @@ target_link_libraries(
  install(TARGETS ${TEST_NAME} DESTINATION bin)
  add_test(NAME t_${TEST_NAME} COMMAND env LLVM_PROFILE_FILE=${TEST_NAME}.profraw
                                     $<TARGET_FILE:${TEST_NAME}>)
+add_executable(direct_centauro_quantum_harness direct_centauro_quantum_harness.cc)
+target_link_libraries(direct_centauro_quantum_harness PRIVATE algorithms_reco_library podio::podio)
+add_executable(direct_centauro_quantum_modes_test direct_centauro_quantum_modes_test.cc)
+target_link_libraries(direct_centauro_quantum_modes_test PRIVATE algorithms_reco_library podio::podio)
```

The `reco.cc` change registers generated and reconstructed DirectCentauro factories. The CMake change compiles the reconstruction test, harness, and mode test. CTest registration of `direct_centauro_quantum_modes_test` is a recommended optional extension, not one of these two tracked modifications. The harness is a fixture driver, not a CTest registration.

## Runtime Contract

Assume a compatible EICrecon build and external inputs already exist. The wrappers require `RUN_DIR`, `INPUT_DIR`, `INPUT_BASENAME`, `EICRECON_BIN`, and `EICRECON_TIMEOUT_MILLISECONDS`; `run-worker` also requires `PYTHON_BIN`. Start `bash scripts/run-worker` in terminal 1 and leave it blocking. After its socket is ready, run `bash scripts/run-shadow` or `bash scripts/run-active` in terminal 2 with the same `RUN_DIR`.

The wrappers fix: cap `128`, shots `512`, exponent `3.0`, seed `314159`, one EICrecon thread, strict parameters, fresh output paths, and these output collections:

```text
EventHeader,ReconstructedBreitFrameParticles,ReconstructedDirectCentauroJets
```

`qiskit_shadow` records a local-Aer response but applies the classical selection. `qiskit_active` applies only a valid candidate index whose `request_sha256` equals SHA-256 of the exact UTF-8 request JSON bytes, excluding the JSONL LF. The bare EICrecon configuration defaults `quantumFailClosed=false` and may classically fall back; `run-active` forces `true`, while `run-shadow` defaults to `true` with only an exploratory `QUANTUM_FAIL_CLOSED=false` override. Oversize, invalid replies, digest mismatches, timeouts, and unavailable service follow this fail-closed configuration. An exact-zero pair distance is applied locally by C++ without a worker request or digest. C++ remains the owner of event state, distances, clustering mutation, and EDM output.
