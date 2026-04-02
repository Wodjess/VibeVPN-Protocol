# VibeVPN Windows - Build Instructions

## Prerequisites

- **Node.js 20+** (https://nodejs.org)
- **macOS or Windows** build machine (cross-compilation from Mac is supported)

## Build steps

```bash
# 1. Install dependencies
npm install

# 2. Build for Windows (works from macOS or Windows)
npx electron-forge package --platform win32 --arch x64

# 3. Output is in:
#    out/VibeVPN-win32-x64/
```

The build includes:
- `VibeVPN.exe` - Main application
- `VibeVPN (Admin).vbs` - Launcher with UAC elevation
- `resources/wintun.dll` - WinTUN driver
- `resources/app.asar` - Bundled application code
- `resources/app.asar.unpacked/node_modules/koffi/` - Native FFI module

## How the build works

1. **Webpack** bundles `src/main.js` and `src/renderer.js` into optimized bundles
2. **koffi** and **ws** are marked as webpack externals (not bundled, loaded at runtime)
3. **electron-forge** packages the Electron shell + webpack bundles + node_modules
4. **afterCopy hook** in `forge.config.js` copies `koffi` and `ws` into the packaged app's `node_modules/`
5. **wintun.dll** is bundled via `extraResource`
6. **VibeVPN.vbs** is copied next to the exe for UAC elevation

## Rebuilding after code changes

```bash
# Clean and rebuild
rm -rf .webpack out
npx electron-forge package --platform win32 --arch x64
```

## Notes

- `wintun.dll` (amd64) is included in `assets/`. If you need a different architecture, download from https://www.wintun.net
- The app requires Administrator privileges to create the TUN adapter
- koffi includes prebuilt native binaries for all platforms, so cross-compilation works
