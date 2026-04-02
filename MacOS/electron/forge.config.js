const { FusesPlugin } = require('@electron-forge/plugin-fuses');
const { FuseV1Options, FuseVersion } = require('@electron/fuses');
const path = require('path');

const srcDir = path.resolve(__dirname, '..', 'src');

module.exports = {
  packagerConfig: {
    asar: true,
    name: 'VibeVPN',
    extraResource: [
      path.resolve(__dirname, 'assets', 'trayTemplate.png'),
      path.resolve(__dirname, 'assets', 'tray.png'),
      // vpn-helper binary (built from src/vpn-helper.py via PyInstaller)
      path.resolve(srcDir, 'dist', 'vpn-helper'),
      path.resolve(srcDir, 'com.vibevpn.helper.plist'),
    ],
  },
  rebuildConfig: {},
  makers: [
    { name: '@electron-forge/maker-zip', platforms: ['darwin'] },
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
