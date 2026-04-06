const { withPlugins, withEntitlementsPlist, withInfoPlist, withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

const APP_GROUP = 'group.com.vibevpn.shared';
const TUNNEL_BUNDLE_ID = 'com.vibevpn.app.tunnel';
const TUNNEL_TARGET_NAME = 'PacketTunnel';

function withVpnEntitlements(config) {
  return withEntitlementsPlist(config, (mod) => {
    mod.modResults['com.apple.developer.networking.networkextension'] = ['packet-tunnel-provider'];
    mod.modResults['com.apple.security.application-groups'] = [APP_GROUP];
    return mod;
  });
}

function withVpnInfoPlist(config) {
  return withInfoPlist(config, (mod) => {
    if (!mod.modResults.UIBackgroundModes) {
      mod.modResults.UIBackgroundModes = [];
    }
    if (!mod.modResults.UIBackgroundModes.includes('network-authentication')) {
      mod.modResults.UIBackgroundModes.push('network-authentication');
    }
    return mod;
  });
}

function withPacketTunnelFiles(config) {
  return withDangerousMod(config, [
    'ios',
    async (mod) => {
      const iosRoot = path.join(mod.modRequest.projectRoot, 'ios');
      const extDir = path.join(iosRoot, TUNNEL_TARGET_NAME);

      if (!fs.existsSync(extDir)) {
        fs.mkdirSync(extDir, { recursive: true });
      }

      // Copy PacketTunnelProvider.swift
      const srcFile = path.join(mod.modRequest.projectRoot, 'modules', 'expo-vpn', 'ios', 'PacketTunnelProvider.swift');
      const dstFile = path.join(extDir, 'PacketTunnelProvider.swift');
      if (fs.existsSync(srcFile)) {
        fs.copyFileSync(srcFile, dstFile);
      }

      // Info.plist
      const infoPlist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>PacketTunnel</string>
  <key>CFBundleExecutable</key>
  <string>$(EXECUTABLE_NAME)</string>
  <key>CFBundleIdentifier</key>
  <string>${TUNNEL_BUNDLE_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$(PRODUCT_NAME)</string>
  <key>CFBundlePackageType</key>
  <string>XPC!</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>NSExtension</key>
  <dict>
    <key>NSExtensionPointIdentifier</key>
    <string>com.apple.networkextension.packet-tunnel</string>
    <key>NSExtensionPrincipalClass</key>
    <string>$(PRODUCT_MODULE_NAME).PacketTunnelProvider</string>
  </dict>
</dict>
</plist>`;
      fs.writeFileSync(path.join(extDir, 'Info.plist'), infoPlist);

      // Entitlements
      const tunnelEntitlements = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.developer.networking.networkextension</key>
  <array>
    <string>packet-tunnel-provider</string>
  </array>
  <key>com.apple.security.application-groups</key>
  <array>
    <string>${APP_GROUP}</string>
  </array>
</dict>
</plist>`;
      fs.writeFileSync(path.join(extDir, `${TUNNEL_TARGET_NAME}.entitlements`), tunnelEntitlements);

      return mod;
    },
  ]);
}

function withVpn(config) {
  return withPlugins(config, [
    withVpnEntitlements,
    withVpnInfoPlist,
    withPacketTunnelFiles,
  ]);
}

module.exports = withVpn;
