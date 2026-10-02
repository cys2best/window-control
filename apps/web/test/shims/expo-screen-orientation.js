// Jest-only shim for expo-screen-orientation. Its real "main" entry
// (build/ScreenOrientation.js) resolves fine (unlike the RN-ecosystem
// packages shimmed alongside this file — it has no package.json
// "react-native" field to trigger that quirk) but is shipped as untransformed
// ESM, which ts-jest's transformIgnorePatterns excludes by default since it's
// under node_modules. @wc/ui only calls `lockAsync` and reads
// `OrientationLock.LANDSCAPE`/`PORTRAIT_UP`.
module.exports = {
  lockAsync: async () => {},
  OrientationLock: { LANDSCAPE: "LANDSCAPE", PORTRAIT_UP: "PORTRAIT_UP" },
};
