cmake_minimum_required(VERSION 3.21)

# Project name = the SKSE .dll file name.
project(AgentBridge VERSION 0.9.0 LANGUAGES CXX)

# Optional auto-deploy: set SKYRIM_FOLDER (loose) or SKYRIM_MODS_FOLDER (MO2/Vortex)
# and the post-build step copies the .dll into place. Left unset on offline/CI.
if(DEFINED ENV{SKYRIM_FOLDER} AND IS_DIRECTORY "$ENV{SKYRIM_FOLDER}/Data")
    set(OUTPUT_FOLDER "$ENV{SKYRIM_FOLDER}/Data")
endif()
if(DEFINED ENV{SKYRIM_MODS_FOLDER} AND IS_DIRECTORY "$ENV{SKYRIM_MODS_FOLDER}")
    set(OUTPUT_FOLDER "$ENV{SKYRIM_MODS_FOLDER}/${PROJECT_NAME} ${CMAKE_BUILD_TYPE}")
endif()

find_package(CommonLibSSE CONFIG REQUIRED)
# CommonLibSSE's exported target names Microsoft::DirectXTK in its link
# interface, so the imported target must exist before it is consumed.
find_package(directxtk CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)

include(cmake/headerlist.cmake)
include(cmake/sourcelist.cmake)
add_commonlibsse_plugin(${PROJECT_NAME} SOURCES ${headers} ${sources})

target_compile_features(${PROJECT_NAME} PRIVATE cxx_std_23)
target_compile_definitions(${PROJECT_NAME} PRIVATE
    AGENT_BRIDGE_VERSION="${PROJECT_VERSION}"
)
target_precompile_headers(${PROJECT_NAME} PRIVATE src/PCH.h)

# ws2_32: the bridge's whole point is a listening socket inside the game
# process. Plain winsock2, no HTTP library — see README "why no cpp-httplib".
target_link_libraries(${PROJECT_NAME} PRIVATE nlohmann_json::nlohmann_json ws2_32)

# When SKSE .dll is compiled, copy it (and optional config/) into the mods folder.
if(DEFINED OUTPUT_FOLDER)
    set(DLL_FOLDER "${OUTPUT_FOLDER}/SKSE/Plugins")

    message(STATUS "SKSE plugin output folder: ${DLL_FOLDER}")

    add_custom_command(
        TARGET "${PROJECT_NAME}"
        POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E make_directory "${DLL_FOLDER}"
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different "$<TARGET_FILE:${PROJECT_NAME}>" "${DLL_FOLDER}/$<TARGET_FILE_NAME:${PROJECT_NAME}>"
        VERBATIM
    )
    if(CMAKE_BUILD_TYPE STREQUAL "Debug")
        add_custom_command(
            TARGET "${PROJECT_NAME}"
            POST_BUILD
            COMMAND "${CMAKE_COMMAND}" -E copy_if_different "$<TARGET_PDB_FILE:${PROJECT_NAME}>" "${DLL_FOLDER}/$<TARGET_PDB_FILE_NAME:${PROJECT_NAME}>"
            VERBATIM
        )
    endif()
endif()
