# Cross-compile toolchain: Linux host → Windows x64 MSVC via clang-cl + lld-link + xwin.
#
# Loaded as VCPKG_CHAINLOAD_TOOLCHAIN_FILE from cmake/x64-windows-skse-clang.cmake,
# so this applies to every port vcpkg builds for the x64-windows-skse-clang triplet
# AND to our own plugin.

set(CMAKE_SYSTEM_NAME      Windows)
set(CMAKE_SYSTEM_PROCESSOR AMD64)

set(CMAKE_C_COMPILER   clang-cl)
set(CMAKE_CXX_COMPILER clang-cl)
set(CMAKE_LINKER       lld-link)
set(CMAKE_AR           llvm-lib)
set(CMAKE_RC_COMPILER  llvm-rc)

set(CMAKE_C_COMPILER_TARGET   x86_64-pc-windows-msvc)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_LINKER_TYPE         LLD)

# Force static CRT on every compile — including CMake's compiler ABI probe.
#
# CMake pre-bakes a CRT flag (/MDd / /MD) into CMAKE_<LANG>_FLAGS_<CONFIG>_INIT
# *before* any policy can influence it. Without intervention it wins over
# CMAKE_MSVC_RUNTIME_LIBRARY injected later as a target property, so the ABI
# test executable links against msvcrtd.lib which xwin doesn't ship.
#
# Strategy:
#   1. Blank the CRT part of the *_INIT flags.
#   2. Set CMP0091 NEW so CMake doesn't re-inject /MDd during project().
#   3. Set CMAKE_MSVC_RUNTIME_LIBRARY so every target (incl. try_compile's)
#      gets /MT or /MTd via the target-property route.
# Force static CRT. CMake's Platform/Windows-MSVC.cmake normally appends /MDd
# to CMAKE_CXX_FLAGS_DEBUG_INIT unless CMP0091 is NEW — but older ports pin
# CMP0091 to OLD via `cmake_minimum_required(<3.15)` and neither
# CMAKE_POLICY_DEFAULT_CMP0091=NEW nor cmake_policy(SET) in this scope
# actually wins against that. Working solution: hard-pin the cache flags
# from the toolchain. Cache FORCE beats `_INIT` entirely — CMake only
# consults `_INIT` when the cache slot is empty.
set(CMAKE_C_FLAGS_DEBUG              "/MTd /Zi /Ob0 /Od /RTC1" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS_DEBUG            "/MTd /Zi /Ob0 /Od /RTC1" CACHE STRING "" FORCE)
set(CMAKE_C_FLAGS_RELEASE            "/MT /O2 /Ob2 /DNDEBUG"   CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS_RELEASE          "/MT /O2 /Ob2 /DNDEBUG"   CACHE STRING "" FORCE)
set(CMAKE_C_FLAGS_RELWITHDEBINFO     "/MT /Zi /O2 /Ob1 /DNDEBUG" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO   "/MT /Zi /O2 /Ob1 /DNDEBUG" CACHE STRING "" FORCE)
set(CMAKE_C_FLAGS_MINSIZEREL         "/MT /O1 /Ob1 /DNDEBUG" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS_MINSIZEREL       "/MT /O1 /Ob1 /DNDEBUG" CACHE STRING "" FORCE)

set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" CACHE STRING "" FORCE)

# CMake's compiler ABI probe (CMakeTestCXXCompiler) runs try_compile in Debug
# config by default, pulling in /MTd → libcmtd.lib. xwin doesn't ship debug
# CRT libs by default, so force the probe to Release.
set(CMAKE_TRY_COMPILE_CONFIGURATION Release)

message(STATUS "[xwin-toolchain] static CRT pinned via cache-FORCE")

# xwin splats the MSVC CRT + Windows SDK directly under --output.
# We use ~/.xwin-cache; override with XWIN_CACHE env var if placed elsewhere.
if(DEFINED ENV{XWIN_CACHE})
    set(_XWIN "$ENV{XWIN_CACHE}")
else()
    set(_XWIN "$ENV{HOME}/.xwin-cache")
endif()

if(NOT EXISTS "${_XWIN}/crt/include")
    message(FATAL_ERROR "xwin cache not found at ${_XWIN}. Run: xwin --accept-license splat --output ~/.xwin-cache")
endif()

# /imsvc marks the dir as system include, silencing warnings from MS headers.
set(_XWIN_INC
    "/imsvc${_XWIN}/crt/include"
    "/imsvc${_XWIN}/sdk/include/ucrt"
    "/imsvc${_XWIN}/sdk/include/shared"
    "/imsvc${_XWIN}/sdk/include/um"
    "/imsvc${_XWIN}/sdk/include/winrt"
)
string(JOIN " " _XWIN_INC_STR ${_XWIN_INC})

set(_XWIN_LIB
    "/libpath:\"${_XWIN}/crt/lib/x86_64\""
    "/libpath:\"${_XWIN}/sdk/lib/ucrt/x86_64\""
    "/libpath:\"${_XWIN}/sdk/lib/um/x86_64\""
)
string(JOIN " " _XWIN_LIB_STR ${_XWIN_LIB})

# Ensure the linker finds the libraries even during early CMake compiler checks
# by setting the LIB environment variable (which lld-link/MSVC linker respects).
set(ENV{LIB} "${_XWIN}/crt/lib/x86_64;${_XWIN}/sdk/lib/ucrt/x86_64;${_XWIN}/sdk/lib/um/x86_64")

# -fdelayed-template-parsing: clang-cl's MSVC-compat behavior that defers
# template body parsing until instantiation. MSVC does this by default (which
# is why CommonLibSSE-NG compiles with cl.exe); Clang 16+ turned this OFF by
# default, making unqualified name lookups inside template bases fail.
set(_MSVC_COMPAT_FLAGS "/clang:-fdelayed-template-parsing /EHsc")

set(CMAKE_C_FLAGS_INIT             "${_XWIN_INC_STR} ${_MSVC_COMPAT_FLAGS}")
set(CMAKE_CXX_FLAGS_INIT           "${_XWIN_INC_STR} ${_MSVC_COMPAT_FLAGS}")

set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${_XWIN_INC_STR} ${_MSVC_COMPAT_FLAGS}" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${_XWIN_INC_STR} ${_MSVC_COMPAT_FLAGS}" CACHE STRING "" FORCE)

set(VCPKG_LINKER_FLAGS "${VCPKG_LINKER_FLAGS} ${_XWIN_LIB_STR}")
set(CMAKE_EXE_LINKER_FLAGS_INIT    "${VCPKG_LINKER_FLAGS}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${VCPKG_LINKER_FLAGS}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${VCPKG_LINKER_FLAGS}")

set(CMAKE_EXE_LINKER_FLAGS    "${_XWIN_LIB_STR} ${CMAKE_EXE_LINKER_FLAGS}" CACHE STRING "" FORCE)
set(CMAKE_SHARED_LINKER_FLAGS "${_XWIN_LIB_STR} ${CMAKE_SHARED_LINKER_FLAGS}" CACHE STRING "" FORCE)
set(CMAKE_MODULE_LINKER_FLAGS "${_XWIN_LIB_STR} ${CMAKE_MODULE_LINKER_FLAGS}" CACHE STRING "" FORCE)
set(CMAKE_STATIC_LINKER_FLAGS_INIT "")

# Don't let CMake look at Linux host's /usr paths when cross-building for Windows.
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
