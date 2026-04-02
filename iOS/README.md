# VibeVPN iOS Client

React Native VPN client for iPhone/iPad. Uses iOS NetworkExtension (Packet Tunnel Provider) for the tunnel and WebSocket (WSS) for the encrypted connection.

## Architecture

```
VibeVPN (React Native App)
  HomeScreen.js          - Main UI: status, connect, server list, peers
  AddServerModal.js      - Server configuration form
  vpn.js                 - JS bridge to native VPNManager module
      |
      | NETunnelProviderManager API
      v
PacketTunnel (iOS Network Extension, separate process)
  PacketTunnelProvider.swift
    - WebSocket connection via URLSession
    - Authentication (username:password)
    - Packet forwarding (packetFlow.readPackets / writePackets)
    - Peer list shared via App Group UserDefaults
```

## How it works

1. User opens the app, adds a VPN server (host, port, username, password)
2. Taps Connect
3. React Native calls `VPNManager.connect()` (native Swift module)
4. VPNManager configures `NETunnelProviderManager` and starts the tunnel
5. iOS launches the PacketTunnel extension in a separate process
6. PacketTunnelProvider connects to VPN server via WebSocket (WSS)
7. Authenticates, receives assigned IP, configures the tunnel interface
8. Packets flow: iOS network stack <-> packetFlow <-> WebSocket <-> VPN server
9. Peer list is shared from extension to app via App Group UserDefaults

## Key files

| File | Purpose |
|------|---------|
| `src/screens/HomeScreen.js` | Main screen: status, connect button, server list |
| `src/components/AddServerModal.js` | Add server form |
| `src/utils/vpn.js` | JS bridge to native VPNManager |
| `src/utils/storage.js` | AsyncStorage for server list |
| `ios/VibeVPN/VPNManager.swift` | Native module: NETunnelProviderManager |
| `ios/VibeVPN/VPNManagerBridge.m` | ObjC bridge for React Native |
| `ios/PacketTunnel/PacketTunnelProvider.swift` | VPN tunnel: WebSocket + packet forwarding |

## Requirements

- iPhone/iPad with iOS 16+
- Apple Developer Account ($99/year) with Network Extensions entitlement
- Xcode 16+ (for building)
