# user/user_sources.cmake — auto-include all user source files
#
# Automatically injected at the end of the CubeMX-generated CMakeLists.txt
# by `make configure`.  Drop .c/.h files into user/Src/ and user/Inc/ —
# they are picked up on the next build without editing CMakeLists.txt.
#
# CONFIGURE_DEPENDS: Ninja re-runs cmake configure when files are
# added or removed, so new sources are compiled automatically.

file(GLOB_RECURSE USER_SOURCES CONFIGURE_DEPENDS
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Src/*.c
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Src/*.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Src/*.s
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Src/*.S
)

file(GLOB_RECURSE USER_HEADERS CONFIGURE_DEPENDS
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Inc/*.h
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Inc/*.hpp
)

if(USER_SOURCES OR USER_HEADERS)
    target_sources(${CMAKE_PROJECT_NAME} PRIVATE ${USER_SOURCES} ${USER_HEADERS})
endif()

target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/user/Inc
)
