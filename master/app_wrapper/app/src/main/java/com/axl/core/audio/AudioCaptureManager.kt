package com.axl.core.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import com.axl.core.NativeBridge
import java.util.concurrent.atomic.AtomicBoolean

/**
 * High-performance audio ingestion loop.
 * Maintains a strict zero-allocation policy inside the capture loop.
 */
class AudioCaptureManager {
    private val TAG = "AXL_AUDIO"
    
    // Strict A-X-L DSP requirements
    private val SAMPLE_RATE = 16000
    private val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
    private val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT

    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null
    private val isRecording = AtomicBoolean(false)

    // Chunk size: 1024 samples @ 16kHz = 64ms of audio per dispatch.
    // Perfectly feeds our 25ms MFCC window requirements without stalling.
    private val CHUNK_SIZE = 1024
    private val shortBuffer = ShortArray(CHUNK_SIZE)
    // ELIMINATO: private val floatBuffer = FloatArray(CHUNK_SIZE)

    @SuppressLint("MissingPermission") // Le autorizzazioni verranno gestite dalla UI
    fun start() {
        if (isRecording.get()) return

        // Compute internal OS buffer size. We use a multiple to prevent OS-level underruns.
        val minOsBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        
        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION, // Ottimizzato per voce, bypassa AGC/NoiseReduction aggressivi
            SAMPLE_RATE,
            CHANNEL_CONFIG,
            AUDIO_FORMAT,
            minOsBufferSize * 4 
        )

        if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "FATAL: AudioRecord initialization failed. Microphone in use?")
            return
        }

        isRecording.set(true)
        audioRecord?.startRecording()

        // Eseguiamo il loop su un thread POSIX puro, niente Coroutines per garantire preemption ferrea
        captureThread = Thread { captureLoop() }.apply {
            priority = Thread.MAX_PRIORITY
            name = "AXL_AudioIngestionThread"
            start()
        }
        
        Log.i(TAG, "Audio pipeline engaged. Listening...")
    }

    private fun captureLoop() {
        while (isRecording.get()) {
            val readResult = audioRecord?.read(shortBuffer, 0, CHUNK_SIZE) ?: 0
            
            if (readResult > 0) {
                // Fire and forget into the native realm.
                // Il cast a float e l'allocazione vettoriale avvengono in C++ (JniBridge.cpp)
                NativeBridge.pushAudioChunk(shortBuffer, readResult)
                
            } else if (readResult < 0) {
                Log.e(TAG, "AudioRecord read error code: $readResult")
            }
        }
    }

    fun stop() {
        if (!isRecording.get()) return
        isRecording.set(false)
        
        captureThread?.join(500) // Aspetta che il thread muoia con grazia
        
        audioRecord?.apply {
            stop()
            release()
        }
        audioRecord = null
        
        Log.i(TAG, "Audio pipeline offline.")
    }
}