set(DIRECTXTK_TAG oct2022)

vcpkg_check_linkage(ONLY_STATIC_LIBRARY)

if(VCPKG_TARGET_IS_MINGW)
    message(NOTICE "Building ${PORT} for MinGW requires the HLSL Compiler fxc.exe also be in the PATH. See https://aka.ms/windowssdk.")
endif()

vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO Microsoft/DirectXTK
    REF ${DIRECTXTK_TAG}
    SHA512 59cdfb0e7c3ca121ffc4935f93702b67b66dc000920de915b1589b74a192b015aa46e42438f6a2e68da344001ec69896691cf343eae3379cec272feeeb2ee02c
    HEAD_REF main
)

vcpkg_check_features(
    OUT_FEATURE_OPTIONS FEATURE_OPTIONS
    FEATURES
        xaudio2-9 BUILD_XAUDIO_WIN10
        xaudio2-8 BUILD_XAUDIO_WIN8
        xaudio2redist BUILD_XAUDIO_WIN7
)

# Overlay change: force BUILD_TOOLS=OFF on non-Windows hosts (Linux cross-compile
# via clang-cl+xwin). Upstream tries to build xwbtool/makespritefont, which in
# the oct2022 tag refer to sources that are missing and fail at configure time.
# Those tools are Windows-side CLI helpers that a Linux developer can't run
# anyway, so we skip them. On Windows hosts the upstream behavior is preserved.
if(VCPKG_TARGET_IS_UWP OR CMAKE_HOST_UNIX)
  set(EXTRA_OPTIONS -DBUILD_TOOLS=OFF)
else()
  set(EXTRA_OPTIONS -DBUILD_TOOLS=ON)
endif()

# Overlay change: on Linux hosts, upstream's shader compile step runs
# `CompileShaders.cmd` → `fxc.exe`, neither of which Linux provides. We don't
# actually use DirectXTK's effect/shader subsystem (CommonLibSSE only pulls in
# <SimpleMath.h> from this lib), so stub every `.inc` file that the .cpp files
# include as `static const unsigned char <SYM>[] = {0};` and switch to
# USE_PREBUILT_SHADERS. The resulting .lib has non-functional shaders at
# runtime — fine, since we never load this .dll on a real Windows machine
# (compile verification only; shipping builds still go via Windows CI).
if(CMAKE_HOST_UNIX)
  set(STUB_SHADERS_DIR "${CURRENT_BUILDTREES_DIR}/${TARGET_TRIPLET}-stub-shaders")
  file(MAKE_DIRECTORY "${STUB_SHADERS_DIR}")

  file(GLOB SRC_FILES "${SOURCE_PATH}/Src/*.cpp")
  set(SEEN_INCLUDES "")
  foreach(src_file IN LISTS SRC_FILES)
    file(READ "${src_file}" src_content)
    string(REGEX MATCHALL "#include \"[A-Za-z0-9_]+\\.inc\"" matches "${src_content}")
    foreach(m IN LISTS matches)
      string(REGEX REPLACE "#include \"([A-Za-z0-9_]+)\\.inc\"" "\\1" sym "${m}")
      list(FIND SEEN_INCLUDES "${sym}" idx)
      if(idx EQUAL -1)
        list(APPEND SEEN_INCLUDES "${sym}")
        file(WRITE "${STUB_SHADERS_DIR}/${sym}.inc"
          "static const unsigned char ${sym}[] = { 0 };\n")
      endif()
    endforeach()
  endforeach()

  list(LENGTH SEEN_INCLUDES stub_count)
  message(STATUS "[directxtk-overlay] generated ${stub_count} stub shader .inc files in ${STUB_SHADERS_DIR}")

  list(APPEND EXTRA_OPTIONS
    -DUSE_PREBUILT_SHADERS=ON
    "-DCOMPILED_SHADERS=${STUB_SHADERS_DIR}")
endif()

vcpkg_cmake_configure(
    SOURCE_PATH "${SOURCE_PATH}"
    OPTIONS ${FEATURE_OPTIONS} ${EXTRA_OPTIONS}
)

vcpkg_cmake_install()
vcpkg_cmake_config_fixup(CONFIG_PATH share/directxtk)

if((VCPKG_HOST_IS_WINDOWS) AND (VCPKG_TARGET_ARCHITECTURE MATCHES x64))
  vcpkg_download_distfile(
    MAKESPRITEFONT_EXE
    URLS "https://github.com/Microsoft/DirectXTK/releases/download/${DIRECTXTK_TAG}/MakeSpriteFont.exe"
    FILENAME "makespritefont-${DIRECTXTK_TAG}.exe"
    SHA512 1a55c1fe22f10c883fad4c263437a5d5084275f4303863afc7c1cc871221364d189963f248b7b338f002fac1ae7fda40352a7ef6c96bfdc1ffded2f53c187c32
  )

  vcpkg_download_distfile(
    XWBTOOL_EXE
    URLS "https://github.com/Microsoft/DirectXTK/releases/download/${DIRECTXTK_TAG}/XWBTool.exe"
    FILENAME "xwbtool-${DIRECTXTK_TAG}.exe"
    SHA512 ab14800b36a7e40785c0e8b88e08e3d6e0ab8f25711093633ce66e0c123cb5eef72ac7e6a3ce714ad6b6881d54c04149ba64101f768b5dd40c48f52b41514abc
  )

  file(MAKE_DIRECTORY "${CURRENT_PACKAGES_DIR}/tools/directxtk/")

  file(INSTALL
    ${MAKESPRITEFONT_EXE}
    ${XWBTOOL_EXE}
    DESTINATION "${CURRENT_PACKAGES_DIR}/tools/directxtk/")

  file(RENAME "${CURRENT_PACKAGES_DIR}/tools/directxtk/makespritefont-${DIRECTXTK_TAG}.exe" "${CURRENT_PACKAGES_DIR}/tools/directxtk/makespritefont.exe")
  file(RENAME "${CURRENT_PACKAGES_DIR}/tools/directxtk/xwbtool-${DIRECTXTK_TAG}.exe" "${CURRENT_PACKAGES_DIR}/tools/directxtk/xwbtool.exe")

elseif(NOT VCPKG_TARGET_IS_UWP AND NOT CMAKE_HOST_UNIX)

  vcpkg_copy_tools(
        TOOL_NAMES XWBTool
        SEARCH_DIR "${CURRENT_BUILDTREES_DIR}/${TARGET_TRIPLET}-rel/bin/CMake"
    )

endif()

file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/debug/include")

file(INSTALL "${SOURCE_PATH}/LICENSE" DESTINATION "${CURRENT_PACKAGES_DIR}/share/${PORT}" RENAME copyright)
