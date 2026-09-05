// Jest-only shim for react-native-svg — see react-native-gesture-handler.js
// in this directory for why a plain moduleNameMapper subpath/absolute-path
// redirect doesn't work here. @wc/ui only uses these as decorative icons;
// rendering nothing is enough for a "doesn't crash on mount" smoke test.
function Noop() {
  return null;
}

module.exports = {
  __esModule: true,
  default: Noop,
  Svg: Noop,
  Rect: Noop,
  Path: Noop,
  Circle: Noop,
};
