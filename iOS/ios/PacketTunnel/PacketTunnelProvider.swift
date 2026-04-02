import NetworkExtension
import os.log

/// VibeVPN Packet Tunnel Provider
/// Runs as a separate process (Network Extension).
/// Connects to VPN server via WebSocket, forwards IP packets through the tunnel.
class PacketTunnelProvider: NEPacketTunnelProvider {

    private let log = OSLog(subsystem: "com.vibevpn.tunnel", category: "tunnel")
    private var wsTask: URLSessionWebSocketTask?
    private var urlSession: URLSession?
    private var assignedIp: String?
    private var isRunning = false

    // MARK: - Tunnel lifecycle

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        os_log("Starting tunnel...", log: log, type: .info)

        guard let config = protocolConfiguration as? NETunnelProviderProtocol,
              let serverAddress = config.serverAddress,
              let providerConfig = config.providerConfiguration,
              let username = providerConfig["username"] as? String,
              let password = providerConfig["password"] as? String,
              let port = providerConfig["port"] as? Int else {
            completionHandler(NSError(domain: "VibeVPN", code: 1, userInfo: [NSLocalizedDescriptionKey: "Missing configuration"]))
            return
        }

        let host = serverAddress
        isRunning = true

        connectWebSocket(host: host, port: port, username: username, password: password) { [weak self] result in
            switch result {
            case .success(let ip):
                self?.assignedIp = ip
                self?.configureTunnel(assignedIp: ip) { error in
                    if let error = error {
                        completionHandler(error)
                    } else {
                        self?.startPacketForwarding()
                        completionHandler(nil)
                    }
                }
            case .failure(let error):
                os_log("Connection failed: %{public}@", log: self?.log ?? .default, type: .error, error.localizedDescription)
                completionHandler(error)
            }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        os_log("Stopping tunnel, reason: %d", log: log, type: .info, reason.rawValue)
        isRunning = false
        wsTask?.cancel(with: .goingAway, reason: nil)
        wsTask = nil
        urlSession?.invalidateAndCancel()
        urlSession = nil
        completionHandler()
    }

    // MARK: - WebSocket connection

    private func connectWebSocket(host: String, port: Int, username: String, password: String,
                                   completion: @escaping (Result<String, Error>) -> Void) {
        let urlString = "wss://\(host):\(port)"
        guard let url = URL(string: urlString) else {
            completion(.failure(NSError(domain: "VibeVPN", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid URL"])))
            return
        }

        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false

        // For IP-based servers, disable TLS verification
        let isIp = host.allSatisfy { $0.isNumber || $0 == "." }
        let delegate = isIp ? InsecureWSDelegate() : nil
        urlSession = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
        wsTask = urlSession!.webSocketTask(with: url)
        wsTask?.resume()

        // Authenticate: send "username:password"
        let authMessage = URLSessionWebSocketTask.Message.string("\(username):\(password)")
        wsTask?.send(authMessage) { [weak self] error in
            if let error = error {
                completion(.failure(error))
                return
            }

            // Wait for assigned IP response
            self?.wsTask?.receive { result in
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let ip):
                        let trimmedIp = ip.trimmingCharacters(in: .whitespacesAndNewlines)
                        os_log("Assigned IP: %{public}@", log: self?.log ?? .default, type: .info, trimmedIp)

                        // Send hostname
                        let hostname = UIDevice.current.name
                        let hostMsg = URLSessionWebSocketTask.Message.string("HOST:\(hostname)")
                        self?.wsTask?.send(hostMsg) { _ in }

                        completion(.success(trimmedIp))
                    default:
                        completion(.failure(NSError(domain: "VibeVPN", code: 3, userInfo: [NSLocalizedDescriptionKey: "Unexpected response"])))
                    }
                case .failure(let error):
                    completion(.failure(error))
                }
            }
        }
    }

    // MARK: - Tunnel configuration

    private func configureTunnel(assignedIp: String, completion: @escaping (Error?) -> Void) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "10.8.0.1")

        // IPv4 settings
        let ipv4 = NEIPv4Settings(addresses: [assignedIp], subnetMasks: ["255.255.255.0"])
        ipv4.includedRoutes = [NEIPv4Route.default()]
        settings.ipv4Settings = ipv4

        // DNS
        settings.dnsSettings = NEDNSSettings(servers: ["1.1.1.1", "8.8.8.8"])

        // MTU
        settings.mtu = 1400

        setTunnelNetworkSettings(settings) { error in
            completion(error)
        }
    }

    // MARK: - Packet forwarding

    private func startPacketForwarding() {
        // TUN -> WebSocket (read from device, send to server)
        readPacketsFromTun()

        // WebSocket -> TUN (receive from server, write to device)
        receiveFromWebSocket()
    }

    private func readPacketsFromTun() {
        packetFlow.readPackets { [weak self] packets, protocols in
            guard let self = self, self.isRunning else { return }

            for packet in packets {
                // Send raw IP packet as binary WebSocket frame
                let msg = URLSessionWebSocketTask.Message.data(packet)
                self.wsTask?.send(msg) { error in
                    if error != nil {
                        os_log("WS send error", log: self.log, type: .error)
                    }
                }
            }

            // Continue reading
            self.readPacketsFromTun()
        }
    }

    private func receiveFromWebSocket() {
        guard isRunning else { return }

        wsTask?.receive { [weak self] result in
            guard let self = self, self.isRunning else { return }

            switch result {
            case .success(let message):
                switch message {
                case .data(let data):
                    // Binary = IP packet, write to TUN
                    if data.count >= 20 {
                        // Determine IP version for protocol number
                        let version = (data[0] >> 4)
                        let proto: NSNumber = version == 6 ? NSNumber(value: AF_INET6) : NSNumber(value: AF_INET)
                        self.packetFlow.writePackets([data], withProtocols: [proto])
                    }
                case .string(let text):
                    // Control messages (PEERS:, etc.) — handle if needed
                    if text.hasPrefix("PEERS:") {
                        // Store peers for UI (via App Group UserDefaults)
                        if let jsonData = text.dropFirst(6).data(using: .utf8) {
                            let defaults = UserDefaults(suiteName: "group.com.vibevpn.shared")
                            defaults?.set(jsonData, forKey: "peers")
                        }
                    }
                @unknown default:
                    break
                }

                // Continue receiving
                self.receiveFromWebSocket()

            case .failure(let error):
                os_log("WS receive error: %{public}@", log: self.log, type: .error, error.localizedDescription)
                // Attempt reconnect after delay
                if self.isRunning {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                        // TODO: Implement reconnection logic
                    }
                }
            }
        }
    }
}

// MARK: - Insecure WebSocket delegate (for IP-based servers)

class InsecureWSDelegate: NSObject, URLSessionDelegate {
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
