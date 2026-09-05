// Jest-only shim for react-native-reanimated — see react-native-gesture-handler.js
// in this directory for why a plain moduleNameMapper subpath/absolute-path
// redirect doesn't work here. @wc/ui only uses `runOnJS`.
module.exports = {
  runOnJS: (fn) => fn,
};
