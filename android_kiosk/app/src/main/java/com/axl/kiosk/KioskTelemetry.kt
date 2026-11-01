package com.axl.kiosk

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.WifiManager
import android.os.BatteryManager
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

        return payload
    }
}
