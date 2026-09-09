package com.axl.core

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.axl.core.audio.AudioCaptureManager
import com.axl.core.utils.AssetHelper

class MainActivity : AppCompatActivity() {
    private val TAG = "AXL_MAIN"
    private val audioManager = AudioCaptureManager()

    // Asynchronous callback for runtime permissions
    private val requestMicPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            Log.i(TAG, "Microphone permission GRANTED. Engaging audio pipeline.")
            audioManager.start()
        } else {
            Log.e(TAG, "Microphone permission DENIED. A-X-L is deaf.")
            // In a production Kiosk environment, we would lock the UI and demand permissions.
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // TODO: Initialize WebView for the UI
        
        Log.i(TAG, "System bootstrap initiated...")

        // 1. Extract TFLite model to POSIX filesystem
        val modelFilename = "dummy_model.tflite"
        val absoluteModelPath = try {
            AssetHelper.extractAssetToInternalStorage(this, modelFilename)
        } catch (e: Exception) {
            Log.e(TAG, "Boot sequence aborted due to missing assets.")
            return
        }

        // 2. Ignite the C++ Core
        NativeBridge.initEngine(absoluteModelPath)

        // 3. Verify hardware permissions and start listening
        checkHardwarePermissions()
    }

    private fun checkHardwarePermissions() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) 
            == PackageManager.PERMISSION_GRANTED) {
            Log.i(TAG, "Hardware permissions verified. Engaging audio pipeline.")
            audioManager.start()
        } else {
            Log.w(TAG, "Requesting hardware permissions...")
            requestMicPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.i(TAG, "System shutdown initiated...")
        audioManager.stop()
        // TODO: Add NativeBridge.shutdown() to elegantly kill POSIX threads
    }
}