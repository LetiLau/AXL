package com.axl.core

import android.util.Log

/**
 * Thin wrapper singleton bridging the Android JVM to the A-X-L C++ Native Core.
 * Responsibilities are strictly limited to JNI marshaling.
 */
object NativeBridge {
    private const val TAG = "AXL_KOTLIN"

    init {
        try {
            // "axl_native" deve corrispondere al target name del CMakeLists.txt
            System.loadLibrary("axl_native")
            Log.i(TAG, "Native library libaxl_native.so loaded successfully.")
        } catch (e: UnsatisfiedLinkError) {
            Log.e(TAG, "FATAL: Could not load native library. Check NDK build.", e)
            throw e
        }
    }

    /**
     * Bootstraps the C++ native engine.
     * @param modelPath Absolute path to the TFLite model on the Android filesystem.
     */
    external fun initEngine(modelPath: String)

    /**
     * High-frequency audio ingestion endpoint.
     * Must be called from a dedicated background thread (e.g., AudioRecord loop).
     */
    external fun pushAudioChunk(audioData: FloatArray, size: Int)

    /**
     * Routes commands from the Android OS or Web UI to the C++ Event Bus.
     */
    external fun enqueueCommand(actionId: Int, payload: String)
}