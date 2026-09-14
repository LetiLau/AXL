//cooler old

// #include <jni.h>
// #include <string>
// #include <vector>
// #include <android/log.h>
// #include "axl/AxlCore.hpp"

// // Android Logcat macros for clinical debugging via adb
// #define LOG_TAG "AXL_NATIVE"
// #define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
// #define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

// extern "C" {

// /**
//  * @brief Bootstraps the C++ native engine.
//  * @param env JNI environment pointer
//  * @param thiz Reference to the calling Kotlin object
//  * @param model_path Absolute path to the TFLite model on the Android filesystem
//  */
// JNIEXPORT void JNICALL
// Java_com_axl_core_NativeBridge_initEngine(JNIEnv* env, jobject /* thiz */, jstring model_path) {
//     if (model_path == nullptr) {
//         LOGE("Boot failed: Model path is null.");
//         return;
//     }

//     // Convert Java string to C++ std::string
//     const char* path_chars = env->GetStringUTFChars(model_path, nullptr);
//     std::string path(path_chars);
//     env->ReleaseStringUTFChars(model_path, path_chars);

//     LOGI("Booting A-X-L Core Engine from JNI...");
//     axl::AxlCore::getInstance().init(path);
// }

// /**
//  * @brief High-frequency audio ingestion endpoint.
//  * Called dozens of times per second by Kotlin's AudioRecord thread.
//  */
// JNIEXPORT void JNICALL
// Java_com_axl_core_NativeBridge_pushAudioChunk(JNIEnv* env, jobject /* thiz */, jfloatArray audio_data, jint size) {
//     if (audio_data == nullptr || size <= 0) return;

//     // Zero-copy attempt: GetFloatArrayElements might copy the array depending on the JVM garbage collector state,
//     // but passing JNI_ABORT upon release guarantees we don't waste CPU cycles copying it back to Java space.
//     jfloat* c_array = env->GetFloatArrayElements(audio_data, nullptr);
//     if (c_array != nullptr) {
//         axl::AxlCore::getInstance().pushAudioChunk(c_array, static_cast<std::size_t>(size));
//         env->ReleaseFloatArrayElements(audio_data, c_array, JNI_ABORT);
//     }
// }

// /**
//  * @brief Routes commands from the Android OS or Web UI to the C++ Event Bus.
//  */
// JNIEXPORT void JNICALL
// Java_com_axl_core_NativeBridge_enqueueCommand(JNIEnv* env, jobject /* thiz */, jint action_id, jstring payload) {
//     std::string c_payload = "";
//     if (payload != nullptr) {
//         const char* payload_chars = env->GetStringUTFChars(payload, nullptr);
//         c_payload = std::string(payload_chars);
//         env->ReleaseStringUTFChars(payload, payload_chars);
//     }
    
//     axl::AxlCore::getInstance().enqueueCommand(static_cast<uint32_t>(action_id), c_payload);
// }

// }//extern "C"


#include <jni.h>
#include <string>
#include <vector>
#include "axl/Logger.hpp" //now using the log universal header 
#include "axl/AxlCore.hpp"

#define TAG "AXL_CORE"
// #define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
// #define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

extern "C" {

JNIEXPORT jboolean JNICALL
Java_com_axl_core_NativeBridge_initCore(JNIEnv *env, jclass /* clazz */, jstring assetPath) {
    const char *path_cstr = env->GetStringUTFChars(assetPath, nullptr);
    std::string path(path_cstr);
    env->ReleaseStringUTFChars(assetPath, path_cstr);

    LOGI("[JNI] Initializing A-X-L Core with path: %s", path.c_str());
    axl::AxlCore::getInstance().init(path);
    
    return JNI_TRUE;
}

JNIEXPORT void JNICALL
Java_com_axl_core_NativeBridge_pushAudioChunk(JNIEnv *env, jclass /* clazz */, jshortArray pcmData, jint length) {
    if (length <= 0) return;

    jshort *buffer = env->GetShortArrayElements(pcmData, nullptr);
    
    // Local stack/heap allocation for DSP normalization
    std::vector<float> float_buffer(length);
    for (int i = 0; i < length; ++i) {
        // Linear normalization from int16 to float [-1.0, 1.0]
        float_buffer[i] = static_cast<float>(buffer[i]) / 32768.0f;
    }
    
    axl::AxlCore::getInstance().pushAudioChunk(float_buffer.data(), length);
    
    // JNI_ABORT: do not copy modifications back to the JVM
    env->ReleaseShortArrayElements(pcmData, buffer, JNI_ABORT);
}

JNIEXPORT void JNICALL
Java_com_axl_core_NativeBridge_shutdown(JNIEnv *env, jclass /* clazz */) {
    LOGI("[JNI] Shutting down A-X-L Core POSIX threads.");
    axl::AxlCore::getInstance().shutdown();
}

} // extern "C"