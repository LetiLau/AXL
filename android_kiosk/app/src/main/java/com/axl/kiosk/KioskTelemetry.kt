package com.axl.kiosk

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

object KioskTelemetry {

    fun collect(context: Context): JSONObject {
        val payload = JSONObject()
        payload.put("type", "KIOSK_TELEMETRY")

        // 1. Battery metrics (percentage, charging state, temperature)
        val batteryIntent = context.registerReceiver(
            null,
            IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        )

        var batteryPct = -1
        var isCharging = false
        var batteryTempC = 0.0

        if (batteryIntent != null) {
            val level = batteryIntent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = batteryIntent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
            if (level >= 0 && scale > 0) {
                batteryPct = ((level.toFloat() / scale.toFloat()) * 100).toInt()
            }

            val status = batteryIntent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
            isCharging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                    status == BatteryManager.BATTERY_STATUS_FULL

            // Temperature is reported in tenths of a degree Celsius (e.g. 320 -> 32.0 C)
            val rawTemp = batteryIntent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0)
            batteryTempC = (rawTemp / 10.0)
        }

        payload.put("battery_pct", batteryPct)
        payload.put("is_charging", isCharging)
        payload.put("battery_temp", String.format(Locale.US, "%.1f", batteryTempC).toDouble())

        // 2. RAM metrics (ActivityManager)
        val actManager = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
        var ramUsedPct = 0.0
        var ramFreeMb = 0L
        if (actManager != null) {
            val memInfo = ActivityManager.MemoryInfo()
            actManager.getMemoryInfo(memInfo)
            val totalMem = memInfo.totalMem
            val availMem = memInfo.availMem
            val usedMem = totalMem - availMem
            ramUsedPct = (usedMem.toDouble() / totalMem.toDouble()) * 100.0
            ramFreeMb = availMem / (1024 * 1024)
        }

        payload.put("ram_used_pct", String.format(Locale.US, "%.1f", ramUsedPct).toDouble())
        payload.put("ram_free_mb", ramFreeMb)

        // 3. Wi-Fi Signal RSSI
        var wifiRssiDbm = -100
        try {
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val wifiInfo = wifiManager?.connectionInfo
            if (wifiInfo != null) {
                wifiRssiDbm = wifiInfo.rssi
            }
        } catch (_: Exception) {
            // Optional permission fallback
        }
        payload.put("wifi_rssi_dbm", wifiRssiDbm)

        // 4. SoC / Hardware Info
        payload.put("cpu_cores", Runtime.getRuntime().availableProcessors())
        payload.put("soc_name", "Exynos 1280")

        // 5. Connected Audio Output Routes (Bluetooth A2DP / Wired / USB) without extra permissions
        payload.put("audio_outputs", collectAudioOutputs(context))

        return payload
    }

    private fun collectAudioOutputs(context: Context): JSONArray {
        val outputs = JSONArray()
        val seen = HashSet<String>()
        try {
            val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return outputs
            val devices = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            for (dev in devices) {
                val typeLabel = when (dev.type) {
                    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "bluetooth_a2dp"
                    AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "bluetooth_sco"
                    AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER -> "bluetooth_le"
                    AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "wired_aux"
                    AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_USB_HEADSET -> "usb_audio"
                    else -> continue
                }
                val name = dev.productName?.toString()?.trim().orEmpty().ifEmpty { typeLabel }
                val dedupeKey = "$typeLabel:$name"
                if (seen.add(dedupeKey)) {
                    outputs.put(
                        JSONObject().apply {
                            put("name", name)
                            put("type", typeLabel)
                        }
                    )
                }
            }
        } catch (_: Exception) {
            // Ignore audio device inspection errors on restricted profiles
        }
        return outputs
    }
}
