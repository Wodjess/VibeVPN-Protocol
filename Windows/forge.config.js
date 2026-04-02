const { FusesPlugin } = require('@electron-forge/plugin-fuses');
const { FuseV1Options, FuseVersion } = require('@electron/fuses');
const path = require('path');
const fs = require('fs');

function copyDirSync(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDirSync(s, d);
    else fs.copyFileSync(s, d);
  }
}

module.exports = {
  packagerConfig: {
    asar: {
      unpack: '{**/koffi/**,**/ws/**}',
    },
    name: 'VibeVPN',
    extraResource: [
      path.resolve(__dirname, 'assets', 'trayTemplate.png'),
      path.resolve(__dirname, 'assets', 'tray.png'),
      path.resolve(__dirname, 'assets', 'wintun.dll'),
    ],
    afterCopy: [
      (buildPath, electronVersion, platform, arch, callback) => {
        const targetNM = path.join(buildPath, 'node_modules');
        fs.mkdirSync(targetNM, { recursive: true });
        for (const mod of ['koffi', 'ws']) {
          const src = path.join(__dirname, 'node_modules', mod);
          if (fs.existsSync(src)) {
            copyDirSync(src, path.join(targetNM, mod));
          }
        }
        // VBS launcher for admin elevation
        const vbsSrc = path.join(__dirname, 'assets', 'VibeVPN.vbs');
        if (fs.existsSync(vbsSrc)) {
          fs.copyFileSync(vbsSrc, path.join(buildPath, '..', '..', 'VibeVPN (Admin).vbs'));
        }
        callback();
      },
    ],
  },
  rebuildConfig: {},
  makers: [
    { name: '@electron-forge/maker-squirrel', config: { name: 'VibeVPN' } },
  ],
  plugins: [
    { name: '@electron-forge/plugin-auto-unpack-natives', config: {} },
    {
      name: '@electron-forge/plugin-webpack',
      config: {
        mainConfig: './webpack.main.config.js',
        renderer: {
          config: './webpack.renderer.config.js',
          entryPoints: [{
            html: './src/index.html',
            js: './src/renderer.js',
            name: 'main_window',
            preload: { js: './src/preload.js' },
          }],
        },
      },
    },
    new FusesPlugin({
      version: FuseVersion.V1,
      [FuseV1Options.RunAsNode]: false,
      [FuseV1Options.EnableCookieEncryption]: true,
      [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
      [FuseV1Options.EnableNodeCliInspectArguments]: false,
      [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: false,
      [FuseV1Options.OnlyLoadAppFromAsar]: false,
    }),
  ],
};
