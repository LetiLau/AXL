package com.axl.kiosk

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

data class SavedHost(
    val name: String,
    val host: String,
    val port: Int,
)

object KioskConfig {
    private const val PREFS_NAME = "axl_kiosk_prefs"
    private const val KEY_HOST = "master_host"
    private const val KEY_PORT = "master_port"
    private const val KEY_CONFIGURED = "is_configured"
    private const val KEY_SAVED_HOSTS = "saved_hosts"

    private const val DEFAULT_HOST = "192.168.1.100"
    private const val DEFAULT_PORT = 8000

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun isConfigured(context: Context): Boolean {
        return getPrefs(context).getBoolean(KEY_CONFIGURED, false)
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
            .putBoolean(KEY_CONFIGURED, true)
            .apply()
    }

    fun getSavedHosts(context: Context): List<SavedHost> {
        val raw = getPrefs(context).getString(KEY_SAVED_HOSTS, null) ?: return emptyList()
        val list = mutableListOf<SavedHost>()
        try {
            val arr = JSONArray(raw)
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                val name = obj.optString("name", "").trim()
                val host = obj.optString("host", "").trim()
                val port = obj.optInt("port", DEFAULT_PORT)
                if (name.isNotEmpty() && host.isNotEmpty()) {
                    list.add(SavedHost(name = name, host = host, port = port))
                }
            }
        } catch (_: Exception) {
            return emptyList()
        }
        return list
    }

    private fun persistSavedHosts(context: Context, hosts: List<SavedHost>) {
        val arr = JSONArray()
        for (item in hosts) {
            val obj = JSONObject().apply {
                put("name", item.name)
                put("host", item.host)
                put("port", item.port)
            }
            arr.put(obj)
        }
        getPrefs(context).edit()
            .putString(KEY_SAVED_HOSTS, arr.toString())
            .apply()
    }

    fun saveHostProfile(context: Context, name: String, host: String, port: Int) {
        val cleanName = name.trim()
        val cleanHost = host.trim()
        if (cleanName.isEmpty() || cleanHost.isEmpty()) return

        val updated = getSavedHosts(context)
            .filterNot {
                (it.host == cleanHost && it.port == port) ||
                    it.name.equals(cleanName, ignoreCase = true)
            }
            .toMutableList()

        updated.add(0, SavedHost(name = cleanName, host = cleanHost, port = port))
        persistSavedHosts(context, updated)
    }

    fun deleteSavedHost(context: Context, host: String, port: Int) {
        val cleanHost = host.trim()
        val updated = getSavedHosts(context).filterNot {
            it.host == cleanHost && it.port == port
        }
        persistSavedHosts(context, updated)
    }

    fun findSavedHostName(context: Context, host: String, port: Int): String? {
        val cleanHost = host.trim()
        return getSavedHosts(context).firstOrNull {
            it.host == cleanHost && it.port == port
        }?.name
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
        // Identify background service socket separately from browser Visor tabs
        return "ws://$host:$port/ws/ui?client=kiosk_service"
    }
}
