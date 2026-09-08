cmake_minimum_required(VERSION 3.22.1)
project(AXL_Core CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF) #NO GNU extensions (massima portabilità)

#shared resources 
set(CORE_SOURCES
    #list of all cpp files
    src/core/EventDispatcher.cpp
    src/core/RingBuffer.cpp
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



if(ANDROID)
    message(STATUS "[A-X-L] Target OS: Android NDK. Building JNI Shared Library.")
    
    set(JNI_SOURCES
        src/jni/axl_bridge.cpp
    )
    
    #.so library for Kotlin
    add_library(axl_native SHARED ${CORE_SOURCES} ${JNI_SOURCES})
    
    #include position (<axl/...>)
    target_include_directories(axl_native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    
    #Android essential native libraries (logcat)
    find_library(log-lib log)
    target_link_libraries(axl_native ${log-lib})

else()
    message(STATUS "[A-X-L] Target OS: Linux/Fedora. Building Test Executable.")
    
    #terminal test entry point
    set(TEST_SOURCES "tests/main_test.cpp")
    
    #executable
    add_executable(axl_test_bus ${CORE_SOURCES} ${TEST_SOURCES})
    
    # Risolve i percorsi per gli header senza usare path relativi osceni
    target_include_directories(axl_test_bus PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    
    # Link ai thread POSIX necessari per l'Event Dispatcher asincrono su Linux
    find_package(Threads REQUIRED)
    target_link_libraries(axl_test_bus Threads::Threads)
    
    #optimization
    target_compile_options(axl_test_bus PRIVATE -Wall -Wextra -Wpedantic -O3)
    
    ######ATTTENZIONE MANCA QUESTO PEZZO NELL'IF DI ANDROID ALTRIMENTI NON SI USA FFT
    # LINKING: Aggiungiamo kissfft. Il compilatore lo tratterà come codice nativo.
    target_link_libraries(axl_test_bus Threads::Threads kissfft)
    
    # Aggiungiamo l'include directory di KissFFT per poter usare <kiss_fftr.h>
    target_include_directories(axl_test_bus PRIVATE ${kissfft_SOURCE_DIR})

endif()