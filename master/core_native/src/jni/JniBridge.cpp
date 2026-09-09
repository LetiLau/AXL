#include <jni.h>
#include <string>
#include <android/log.h>
#include "axl/AxlCore.hpp"

// Android Logcat macros for clinical debugging via adb
#define LOG_TAG "AXL_NATIVE"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

extern "C" {

/**
 * @brief Bootstraps the C++ native engine.
 * @param env JNI environment pointer
 * @param thiz Reference to the calling Kotlin object
 * @param model_path Absolute path to the TFLite model on the Android filesystem
 */
JNIEXPORT void JNICALL
Java_com_axl_core_NativeBridge_initEngine(JNIEnv* env, jobject /* thiz */, jstring model_path) {
    if (model_path == nullptr) {
        LOGE("Boot failed: Model path is null.");
        return;
    }

    // Convert Java string to C++ std::string
    const char* path_chars = env->GetStringUTFChars(model_path, nullptr);
    std::string path(path_chars);
    env->ReleaseStringUTFChars(model_path, path_chars);

    LOGI("Booting A-X-L Core Engine from JNI...");
    axl::AxlCore::getInstance().init(path);
}

/**
 * @brief High-frequency audio ingestion endpoint.
 * Called dozens of times per second by Kotlin's AudioRecord thread.
 */
JNIEXPORT void JNICALL
Java_com_axl_core_NativeBridge_pushAudioChunk(JNIEnv* env, jobject /* thiz */, jfloatArray audio_data, jint size) {
    if (audio_data == nullptr || size <= 0) return;

    // Zero-copy attempt: GetFloatArrayElements might copy the array depending on the JVM garbage collector state,
    // but passing JNI_ABORT upon release guarantees we don't waste CPU cycles copying it back to Java space.
    jfloat* c_array = env->GetFloatArrayElements(audio_data, nullptr);
    if (c_array != nullptr) {
        axl::AxlCore::getInstance().pushAudioChunk(c_array, static_cast<std::size_t>(size));
        env->ReleaseFloatArrayElements(audio_data, c_array, JNI_ABORT);
    }
}

/**
 * @brief Routes commands from the Android OS or Web UI to the C++ Event Bus.
 */
JNIEXPORT void JNICALL
Java_com_axl_core_NativeBridge_enqueueCommand(JNIEnv* env, jobject /* thiz */, jint action_id, jstring payload) {
    std::string c_payload = "";
    if (payload != nullptr) {
        const char* payload_chars = env->GetStringUTFChars(payload, nullptr);
        c_payload = std::string(payload_chars);
        env->ReleaseStringUTFChars(payload, payload_chars);
    }
    
    axl::AxlCore::getInstance().enqueueCommand(static_cast<uint32_t>(action_id), c_payload);
}

}//extern "C"