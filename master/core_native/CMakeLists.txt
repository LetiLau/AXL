cmake_minimum_required(VERSION 3.22.1)
project(AXL_Core CXX)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF) #NO GNU extensions (massima portabilità)

#shared resources 
set(CORE_SOURCES
    #list of all cpp files
    src/core/EventDispatcher.cpp
    src/core/RingBuffer.cpp
    src/core/MFCCExtractor.cpp
    src/WakeWordEngine.cpp
)


# DEPENDENCIES: FetchContent
include(FetchContent)

message(STATUS "[A-X-L] Fetching KissFFT...")
# Force KissFFT to use 32-bit floats (standard for audio DSP and neural nets)
set(KISSFFT_DATATYPE float CACHE STRING "Use float" FORCE)
# Disable tools and tests to save compilation time
set(KISSFFT_TOOLS OFF CACHE BOOL "" FORCE)
set(KISSFFT_TEST OFF CACHE BOOL "" FORCE)
set(KISSFFT_STATIC ON CACHE BOOL "" FORCE)

FetchContent_Declare(
    kissfft
    GIT_REPOSITORY https://github.com/mborgerding/kissfft.git
    GIT_TAG        131.1.0 # Stable production release
)
FetchContent_MakeAvailable(kissfft)

# Aggiorniamo la variabile CORE_SOURCES per assicurarci che non ci siano problemi
file(GLOB_RECURSE CORE_SOURCES "src/core/*.cpp")



# ... [Impostazioni iniziali e FetchContent di KissFFT] ...

# Raccogliamo tutto il core indipendente dall'OS
file(GLOB_RECURSE CORE_SOURCES "src/core/*.cpp")

if(ANDROID)
    message(STATUS "[A-X-L] Cross-compiling Core for Android NDK (arm64-v8a)")
    
    # Aggiungiamo esplicitamente il file del bridge JNI
    set(JNI_SOURCES "src/jni/JniBridge.cpp")
    
    # Creiamo la libreria condivisa .so che la JVM caricherà
    add_library(axl_native SHARED ${CORE_SOURCES} ${JNI_SOURCES})
    
    # Cerchiamo la libreria di logging nativa di Android (per android/log.h)
    find_library(log-lib log)
    
    # Linkiamo tutto: il logger Android e il motore matematico KissFFT
    target_link_libraries(axl_native ${log-lib} kissfft)
    target_include_directories(axl_native PRIVATE ${kissfft_SOURCE_DIR})

    # A-X-L FIX: Necessario affinché JniBridge.cpp trovi <axl/AxlCore.hpp>
    target_include_directories(axl_native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

else()
    message(STATUS "[A-X-L] Compiling Standalone Core for Fedora Host")
    
    # Il tuo target di test esistente
    add_executable(axl_test_bus tests/main_test.cpp ${CORE_SOURCES})
    
    find_package(Threads REQUIRED)
    target_link_libraries(axl_test_bus Threads::Threads kissfft)
    target_include_directories(axl_test_bus PRIVATE ${kissfft_SOURCE_DIR})
endif()