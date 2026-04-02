# VibeVPN iOS - Build Instructions

## Prerequisites

1. **Xcode 16+** (from App Store, ~30GB)
2. **Apple Developer Account** ($99/year at developer.apple.com)
   - Needed for NetworkExtension entitlement
3. **CocoaPods**: `sudo gem install cocoapods`

## Setup

```bash
cd Client/iOS/VibeVPN

# Install JS dependencies
npm install

# Install native dependencies
cd ios
pod install
cd ..
```

## Xcode Project Setup

Since we can't use `npx react-native init` without Xcode, you need to create the Xcode workspace:

### Option A: Initialize with React Native CLI (recommended)

```bash
# From Client/iOS/ directory, create a fresh RN project:
npx react-native@latest init VibeVPNApp --directory VibeVPNApp

# Then copy our source files over:
cp -r VibeVPN/src VibeVPNApp/src/
cp VibeVPN/App.js VibeVPNApp/
cp VibeVPN/index.js VibeVPNApp/

# Copy native Swift files into the Xcode project:
cp VibeVPN/ios/VibeVPN/VPNManager.swift VibeVPNApp/ios/VibeVPNApp/
cp VibeVPN/ios/VibeVPN/VPNManagerBridge.m VibeVPNApp/ios/VibeVPNApp/
cp VibeVPN/ios/VibeVPN/VibeVPN-Bridging-Header.h VibeVPNApp/ios/VibeVPNApp/
cp VibeVPN/ios/VibeVPN/VibeVPN.entitlements VibeVPNApp/ios/VibeVPNApp/
```

### Option B: Add to existing Xcode project manually

1. Open `ios/VibeVPN.xcworkspace` in Xcode
2. Add Swift files to the main target:
   - `VPNManager.swift`
   - `VPNManagerBridge.m`
3. Set Bridging Header: Build Settings > "Objective-C Bridging Header" = `VibeVPN/VibeVPN-Bridging-Header.h`

### Add Network Extension Target

1. In Xcode: File > New > Target > **Network Extension**
2. Name: `PacketTunnel`
3. Provider Type: **Packet Tunnel Provider**
4. Language: **Swift**
5. Replace generated `PacketTunnelProvider.swift` with our file from `ios/PacketTunnel/PacketTunnelProvider.swift`

### Configure Entitlements

**Main App (VibeVPN)**:
- Capabilities > Network Extensions > Packet Tunnel ✅
- Capabilities > App Groups > `group.com.vibevpn.shared` ✅
- Capabilities > Personal VPN ✅

**PacketTunnel Extension**:
- Capabilities > Network Extensions > Packet Tunnel ✅
- Capabilities > App Groups > `group.com.vibevpn.shared` ✅

### Signing

1. In Xcode, select each target (VibeVPN + PacketTunnel)
2. Set Team to your Apple Developer account
3. Set Bundle Identifier:
   - App: `com.vibevpn.app` (or your own)
   - Extension: `com.vibevpn.app.tunnel`

### Apple Developer Portal

1. Go to developer.apple.com > Certificates, Identifiers & Profiles
2. Create App ID for `com.vibevpn.app` with:
   - Network Extensions ✅
   - App Groups ✅
   - Personal VPN ✅
3. Create App ID for `com.vibevpn.app.tunnel` with:
   - Network Extensions ✅
   - App Groups ✅
4. Create App Group: `group.com.vibevpn.shared`
5. Generate provisioning profiles for both

## Build & Run

```bash
# Build for simulator
npx react-native run-ios

# Build for device
npx react-native run-ios --device "iPhone"

# Archive for distribution
cd ios
xcodebuild -workspace VibeVPN.xcworkspace -scheme VibeVPN \
  -configuration Release -sdk iphoneos \
  -archivePath build/VibeVPN.xcarchive archive
```

## Create IPA

```bash
xcodebuild -exportArchive \
  -archivePath build/VibeVPN.xcarchive \
  -exportPath build/ \
  -exportOptionsPlist ExportOptions.plist
```

## Sideloading (without App Store)

For testing on devices without TestFlight:
1. Use **AltStore** or **Sideloadly**
2. Or install via Xcode directly (requires developer account)

## Architecture

```
VibeVPN (React Native App)
  ├── HomeScreen.js        — UI: server list, connect button, status
  ├── AddServerModal.js    — Add server form
  ├── vpn.js               — JS bridge to native VPNManager
  └── VPNManager.swift     — NETunnelProviderManager wrapper

PacketTunnel (Network Extension, separate process)
  └── PacketTunnelProvider.swift
      ├── WebSocket connection to VPN server (wss://)
      ├── Authentication (username:password)
      ├── TUN packet forwarding (packetFlow read/write)
      └── Peer list via App Group UserDefaults
```

## Protocol

The VPN server expects:
1. WebSocket connect to `wss://host:port`
2. Send text: `username:password`
3. Receive text: assigned IP (e.g., `10.8.0.42`)
4. Send text: `HOST:device-name`
5. Exchange binary frames = raw IPv4 packets
6. Receive text `PEERS:[...]` for peer discovery
