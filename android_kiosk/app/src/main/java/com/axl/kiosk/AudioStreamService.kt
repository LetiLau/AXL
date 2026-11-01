package com.axl.kiosk

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class AudioStreamService : Service() {

    companion object {
        private const val TAG = "AXL-AudioStreamService"
        private const val NOTIFICATION_CHANNEL_ID = "axl_audio_channel"
        private const val NOTIFICATION_ID = 1001

        const val SAMPLE_RATE = 16000
        const val CHUNK_SIZE_BYTES = 640 // 20ms chunk at 16kHz 16-bit Mono (320 samples)

        const val ACTION_START = "com.axl.kiosk.START_STREAM"
        const val ACTION_STOP = "com.axl.kiosk.STOP_STREAM"
        const val ACTION_RECONNECT = "com.axl.kiosk.RECONNECT"
    }

    private var streamJob: Job? = null
    private var streamScope: CoroutineScope? = null

    private var wakeLock: PowerManager.WakeLock? = null
    private val isRunning = AtomicBoolean(false)

    // Audio In / Out
    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null

    // OkHttp & Sockets
    private val okHttpClient = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    @Volatile
    private var audioWebSocket: WebSocket? = null
    @Volatile
    private var controlWebSocket: WebSocket? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        acquireWakeLock()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: ACTION_START
        Log.i(TAG, "onStartCommand received action: $action")

        when (action) {
            ACTION_STOP -> {
                stopStreaming()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_RECONNECT -> {
                Log.i(TAG, "Reconnecting sockets with updated server configuration...")
                // Force close existing sockets so the reconnect loop reconnects with new URL
                audioWebSocket?.close(1000, "Reconnecting with new address")
                controlWebSocket?.close(1000, "Reconnecting with new address")
                audioWebSocket = null
                controlWebSocket = null
                return START_STICKY
            }
            ACTION_START -> {
                startForegroundNotification()
                if (!isRunning.get()) {
                    isRunning.set(true)
                    initAudioHardware()
                    startPipelines()
                }
            }
        }

        return START_STICKY
    }

    private fun startForegroundNotification() {
        val notification = buildForegroundNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val serviceType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
            } else {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            }
            startForeground(NOTIFICATION_ID, notification, serviceType)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "A-X-L Kiosk Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Streaming audio bidirezionale per assistente A-X-L"
                setShowBadge(false)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
        }
    }

    private fun buildForegroundNotification(): Notification {
        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("A-X-L Kiosk Online")
            .setContentText("Microfono e Speaker sincronizzati con il Master")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .build()
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(Context.POWER_SERVICE) as? PowerManager
        wakeLock = powerManager?.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "AXL::KioskAudioStreamWakeLock"
        )
        wakeLock?.acquire(24 * 60 * 60 * 1000L) // 24 hours
    }

    private fun initAudioHardware() {
        // 1. AudioTrack Setup (Speaker Output)
        val minTrackBuf = AudioTrack.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val trackBufSize = maxOf(minTrackBuf, CHUNK_SIZE_BYTES * 8)

        val audioAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()

        val audioFormatOut = AudioFormat.Builder()
            .setSampleRate(SAMPLE_RATE)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()

        try {
            audioTrack = AudioTrack(
                audioAttributes,
                audioFormatOut,
                trackBufSize,
                AudioTrack.MODE_STREAM,
                AudioManager.AUDIO_SESSION_ID_GENERATE
            )
            audioTrack?.play()
            Log.i(TAG, "AudioTrack initialized successfully and in PLAYING state.")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize AudioTrack: ${e.message}", e)
        }

        // 2. AudioRecord Setup (Microphone Input)
        val minRecordBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val recordBufSize = maxOf(minRecordBuf, CHUNK_SIZE_BYTES * 8)

        try {
            // First attempt: VOICE_RECOGNITION (prefers clean beamformed speech without echo)
            var record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                recordBufSize
            )
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                Log.w(TAG, "VOICE_RECOGNITION not initialized, falling back to MIC source.")
                record.release()
                record = AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    recordBufSize
                )
            }

            if (record.state == AudioRecord.STATE_INITIALIZED) {
                record.startRecording()
                audioRecord = record
                Log.i(TAG, "AudioRecord initialized and recording successfully.")
            } else {
                Log.e(TAG, "AudioRecord failed to initialize in any audio source mode.")
            }
        } catch (e: SecurityException) {
            Log.e(TAG, "Missing RECORD_AUDIO permission: ${e.message}")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start AudioRecord: ${e.message}", e)
        }
    }

    private fun startPipelines() {
        streamJob?.cancel()
        val job = SupervisorJob()
        streamJob = job
        val scope = CoroutineScope(Dispatchers.IO + job)
        streamScope = scope

        // Pipeline 1: Binary Audio WebSocket (Send mic PCM, receive Piper TTS PCM)
        scope.launch {
            runAudioWebSocketLoop()
        }

        // Pipeline 2: JSON Control WebSocket (Telemetry & Intents)
        scope.launch {
            runControlWebSocketLoop()
        }

        // Pipeline 3: Continuous Mic Recording & Streaming Loop
        scope.launch {
            recordAndStreamMic()
        }

        // Pipeline 4: Periodic Kiosk Telemetry Broadcast (every 5 seconds)
        scope.launch {
            while (isActive && isRunning.get()) {
                sendKioskTelemetry()
                delay(5000)
            }
        }
    }

    private suspend fun runAudioWebSocketLoop() {
        while (streamScope?.isActive == true && isRunning.get()) {
            val url = KioskConfig.getAudioWsUrl(this@AudioStreamService)
            Log.i(TAG, "Connecting Audio WS to: $url")

            val closedSignal = CompletableDeferred<Unit>()
            val request = Request.Builder().url(url).build()

            val ws = okHttpClient.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    Log.i(TAG, ">>> [AUDIO WS CONNECTED] Bidirectional audio link established with Master! <<<")
                    audioWebSocket = webSocket
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    // Outbound audio from Master (Piper TTS stream) -> Play through Phone Speaker
                    val raw = bytes.toByteArray()
                    if (raw.isNotEmpty() && audioTrack?.playState == AudioTrack.PLAYSTATE_PLAYING) {
                        audioTrack?.write(raw, 0, raw.size)
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    Log.w(TAG, "Audio WS failure: ${t.message}. Will retry in 3s...")
                    audioWebSocket = null
                    if (!closedSignal.isCompleted) closedSignal.complete(Unit)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    Log.i(TAG, "Audio WS closed ($code: $reason). Will retry...")
                    audioWebSocket = null
                    if (!closedSignal.isCompleted) closedSignal.complete(Unit)
                }
            })

            // Wait until this connection closes before attempting a new one!
            closedSignal.await()
            audioWebSocket = null
            delay(3000)
        }
    }

    private suspend fun runControlWebSocketLoop() {
        while (streamScope?.isActive == true && isRunning.get()) {
            val url = KioskConfig.getControlWsUrl(this@AudioStreamService)
            Log.i(TAG, "Connecting Control WS to: $url")

            val closedSignal = CompletableDeferred<Unit>()
            val request = Request.Builder().url(url).build()

            val ws = okHttpClient.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    Log.i(TAG, ">>> [CONTROL WS CONNECTED] Kiosk telemetry and intent link established! <<<")
                    controlWebSocket = webSocket
                    sendKioskTelemetry()
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    try {
                        val json = JSONObject(text)
                        val type = json.optString("type", "")
                        if (type == "INTENT" || json.has("action")) {
                            IntentExecutor.execute(this@AudioStreamService, json)
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Error parsing incoming control message: ${e.message}")
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    Log.w(TAG, "Control WS failure: ${t.message}. Will retry in 4s...")
                    controlWebSocket = null
                    if (!closedSignal.isCompleted) closedSignal.complete(Unit)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    Log.i(TAG, "Control WS closed ($code: $reason). Will retry...")
                    controlWebSocket = null
                    if (!closedSignal.isCompleted) closedSignal.complete(Unit)
                }
            })

            closedSignal.await()
            controlWebSocket = null
            delay(4000)
        }
    }

    private fun recordAndStreamMic() {
        val buffer = ByteArray(CHUNK_SIZE_BYTES)
        Log.i(TAG, "Mic recording loop running. Buffer chunk: $CHUNK_SIZE_BYTES bytes.")
        while (streamScope?.isActive == true && isRunning.get()) {
            val recorder = audioRecord
            if (recorder != null && recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                var bytesReadTotal = 0
                while (bytesReadTotal < CHUNK_SIZE_BYTES && streamScope?.isActive == true) {
                    val read = recorder.read(
                        buffer,
                        bytesReadTotal,
                        CHUNK_SIZE_BYTES - bytesReadTotal
                    )
                    if (read > 0) {
                        bytesReadTotal += read
                    } else {
                        Log.w(TAG, "AudioRecord read returned error code: $read")
                        try {
                            Thread.sleep(10)
                        } catch (_: InterruptedException) {
                            break
                        }
                        break
                    }
                }

                if (bytesReadTotal == CHUNK_SIZE_BYTES) {
                    val ws = audioWebSocket
                    if (ws != null) {
                        ws.send(buffer.toByteString(0, CHUNK_SIZE_BYTES))
                    }
                }
            } else {
                try {
                    Thread.sleep(20)
                } catch (_: InterruptedException) {
                    break
                }
            }
        }
        Log.i(TAG, "Mic recording loop exited.")
    }

    private fun sendKioskTelemetry() {
        val ws = controlWebSocket ?: return
        try {
            val telemetryJson = KioskTelemetry.collect(this)
            ws.send(telemetryJson.toString())
        } catch (e: Exception) {
            Log.w(TAG, "Failed to send kiosk telemetry: ${e.message}")
        }
    }

    private fun stopStreaming() {
        isRunning.set(false)
        streamJob?.cancel()
        streamJob = null
        streamScope = null

        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (_: Exception) {}
        audioRecord = null

        try {
            audioTrack?.stop()
            audioTrack?.release()
        } catch (_: Exception) {}
        audioTrack = null

        try {
            audioWebSocket?.close(1000, "Service stopped")
            controlWebSocket?.close(1000, "Service stopped")
        } catch (_: Exception) {}
        audioWebSocket = null
        controlWebSocket = null

        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
        }
    }

    override fun onDestroy() {
        stopStreaming()
        super.onDestroy()
    }
}
