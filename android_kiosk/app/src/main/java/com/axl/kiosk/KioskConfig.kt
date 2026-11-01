package com.axl.kiosk

import android.content.Context
import android.content.SharedPreferences

object KioskConfig {
    private const val PREFS_NAME = "axl_kiosk_prefs"
    private const val KEY_HOST = "master_host"
    private const val KEY_PORT = "master_port"

    private const val DEFAULT_HOST = "192.168.1.100"
    private const val DEFAULT_PORT = 8000

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun getHost(context: Context): String {
        return getPrefs(context).getString(KEY_HOST, DEFAULT_HOST) ?: DEFAULT_HOST
    }

    fun getPort(context: Context): Int {
        return getPrefs(context).getInt(KEY_PORT, DEFAULT_PORT)
    }

    fun saveServerAddress(context: Context, host: String, port: Int) {
        getPrefs(context).edit()
            .putString(KEY_HOST, host.trim())
            .putInt(KEY_PORT, port)
            .apply()
    }

    fun getUiUrl(context: Context): String {
        val host = getHost(context)
        val port = getPort(context)
        return "http://$host:$port/ui"
    }

    fun getAudioWsUrl(context: Context): String {
        val host = getHost(context)
        val port = getPort(context)
        return "ws://$host:$port/ws/audio"
    }

    fun getControlWsUrl(context: Context): String {
        val host = getHost(context)
        val port = getPort(context)
        return "ws://$host:$port/ws/ui"
    }
}
