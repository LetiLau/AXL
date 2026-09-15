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
    src/core/WakeWordEngine.cpp
    src/core/AxlCore.cpp
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

# Raccogliamo tutto il core indipendente dall'OS
#file(GLOB_RECURSE CORE_SOURCES "src/core/*.cpp") #sovrascrive la lista manuale dei file --> causa danni


# --- TENSORFLOW LITE INTEGRATION ---
set(TFLITE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/tflite")

if(ANDROID)
    message(STATUS "[A-X-L] Linking prebuilt Android TFLite (arm64-v8a)")
    
    # 1. Definisce la libreria precompilata fornita dal file .so
    add_library(tflite SHARED IMPORTED)
    set_target_properties(tflite PROPERTIES
        IMPORTED_LOCATION "${TFLITE_DIR}/lib/android/arm64-v8a/libtensorflowlite_jni.so"
    )
    
    set(JNI_SOURCES "src/jni/JniBridge.cpp")
    add_library(axl_native SHARED ${CORE_SOURCES} ${JNI_SOURCES})
    
    find_library(log-lib log)
    
    # 2. Linka TFLite e gli header
    target_link_libraries(axl_native 
        ${log-lib} 
        kissfft
        tflite
    )

    target_include_directories(axl_native PUBLIC 
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${TFLITE_DIR}/include
    )
    target_include_directories(axl_native PRIVATE ${kissfft_SOURCE_DIR})

else()
    message(STATUS "[A-X-L] Linking System TFLite for Fedora Host")
    
    add_executable(axl_test_bus tests/main_test.cpp ${CORE_SOURCES})
    
    find_package(Threads REQUIRED)
    
    # 3. Su Fedora usiamo il path di sistema (dnf install tensorflow-lite-devel)
    # Se vuoi usare il .so nella cartella linux/, cambia questo blocco come per ANDROID
    find_library(TFLITE_LIB tensorflow-lite REQUIRED)
    
    target_link_libraries(axl_test_bus 
        Threads::Threads 
        kissfft
        ${TFLITE_LIB}
    )
    
    target_include_directories(axl_test_bus PUBLIC 
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${TFLITE_DIR}/include 
    )
    target_include_directories(axl_test_bus PRIVATE ${kissfft_SOURCE_DIR})
endif()