import Foundation
import NetworkExtension
import React

/// Native module bridging React Native <-> iOS NetworkExtension VPN
@objc(VPNManager)
class VPNManager: RCTEventEmitter {

    private var manager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?

    override init() {
        super.init()
        loadManager()
    }

    override static func requiresMainQueueSetup() -> Bool { true }
    override func supportedEvents() -> [String]! { ["vpnStatusChanged"] }

    // MARK: - Load existing VPN configuration

    private func loadManager() {
        NETunnelProviderManager.loadAllFromPreferences { [weak self] managers, error in
            self?.manager = managers?.first
            self?.observeStatus()
        }
    }

    private func observeStatus() {
        // Remove old observer
        if let obs = statusObserver { NotificationCenter.default.removeObserver(obs) }

        statusObserver = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange, object: nil, queue: .main
        ) { [weak self] _ in
            self?.emitStatus()
        }
        emitStatus()
    }

    private func emitStatus() {
        let status: String
        switch manager?.connection.status {
        case .connected: status = "connected"
        case .connecting: status = "connecting"
        case .disconnecting: status = "disconnecting"
        case .disconnected, .none: status = "disconnected"
        case .reasserting: status = "connecting"
        case .invalid: status = "disconnected"
        @unknown default: status = "disconnected"
        }
        sendEvent(withName: "vpnStatusChanged", body: ["status": status])
    }

    // MARK: - Connect

    @objc func connect(_ config: NSDictionary, resolver resolve: @escaping RCTPromiseResolveBlock, rejecter reject: @escaping RCTPromiseRejectBlock) {
        guard let host = config["host"] as? String,
              let port = config["port"] as? Int,
              let username = config["username"] as? String,
              let password = config["password"] as? String else {
            reject("E_CONFIG", "Missing host, port, username, or password", nil)
            return
        }

        let setupAndConnect = { [weak self] (mgr: NETunnelProviderManager) in
            let proto = NETunnelProviderProtocol()
            proto.providerBundleIdentifier = "com.vibevpn.app.tunnel"
            proto.serverAddress = host
            proto.providerConfiguration = [
                "username": username,
                "password": password,
                "port": port,
            ]
            // Display name in iOS Settings > VPN
            mgr.localizedDescription = "VibeVPN"
            mgr.protocolConfiguration = proto
            mgr.isEnabled = true

            mgr.saveToPreferences { error in
                if let error = error {
                    reject("E_SAVE", error.localizedDescription, error)
                    return
                }

                // Reload after save (required by iOS)
                mgr.loadFromPreferences { error in
                    if let error = error {
                        reject("E_LOAD", error.localizedDescription, error)
                        return
                    }

                    do {
                        try (mgr.connection as? NETunnelProviderSession)?.startTunnel()
                        self?.manager = mgr
                        self?.observeStatus()
                        resolve(["ok": true])
                    } catch {
                        reject("E_START", error.localizedDescription, error)
                    }
                }
            }
        }

        // Reuse existing manager or create new one
        if let mgr = manager {
            setupAndConnect(mgr)
        } else {
            let mgr = NETunnelProviderManager()
            setupAndConnect(mgr)
        }
    }

    // MARK: - Disconnect

    @objc func disconnect(_ resolve: @escaping RCTPromiseResolveBlock, rejecter reject: @escaping RCTPromiseRejectBlock) {
        manager?.connection.stopVPNTunnel()
        resolve(["ok": true])
    }

    // MARK: - Status

    @objc func getStatus(_ resolve: @escaping RCTPromiseResolveBlock, rejecter reject: @escaping RCTPromiseRejectBlock) {
        let status: String
        switch manager?.connection.status {
        case .connected: status = "connected"
        case .connecting: status = "connecting"
        case .disconnecting: status = "disconnecting"
        default: status = "disconnected"
        }

        // Read peers from App Group (written by PacketTunnelProvider)
        var peers: [[String: String]] = []
        if let defaults = UserDefaults(suiteName: "group.com.vibevpn.shared"),
           let data = defaults.data(forKey: "peers"),
           let parsed = try? JSONSerialization.jsonObject(with: data) as? [[String: String]] {
            peers = parsed
        }

        resolve(["status": status, "peers": peers])
    }
}
