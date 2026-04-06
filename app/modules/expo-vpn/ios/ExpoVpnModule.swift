import ExpoModulesCore
import NetworkExtension

public class ExpoVpnModule: Module {
  private var vpnManager: NETunnelProviderManager?
  private var statusObserver: NSObjectProtocol?

  public func definition() -> ModuleDefinition {
    Name("ExpoVpn")

    Events("onStatusChange")

    OnCreate {
      self.observeVpnStatus()
    }

    OnDestroy {
      if let observer = self.statusObserver {
        NotificationCenter.default.removeObserver(observer)
      }
    }

    AsyncFunction("connect") { (config: [String: Any], promise: Promise) in
      guard let host = config["host"] as? String,
            let port = config["port"] as? Int,
            let username = config["username"] as? String,
            let password = config["password"] as? String else {
        promise.reject("INVALID_CONFIG", "Missing host, port, username, or password")
        return
      }

      NETunnelProviderManager.loadAllFromPreferences { managers, error in
        if let error = error {
          promise.reject("LOAD_ERROR", error.localizedDescription)
          return
        }

        let manager = managers?.first ?? NETunnelProviderManager()
        self.vpnManager = manager

        let proto = NETunnelProviderProtocol()
        proto.providerBundleIdentifier = "com.vibevpn.app.tunnel"
        proto.serverAddress = "\(host):\(port)"
        proto.providerConfiguration = [
          "host": host,
          "port": port,
          "username": username,
          "password": password
        ]

        manager.protocolConfiguration = proto
        manager.localizedDescription = "VibeVPN"
        manager.isEnabled = true

        manager.saveToPreferences { error in
          if let error = error {
            promise.reject("SAVE_ERROR", error.localizedDescription)
            return
          }

          manager.loadFromPreferences { error in
            if let error = error {
              promise.reject("RELOAD_ERROR", error.localizedDescription)
              return
            }

            do {
              let session = manager.connection as! NETunnelProviderSession
              try session.startTunnel()
              promise.resolve(nil)
            } catch {
              promise.reject("START_ERROR", error.localizedDescription)
            }
          }
        }
      }
    }

    AsyncFunction("disconnect") { (promise: Promise) in
      guard let manager = self.vpnManager else {
        NETunnelProviderManager.loadAllFromPreferences { managers, error in
          if let manager = managers?.first {
            manager.connection.stopVPNTunnel()
          }
          promise.resolve(nil)
        }
        return
      }
      manager.connection.stopVPNTunnel()
      promise.resolve(nil)
    }

    AsyncFunction("getStatus") { (promise: Promise) in
      NETunnelProviderManager.loadAllFromPreferences { managers, error in
        let manager = managers?.first ?? self.vpnManager
        let status = self.mapStatus(manager?.connection.status)

        var peers: [String] = []
        if let sharedDefaults = UserDefaults(suiteName: "group.com.vibevpn.shared"),
           let peersData = sharedDefaults.string(forKey: "peers"),
           let data = peersData.data(using: .utf8),
           let parsed = try? JSONSerialization.jsonObject(with: data) as? [String] {
          peers = parsed
        }

        promise.resolve([
          "status": status,
          "peers": peers
        ])
      }
    }
  }

  private func observeVpnStatus() {
    statusObserver = NotificationCenter.default.addObserver(
      forName: .NEVPNStatusDidChange,
      object: nil,
      queue: .main
    ) { [weak self] notification in
      guard let connection = notification.object as? NEVPNConnection else { return }
      let status = self?.mapStatus(connection.status) ?? "disconnected"
      self?.sendEvent("onStatusChange", ["status": status])
    }
  }

  private func mapStatus(_ status: NEVPNStatus?) -> String {
    switch status {
    case .connected: return "connected"
    case .connecting: return "connecting"
    case .disconnecting: return "disconnecting"
    case .reasserting: return "connecting"
    default: return "disconnected"
    }
  }
}
