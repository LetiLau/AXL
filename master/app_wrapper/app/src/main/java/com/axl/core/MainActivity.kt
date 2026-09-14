package com.axl.core

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.axl.core.audio.AudioCaptureManager
import com.axl.core.utils.AssetHelper
import java.io.File

class MainActivity : ComponentActivity() {
    private val TAG = "AXL_MAIN"
    private val audioManager = AudioCaptureManager()

    private val requestMicPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            Log.i(TAG, "[KOTLIN] Mic permission GRANTED. Starting audio capture.")
            audioManager.start()
        } else {
            Log.e(TAG, "[KOTLIN] Mic permission DENIED.")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        Log.i(TAG, "[KOTLIN] =========== SYSTEM BOOT STRAP ===========")

        try {
            // 1. Estrazione del file fittizio TFLite
            val modelFilename = "dummy_model.tflite"
            val absoluteModelPath = AssetHelper.extractAssetToInternalStorage(this, modelFilename)
            
            // Verifica che il file esista fisicamente
            if(File(absoluteModelPath).exists()) {
                Log.i(TAG, "[KOTLIN] Model extracted successfully at: $absoluteModelPath")
            }

            // 2. Avvio del Core Nativo C++
            Log.i(TAG, "[KOTLIN] Calling NativeBridge.initCore()...")
            val success = NativeBridge.initCore(absoluteModelPath)
            
            if (success) {
                Log.i(TAG, "[KOTLIN] Native Core successfully initialized.")
                checkHardwarePermissions()
            } else {
                Log.e(TAG, "[KOTLIN] Native Core initialization FAILED.")
            }

        } catch (e: Exception) {
            Log.e(TAG, "[KOTLIN] Critical error during boot", e)
        }
    }

    private fun checkHardwarePermissions() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) 
            == PackageManager.PERMISSION_GRANTED) {
            Log.i(TAG, "[KOTLIN] Permissions verified. Engaging audio.")
            audioManager.start()
        } else {
            Log.w(TAG, "[KOTLIN] Requesting mic permissions...")
            requestMicPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        audioManager.stop()
        NativeBridge.shutdown()
        Log.i(TAG, "[KOTLIN] System shutdown.")
    }
}