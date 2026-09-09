package com.axl.core.utils

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

object AssetHelper {
    private const val TAG = "AXL_ASSETS"

    /**
     * Extracts a file from the APK assets to the app's internal POSIX-compliant storage.
     * Skips extraction if the file already exists to save I/O cycles on subsequent boots.
     * 
     * @return The absolute path to the extracted file, readable by standard C++ libraries.
     */
    fun extractAssetToInternalStorage(context: Context, filename: String): String {
        val outFile = File(context.filesDir, filename)
        
        if (outFile.exists()) {
            Log.i(TAG, "Asset '$filename' already exists at ${outFile.absolutePath}. Skipping extraction.")
            return outFile.absolutePath
        }

        try {
            context.assets.open(filename).use { inputStream ->
                FileOutputStream(outFile).use { outputStream ->
                    inputStream.copyTo(outputStream)
                }
            }
            Log.i(TAG, "Successfully extracted '$filename' to internal storage.")
        } catch (e: IOException) {
            Log.e(TAG, "FATAL: Failed to extract asset '$filename'.", e)
            throw RuntimeException("Asset extraction failed.", e)
        }

        return outFile.absolutePath
    }
}