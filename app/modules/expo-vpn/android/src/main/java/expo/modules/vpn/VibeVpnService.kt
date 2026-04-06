package expo.modules.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.InetAddress
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.*

class VibeVpnService : VpnService() {
  companion object {
    private const val TAG = "VibeVpnService"
    private const val CHANNEL_ID = "vibevpn_channel"
    private const val NOTIFICATION_ID = 1
  }

  private var vpnInterface: ParcelFileDescriptor? = null
  private var webSocket: WebSocket? = null
  private var readThread: Thread? = null
  @Volatile private var isRunning = false

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    when (intent?.action) {
      "START" -> {
        val host = intent.getStringExtra("host") ?: return START_NOT_STICKY
        val port = intent.getIntExtra("port", 443)
        val username = intent.getStringExtra("username") ?: return START_NOT_STICKY
        val password = intent.getStringExtra("password") ?: return START_NOT_STICKY

        startForegroundNotification()
        updateStatus("connecting")
        connectWebSocket(host, port, username, password)
      }
      "STOP" -> {
        stopVpn()
        stopSelf()
      }
    }
    return START_STICKY
  }

  private fun connectWebSocket(host: String, port: Int, username: String, password: String) {
    val trustAllCerts = arrayOf<TrustManager>(object : X509TrustManager {
      override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
      override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
      override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
    })

    val sslContext = SSLContext.getInstance("TLS")
    sslContext.init(null, trustAllCerts, java.security.SecureRandom())

    val client = OkHttpClient.Builder()
      .sslSocketFactory(sslContext.socketFactory, trustAllCerts[0] as X509TrustManager)
      .hostnameVerifier { _, _ -> true }
      .pingInterval(20, TimeUnit.SECONDS)
      .readTimeout(0, TimeUnit.SECONDS)
      .build()

    val request = Request.Builder()
      .url("wss://$host:$port")
      .build()

    webSocket = client.newWebSocket(request, object : WebSocketListener() {
      private var authenticated = false

      override fun onOpen(ws: WebSocket, response: Response) {
        Log.d(TAG, "WebSocket connected, authenticating...")
        ws.send("$username:$password")
      }

      override fun onMessage(ws: WebSocket, text: String) {
        if (!authenticated) {
          authenticated = true
          val assignedIP = text.trim()
          Log.d(TAG, "Assigned IP: $assignedIP")

          ws.send("HOST:${Build.MODEL}")

          setupTunnel(assignedIP)
          updateStatus("connected")
          return
        }

        if (text.startsWith("PEERS:")) {
          val json = text.substring(6)
          savePeers(json)
        }
      }

      override fun onMessage(ws: WebSocket, bytes: ByteString) {
        val data = bytes.toByteArray()
        if (data.size >= 20 && isRunning) {
          try {
            val output = FileOutputStream(vpnInterface!!.fileDescriptor)
            output.write(data)
            output.flush()
          } catch (e: Exception) {
            Log.e(TAG, "Write to TUN error: ${e.message}")
          }
        }
      }

      override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
        Log.e(TAG, "WebSocket failure: ${t.message}")
        updateStatus("disconnected")
        stopVpn()
        stopSelf()
      }

      override fun onClosed(ws: WebSocket, code: Int, reason: String) {
        Log.d(TAG, "WebSocket closed: $reason")
        updateStatus("disconnected")
        stopVpn()
        stopSelf()
      }
    })
  }

  private fun setupTunnel(assignedIP: String) {
    val builder = Builder()
      .setSession("VibeVPN")
      .setMtu(1400)
      .addAddress(assignedIP, 24)
      .addRoute("0.0.0.0", 0)
      .addDnsServer("1.1.1.1")
      .addDnsServer("8.8.8.8")

    vpnInterface = builder.establish()
    isRunning = true

    readThread = Thread {
      val input = FileInputStream(vpnInterface!!.fileDescriptor)
      val buffer = ByteArray(1500)
      while (isRunning) {
        try {
          val length = input.read(buffer)
          if (length > 0) {
            webSocket?.send(buffer.copyOf(length).toByteString())
          }
        } catch (e: Exception) {
          if (isRunning) {
            Log.e(TAG, "Read from TUN error: ${e.message}")
          }
          break
        }
      }
    }.apply {
      name = "vpn-tun-reader"
      start()
    }
  }

  private fun stopVpn() {
    isRunning = false
    updateStatus("disconnecting")

    readThread?.interrupt()
    readThread = null

    webSocket?.close(1000, "User disconnected")
    webSocket = null

    vpnInterface?.close()
    vpnInterface = null

    updateStatus("disconnected")
  }

  private fun updateStatus(status: String) {
    getSharedPreferences("vibevpn", Context.MODE_PRIVATE)
      .edit()
      .putString("vpn_status", status)
      .apply()

    val intent = Intent(ExpoVpnModule.ACTION_VPN_STATUS).apply {
      putExtra(ExpoVpnModule.EXTRA_STATUS, status)
      setPackage(packageName)
    }
    sendBroadcast(intent)
  }

  private fun savePeers(json: String) {
    getSharedPreferences("vibevpn", Context.MODE_PRIVATE)
      .edit()
      .putString("vpn_peers", json)
      .apply()
  }

  private fun startForegroundNotification() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      val channel = NotificationChannel(CHANNEL_ID, "VibeVPN", NotificationManager.IMPORTANCE_LOW).apply {
        description = "VPN connection status"
      }
      val nm = getSystemService(NotificationManager::class.java)
      nm.createNotificationChannel(channel)
    }

    val notification = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      Notification.Builder(this, CHANNEL_ID)
    } else {
      @Suppress("DEPRECATION")
      Notification.Builder(this)
    }
      .setContentTitle("VibeVPN")
      .setContentText("Connected")
      .setSmallIcon(android.R.drawable.ic_lock_lock)
      .build()

    startForeground(NOTIFICATION_ID, notification)
  }

  override fun onDestroy() {
    stopVpn()
    super.onDestroy()
  }
}
