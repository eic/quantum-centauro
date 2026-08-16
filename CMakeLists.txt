cmake_minimum_required(VERSION 3.24)

project(quantum_centauro LANGUAGES CXX)

include(CTest)

find_package(EICrecon CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)

add_library(quantum_centauro SHARED
  plugin/quantum_centauro/quantum_centauro.cc
  plugin/quantum_centauro/src/DirectCentauroJetReconstruction.cc
)
set_target_properties(quantum_centauro PROPERTIES
  CXX_STANDARD 20
  CXX_STANDARD_REQUIRED YES
  CXX_EXTENSIONS NO
  PREFIX ""
  SUFFIX ".so"
)
target_include_directories(quantum_centauro
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/plugin/quantum_centauro/include
)
target_link_libraries(quantum_centauro
  PRIVATE
    EICrecon::algorithms_reco_library
    nlohmann_json::nlohmann_json
)

install(TARGETS quantum_centauro LIBRARY DESTINATION plugins)

if(BUILD_TESTING)
  add_subdirectory(tests/cpp)
endif()
