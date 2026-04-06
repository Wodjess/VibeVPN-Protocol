package expo.modules.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.VpnService
import android.os.Build
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.Promise

class ExpoVpnModule : Module() {
  companion object {
    const val ACTION_VPN_STATUS = "com.vibevpn.VPN_STATUS"
    const val ACTION_VPN_PEERS = "com.vibevpn.VPN_PEERS"
    const val EXTRA_STATUS = "status"
    const val EXTRA_PEERS = "peers"
  }

  private var statusReceiver: BroadcastReceiver? = null

  override fun definition() = ModuleDefinition {
    Name("ExpoVpn")

    Events("onStatusChange")

    OnCreate {
      registerStatusReceiver()
    }

    OnDestroy {
      unregisterStatusReceiver()
    }

    AsyncFunction("connect") { config: Map<String, Any?>, promise: Promise ->
      val context = appContext.reactContext ?: run {
        promise.reject("NO_CONTEXT", "No application context", null)
        return@AsyncFunction
      }

      val host = config["host"] as? String ?: run {
        promise.reject("INVALID_CONFIG", "Missing host", null)
        return@AsyncFunction
      }
      val port = (config["port"] as? Double)?.toInt() ?: 443
      val username = config["username"] as? String ?: ""
      val password = config["password"] as? String ?: ""

      // Check if VPN permission is granted
      val prepareIntent = VpnService.prepare(context)
      if (prepareIntent != null) {
        val activity = appContext.currentActivity
        if (activity != null) {
          activity.startActivityForResult(prepareIntent, 1001)
        }
        promise.reject("VPN_PERMISSION", "VPN permission required. Please try again after granting permission.", null)
        return@AsyncFunction
      }

      val intent = Intent(context, VibeVpnService::class.java).apply {
        action = "START"
        putExtra("host", host)
        putExtra("port", port)
        putExtra("username", username)
        putExtra("password", password)
      }

      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        context.startForegroundService(intent)
      } else {
        context.startService(intent)
      }

      promise.resolve(null)
    }

    AsyncFunction("disconnect") { promise: Promise ->
      val context = appContext.reactContext ?: run {
        promise.reject("NO_CONTEXT", "No application context", null)
        return@AsyncFunction
      }

      val intent = Intent(context, VibeVpnService::class.java).apply {
        action = "STOP"
      }
      context.startService(intent)
      promise.resolve(null)
    }

    AsyncFunction("getStatus") { promise: Promise ->
      val context = appContext.reactContext ?: run {
        promise.reject("NO_CONTEXT", "No application context", null)
        return@AsyncFunction
      }

      val prefs = context.getSharedPreferences("vibevpn", Context.MODE_PRIVATE)
      val status = prefs.getString("vpn_status", "disconnected") ?: "disconnected"
      val peersJson = prefs.getString("vpn_peers", "[]") ?: "[]"

      val peers = try {
        val arr = org.json.JSONArray(peersJson)
        (0 until arr.length()).map { arr.getString(it) }
      } catch (e: Exception) {
        emptyList<String>()
      }

      promise.resolve(mapOf(
        "status" to status,
        "peers" to peers
      ))
    }
  }

  private fun registerStatusReceiver() {
    val context = appContext.reactContext ?: return
    statusReceiver = object : BroadcastReceiver() {
      override fun onReceive(ctx: Context?, intent: Intent?) {
        val status = intent?.getStringExtra(EXTRA_STATUS) ?: return
        sendEvent("onStatusChange", mapOf("status" to status))
      }
    }
    val filter = IntentFilter(ACTION_VPN_STATUS)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
      context.registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
    } else {
      context.registerReceiver(statusReceiver, filter)
    }
  }

  private fun unregisterStatusReceiver() {
    val context = appContext.reactContext ?: return
    statusReceiver?.let {
      try { context.unregisterReceiver(it) } catch (_: Exception) {}
    }
  }
}
