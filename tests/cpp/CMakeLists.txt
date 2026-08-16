find_package(Catch2 3 CONFIG REQUIRED)

add_executable(quantum_centauro_tests reco_DirectCentauroJetReconstruction.cc)
target_compile_features(quantum_centauro_tests PRIVATE cxx_std_20)
target_include_directories(quantum_centauro_tests PRIVATE
  ${PROJECT_SOURCE_DIR}/plugin/quantum_centauro/include
)
target_link_libraries(quantum_centauro_tests PRIVATE
  quantum_centauro
  Catch2::Catch2WithMain
  EICrecon::algorithms_reco_library
  nlohmann_json::nlohmann_json
)

include(Catch)
catch_discover_tests(quantum_centauro_tests)
