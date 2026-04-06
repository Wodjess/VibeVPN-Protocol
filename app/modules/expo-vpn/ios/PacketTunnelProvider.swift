import NetworkExtension
import os.log

class PacketTunnelProvider: NEPacketTunnelProvider {
  private var wsTask: URLSessionWebSocketTask?
  private var urlSession: URLSession?
  private var isRunning = false
  private let logger = OSLog(subsystem: "com.vibevpn.tunnel", category: "PacketTunnel")

  override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
    guard let proto = protocolConfiguration as? NETunnelProviderProtocol,
          let config = proto.providerConfiguration,
          let host = config["host"] as? String,
          let port = config["port"] as? Int,
          let username = config["username"] as? String,
          let password = config["password"] as? String else {
      completionHandler(NSError(domain: "com.vibevpn.tunnel", code: 1, userInfo: [NSLocalizedDescriptionKey: "Missing configuration"]))
      return
    }

    connectWebSocket(host: host, port: port, username: username, password: password, completionHandler: completionHandler)
  }

  override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
    isRunning = false
    wsTask?.cancel(with: .goingAway, reason: nil)
    urlSession?.invalidateAndCancel()
    completionHandler()
  }

  private func connectWebSocket(host: String, port: Int, username: String, password: String, completionHandler: @escaping (Error?) -> Void) {
    let urlString = "wss://\(host):\(port)"
    guard let url = URL(string: urlString) else {
      completionHandler(NSError(domain: "com.vibevpn.tunnel", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid URL"]))
      return
    }

    let isIPAddress = host.allSatisfy { $0.isNumber || $0 == "." }
    let sessionDelegate: URLSessionDelegate? = isIPAddress ? InsecureWSDelegate() : nil
    let session = URLSession(configuration: .default, delegate: sessionDelegate, delegateQueue: nil)
    self.urlSession = session

    let task = session.webSocketTask(with: url)
    task.maximumMessageSize = 65536
    self.wsTask = task
    task.resume()

    let authMessage = "\(username):\(password)"
    task.send(.string(authMessage)) { [weak self] error in
      if let error = error {
        os_log("Auth send error: %{public}@", log: self?.logger ?? .default, type: .error, error.localizedDescription)
        completionHandler(error)
        return
      }

      task.receive { result in
        switch result {
        case .success(let message):
          switch message {
          case .string(let assignedIP):
            os_log("Assigned IP: %{public}@", log: self?.logger ?? .default, type: .info, assignedIP)

            let hostName = ProcessInfo.processInfo.hostName
            task.send(.string("HOST:\(hostName)")) { _ in }

            self?.configureTunnel(assignedIP: assignedIP.trimmingCharacters(in: .whitespacesAndNewlines)) { error in
              if let error = error {
                completionHandler(error)
                return
              }
              self?.isRunning = true
              completionHandler(nil)
              self?.startPacketForwarding()
            }
          default:
            completionHandler(NSError(domain: "com.vibevpn.tunnel", code: 3, userInfo: [NSLocalizedDescriptionKey: "Unexpected server response"]))
          }
        case .failure(let error):
          completionHandler(error)
        }
      }
    }
  }

  private func configureTunnel(assignedIP: String, completionHandler: @escaping (Error?) -> Void) {
    let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "10.8.0.1")

    let ipv4 = NEIPv4Settings(addresses: [assignedIP], subnetMasks: ["255.255.255.0"])
    ipv4.includedRoutes = [NEIPv4Route.default()]
    settings.ipv4Settings = ipv4

    settings.dnsSettings = NEDNSSettings(servers: ["1.1.1.1", "8.8.8.8"])
    settings.mtu = 1400

    setTunnelNetworkSettings(settings, completionHandler: completionHandler)
  }

  private func startPacketForwarding() {
    readPacketsFromTun()
    receiveFromWebSocket()
  }

  private func readPacketsFromTun() {
    packetFlow.readPackets { [weak self] packets, protocols in
      guard let self = self, self.isRunning, let wsTask = self.wsTask else { return }

      for packet in packets {
        wsTask.send(.data(packet)) { error in
          if let error = error {
            os_log("Send error: %{public}@", log: self.logger, type: .error, error.localizedDescription)
          }
        }
      }

      self.readPacketsFromTun()
    }
  }

  private func receiveFromWebSocket() {
    guard isRunning, let wsTask = wsTask else { return }

    wsTask.receive { [weak self] result in
      guard let self = self, self.isRunning else { return }

      switch result {
      case .success(let message):
        switch message {
        case .data(let data):
          if data.count >= 20 {
            let version = (data[0] >> 4)
            let proto: NSNumber = version == 6 ? NSNumber(value: AF_INET6) : NSNumber(value: AF_INET)
            self.packetFlow.writePackets([data], withProtocols: [proto])
          }
        case .string(let text):
          if text.hasPrefix("PEERS:") {
            let json = String(text.dropFirst(6))
            if let sharedDefaults = UserDefaults(suiteName: "group.com.vibevpn.shared") {
              sharedDefaults.set(json, forKey: "peers")
            }
          }
        @unknown default:
          break
        }
      case .failure(let error):
        os_log("Receive error: %{public}@", log: self.logger, type: .error, error.localizedDescription)
        return
      }

      self.receiveFromWebSocket()
    }
  }
}

class InsecureWSDelegate: NSObject, URLSessionDelegate {
  func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
    if let trust = challenge.protectionSpace.serverTrust {
      completionHandler(.useCredential, URLCredential(trust: trust))
    } else {
      completionHandler(.performDefaultHandling, nil)
    }
  }
}
