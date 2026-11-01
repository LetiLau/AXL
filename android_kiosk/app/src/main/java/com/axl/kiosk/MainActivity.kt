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
    private lateinit var tvTargetUrl: TextView
    private lateinit var btnRetry: Button
    private lateinit var btnChangeIp: Button
    private lateinit var btnSettings: ImageButton

    private var hasPageError = false

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
        tvTargetUrl = findViewById(R.id.tvTargetUrl)
        btnRetry = findViewById(R.id.btnRetry)
        btnChangeIp = findViewById(R.id.btnChangeIp)
        btnSettings = findViewById(R.id.btnSettings)

        setupWebView()
        setupListeners()

        // 3. Check and request permissions
        checkAndRequestPermissions()

        // 4. Load the Web UI
        loadVisorUi()
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
            fun isKiosk(): Boolean = true
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
                    showOfflineScreen()
                }
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (!hasPageError) {
                    layoutOffline.visibility = View.GONE
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

        // Tap on gear icon: quick action menu (Reload, IP config, Reconnect)
        btnSettings.setOnClickListener {
            showKioskQuickMenu()
        }

        // Long press on gear icon: immediate 1-touch reload!
        btnSettings.setOnLongClickListener {
            reloadKioskWebUi()
            true
        }
    }

    private fun showKioskQuickMenu() {
        val options = arrayOf(
            "🔄 Ricarica Pagina (Reload UI)",
            "⚙️ Configura IP Master Server",
            "🔁 Riconnetti Audio & Telemetria"
        )
        AlertDialog.Builder(this)
            .setTitle("A-X-L Kiosk Menu")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> reloadKioskWebUi()
                    1 -> showIpConfigDialog()
                    2 -> reconnectAudioService()
                }
            }
            .setNegativeButton("Chiudi", null)
            .show()
    }

    private fun reloadKioskWebUi() {
        Toast.makeText(this, "Ricarica Visore in corso...", Toast.LENGTH_SHORT).show()
        hasPageError = false
        webView.clearCache(true)
        webView.reload()
    }

    private fun loadVisorUi() {
        hasPageError = false
        val uiUrl = KioskConfig.getUiUrl(this)
        tvTargetUrl.text = uiUrl
        layoutOffline.visibility = View.GONE
        webView.loadUrl(uiUrl)
    }

    private fun showOfflineScreen() {
        tvTargetUrl.text = KioskConfig.getUiUrl(this)
        layoutOffline.visibility = View.VISIBLE
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
        webView.destroy()
    }
}
