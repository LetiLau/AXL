package com.axl.kiosk

import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.provider.AlarmClock
import android.util.Log
import android.view.KeyEvent
import org.json.JSONObject

object IntentExecutor {
    private const val TAG = "AXL-IntentExecutor"

    fun execute(context: Context, commandJson: JSONObject): Boolean {
        return try {
            val action = commandJson.optString("action", "").lowercase()
            when (action) {
                "set_volume" -> {
                    val volumePct = commandJson.optInt("volume", 50)
                    setMediaVolume(context, volumePct)
                    true
                }
                "media_control" -> {
                    val cmd = commandJson.optString("command", "play_pause").lowercase()
                    sendMediaKeyEvent(context, cmd)
                    true
                }
                "open_spotify" -> {
                    val query = commandJson.optString("query", "")
                    openSpotify(context, query)
                    true
                }
                "open_app" -> {
                    val pkg = commandJson.optString("package_name", "")
                    openAppByPackage(context, pkg)
                    true
                }
                "set_timer" -> {
                    val seconds = commandJson.optInt("seconds", 60)
                    val message = commandJson.optString("message", "Timer A-X-L")
                    setTimer(context, seconds, message)
                    true
                }
                "set_alarm" -> {
                    val hour = commandJson.optInt("hour", 8)
                    val minutes = commandJson.optInt("minutes", 0)
                    val message = commandJson.optString("message", "Sveglia A-X-L")
                    setAlarm(context, hour, minutes, message)
                    true
                }
                else -> {
                    Log.w(TAG, "Unknown intent action: $action")
                    false
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error executing intent: ${e.message}", e)
            false
        }
    }

    private fun setMediaVolume(context: Context, volumePct: Int) {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return
        val maxVol = audioManager.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val clampedPct = volumePct.coerceIn(0, 100)
        val targetVol = (clampedPct * maxVol) / 100
        audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, targetVol, AudioManager.FLAG_SHOW_UI)
        Log.i(TAG, "Media volume set to $clampedPct% ($targetVol/$maxVol)")
    }

    private fun sendMediaKeyEvent(context: Context, command: String) {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return
        val keyCode = when (command) {
            "play" -> KeyEvent.KEYCODE_MEDIA_PLAY
            "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT
            "prev", "previous" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            else -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
        }
        audioManager.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, keyCode))
        audioManager.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, keyCode))
        Log.i(TAG, "Dispatched media key code: $keyCode ($command)")
    }

    private fun openSpotify(context: Context, query: String) {
        val intent = if (query.isNotBlank()) {
            Intent(Intent.ACTION_VIEW, Uri.parse("spotify:search:$query")).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        } else {
            context.packageManager.getLaunchIntentForPackage("com.spotify.music")?.apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            } ?: Intent(Intent.ACTION_VIEW, Uri.parse("https://open.spotify.com")).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        }
        context.startActivity(intent)
        Log.i(TAG, "Launched Spotify (query: '$query')")
    }

    private fun openAppByPackage(context: Context, packageName: String) {
        if (packageName.isBlank()) return
        val launchIntent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (launchIntent != null) {
            launchIntent.flags = Intent.FLAG_ACTIVITY_NEW_TASK
            context.startActivity(launchIntent)
            Log.i(TAG, "Launched application: $packageName")
        } else {
            Log.w(TAG, "Package not found: $packageName")
        }
    }

    private fun setTimer(context: Context, seconds: Int, message: String) {
        val intent = Intent(AlarmClock.ACTION_SET_TIMER).apply {
            putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            putExtra(AlarmClock.EXTRA_MESSAGE, message)
            putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(intent)
        Log.i(TAG, "Set timer for $seconds seconds: '$message'")
    }

    private fun setAlarm(context: Context, hour: Int, minutes: Int, message: String) {
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, minutes)
            putExtra(AlarmClock.EXTRA_MESSAGE, message)
            putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(intent)
        Log.i(TAG, "Set alarm for $hour:$minutes - '$message'")
    }
}
