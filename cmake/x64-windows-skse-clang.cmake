# Linux-host cross-compile triplet: same port linkage policy as x64-windows-skse,
# but chainloads a clang-cl + lld-link + xwin toolchain so vcpkg builds all deps
# for Windows x64 from Linux (主力機 Manjaro compile-verify path).

set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE static)

if (${PORT} MATCHES "fully-dynamic-game-engine|skse|qt*")
    set(VCPKG_LIBRARY_LINKAGE dynamic)
else ()
    set(VCPKG_LIBRARY_LINKAGE static)
endif ()

set(VCPKG_CMAKE_SYSTEM_NAME Windows)

# xwin's default splat doesn't include Debug CRT libs (libcmtd.lib). Restricting
# ports to Release-only sidesteps this for a pure compile-verification flow;
# the Linux host never loads these DLLs so single-config is fine.
set(VCPKG_BUILD_TYPE release)

set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/toolchain-clang-cl-xwin.cmake")
