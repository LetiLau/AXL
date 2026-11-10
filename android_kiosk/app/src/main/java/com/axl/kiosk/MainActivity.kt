package com.axl.kiosk

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "AXL-MainActivity"
    }

    private lateinit var webView: WebView
    private lateinit var layoutOffline: LinearLayout
    private lateinit var tvStatus: TextView
    private lateinit var tvTargetUrl: TextView
    private lateinit var btnRetry: Button
    private lateinit var btnChangeIp: Button
    private lateinit var btnFloatingSetup: Button

    private var hasPageError = false
    private var isPageLoaded = false
    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private var connectionWatchdog: Runnable? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val recordAudioGranted = permissions[Manifest.permission.RECORD_AUDIO] ?: false
        if (recordAudioGranted) {
            Log.i(TAG, "RECORD_AUDIO permission granted by user.")
            startAudioService()
        } else {
            Toast.makeText(
                this,
                "Permesso microfono indispensabile per ascoltare i comandi vocali!",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 1. Keep screen on 24/7 for Kiosk mode
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // 2. Hide system bars (immersive sticky mode)
        setupImmersiveFullscreen()

        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        layoutOffline = findViewById(R.id.layoutOffline)
        tvStatus = findViewById(R.id.tvStatus)
        tvTargetUrl = findViewById(R.id.tvTargetUrl)
        btnRetry = findViewById(R.id.btnRetry)
        btnChangeIp = findViewById(R.id.btnChangeIp)
        btnFloatingSetup = findViewById(R.id.btnFloatingSetup)

        setupWebView()
        setupListeners()

        // 3. Check and request permissions
        checkAndRequestPermissions()

        // 4. Initial load or first-time setup prompt
        if (!KioskConfig.isConfigured(this)) {
            showOfflineScreen()
            showIpConfigDialog()
        } else {
            loadVisorUi()
        }
    }

    override fun onResume() {
        super.onResume()
        setupImmersiveFullscreen()
        // Ensure service is running if permissions are already granted
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            startAudioService()
        }
    }

    private fun setupImmersiveFullscreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val insetsController = WindowInsetsControllerCompat(window, window.decorView)
        insetsController.hide(WindowInsetsCompat.Type.systemBars())
        insetsController.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            setupImmersiveFullscreen()
        }
    }

    private fun setupWebView() {
        // Enable Chrome remote inspection over USB (chrome://inspect)
        WebView.setWebContentsDebuggingEnabled(true)

        webView.setBackgroundColor(Color.BLACK)
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        // Bypass webview cache during development so edits to CSS/JS reflect immediately on reload
        settings.cacheMode = WebSettings.LOAD_NO_CACHE
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        // Tag User-Agent so the Web UI detects it's running inside the official Android Kiosk app
        val defaultUa = settings.userAgentString ?: ""
        if (!defaultUa.contains("AXL-Kiosk")) {
            settings.userAgentString = "$defaultUa AXL-Kiosk/1.0"
        }

        // Bridge Web UI settings clicks directly to native Android Kiosk dialogs
        webView.addJavascriptInterface(object {
            @JavascriptInterface
            fun showIpConfig() {
                runOnUiThread { showIpConfigDialog() }
            }

            @JavascriptInterface
            fun reload() {
                runOnUiThread { reloadKioskWebUi() }
            }

            @JavascriptInterface
            fun exitKiosk() {
                runOnUiThread {
                    Toast.makeText(this@MainActivity, "Arresto A-X-L Kiosk e Microfono...", Toast.LENGTH_SHORT).show()
                    val stopIntent = Intent(this@MainActivity, AudioStreamService::class.java).apply {
                        action = AudioStreamService.ACTION_STOP
                    }
                    startService(stopIntent)
                    stopService(stopIntent)
                    webView.loadUrl("about:blank")
                    finishAffinity()
                }
            }

            @JavascriptInterface
            fun isKiosk(): Boolean = true

            @JavascriptInterface
            fun getKioskTelemetry(): String {
                return try {
                    KioskTelemetry.collect(this@MainActivity).toString()
                } catch (e: Exception) {
                    "{}"
                }
            }
        }, "AXLKiosk")

        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true) {
                    hasPageError = true
                    val desc = error?.description?.toString() ?: "Connessione rifiutata o timeout"
                    val code = error?.errorCode ?: -1
                    Log.e(TAG, "WebView load failed [$code]: $desc")
                    showOfflineScreen("A-X-L NON RAGGIUNGIBILE\n[$desc ($code)]")
                }
            }

            @Deprecated("Deprecated in Java")
            override fun onReceivedError(
                view: WebView?,
                errorCode: Int,
                description: String?,
                failingUrl: String?
            ) {
                super.onReceivedError(view, errorCode, description, failingUrl)
                hasPageError = true
                Log.e(TAG, "WebView load failed legacy [$errorCode]: $description")
                showOfflineScreen("A-X-L NON RAGGIUNGIBILE\n[${description ?: "Errore"} ($errorCode)]")
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                connectionWatchdog?.let { mainHandler.removeCallbacks(it) }
                if (!hasPageError) {
                    isPageLoaded = true
                    layoutOffline.visibility = View.GONE
                    btnFloatingSetup.visibility = View.GONE
                }
            }
        }
    }

    private fun setupListeners() {
        btnRetry.setOnClickListener {
            loadVisorUi()
            reconnectAudioService()
        }

        btnChangeIp.setOnClickListener {
            showIpConfigDialog()
        }

        btnFloatingSetup.setOnClickListener {
            showIpConfigDialog()
        }

        // Long press anywhere on the root container opens the IP config dialog
        findViewById<View>(R.id.main_container).setOnLongClickListener {
            showIpConfigDialog()
            true
        }
    }

    private fun reloadKioskWebUi() {
        Toast.makeText(this, "Ricarica Visore in corso...", Toast.LENGTH_SHORT).show()
        hasPageError = false
        webView.clearCache(true)
        webView.reload()
    }

    private fun loadVisorUi() {
        hasPageError = false
        isPageLoaded = false
        val uiUrl = KioskConfig.getUiUrl(this)
        tvTargetUrl.text = uiUrl

        // Watchdog: if server does not respond within 4 seconds, show offline/IP setup screen
        connectionWatchdog?.let { mainHandler.removeCallbacks(it) }
        val watchdog = Runnable {
            if (!isPageLoaded && !isFinishing) {
                Log.w(TAG, "Web UI load timed out, displaying offline setup overlay.")
                showOfflineScreen()
            }
        }
        connectionWatchdog = watchdog
        mainHandler.postDelayed(watchdog, 4000)

        webView.loadUrl(uiUrl)
    }

    private fun showOfflineScreen(statusMessage: String? = null) {
        connectionWatchdog?.let { mainHandler.removeCallbacks(it) }
        tvTargetUrl.text = KioskConfig.getUiUrl(this)
        tvStatus.text = statusMessage ?: "A-X-L MASTER NON RAGGIUNGIBILE"
        layoutOffline.visibility = View.VISIBLE
        btnFloatingSetup.visibility = View.VISIBLE
    }

    private fun showIpConfigDialog() {
        val currentHost = KioskConfig.getHost(this)
        val currentPort = KioskConfig.getPort(this)

        val inputLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(50, 20, 50, 10)
        }

        val etHost = EditText(this).apply {
            hint = "Indirizzo IP Master (es. 192.168.1.100)"
            setText(currentHost)
        }
        val etPort = EditText(this).apply {
            hint = "Porta (default: 8000)"
            setText(currentPort.toString())
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }

        inputLayout.addView(etHost)
        inputLayout.addView(etPort)

        AlertDialog.Builder(this)
            .setTitle("Configura Master Server")
            .setMessage("Inserisci l'indirizzo IP del PC su cui gira il Master di A-X-L:")
            .setView(inputLayout)
            .setPositiveButton("Salva e Connetti") { _, _ ->
                val newHost = etHost.text.toString().trim()
                val newPort = etPort.text.toString().toIntOrNull() ?: 8000
                if (newHost.isNotBlank()) {
                    KioskConfig.saveServerAddress(this, newHost, newPort)
                    Toast.makeText(this, "IP impostato: $newHost:$newPort", Toast.LENGTH_SHORT).show()
                    loadVisorUi()
                    reconnectAudioService()
                }
            }
            .setNegativeButton("Annulla", null)
            .show()
    }

    private fun checkAndRequestPermissions() {
        val permissionsToRequest = mutableListOf<String>()

        if (ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.RECORD_AUDIO
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            permissionsToRequest.add(Manifest.permission.RECORD_AUDIO)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                permissionsToRequest.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        // Android 17+ Local Network Access Permission
        val localNetPerm = "android.permission.ACCESS_LOCAL_NETWORK"
        try {
            if (ContextCompat.checkSelfPermission(this, localNetPerm) != PackageManager.PERMISSION_GRANTED) {
                permissionsToRequest.add(localNetPerm)
            }
        } catch (_: Exception) {}

        if (permissionsToRequest.isNotEmpty()) {
            permissionLauncher.launch(permissionsToRequest.toTypedArray())
        } else {
            startAudioService()
        }
    }

    private fun startAudioService() {
        val serviceIntent = Intent(this, AudioStreamService::class.java).apply {
            action = AudioStreamService.ACTION_START
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent)
        } else {
            startService(serviceIntent)
        }
    }

    private fun reconnectAudioService() {
        val reconnectIntent = Intent(this, AudioStreamService::class.java).apply {
            action = AudioStreamService.ACTION_RECONNECT
        }
        startService(reconnectIntent)
        // Also call start just in case it wasn't running
        startAudioService()
    }

    override fun onDestroy() {
        super.onDestroy()
        connectionWatchdog?.let { mainHandler.removeCallbacks(it) }
        webView.destroy()
    }
}
