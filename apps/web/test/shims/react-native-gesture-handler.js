// Jest-only shim for react-native-gesture-handler.
//
// The real package's bare-specifier `import ... from "react-native-gesture-handler"`
// resolves, under Jest's default resolver, to its package.json "react-native"
// field (raw TS source that calls TurboModuleRegistry.getEnforicng for a
// native module that doesn't exist under jsdom) rather than its compiled
// "main"/"module" entry — confirmed by direct experiment: mapping the bare
// specifier to another node_modules path (even an absolute, already-resolved
// file path) under the SAME package name is silently ignored and the
// "react-native" field wins again; only mapping to a file entirely outside
// node_modules bypasses it. This is a Jest-resolution quirk unrelated to
// Next.js's webpack build (which uses next.config.js's own alias/extension
// config and is unaffected by this file).
//
// @wc/ui only uses the chainable Gesture builder (Tap/Pan with
// runOnJS/onEnd/activeOffsetY/failOffsetX) and <GestureDetector> as a
// pass-through wrapper — enough surface for this smoke test.
function chain() {
  const api = {
    runOnJS: () => api,
    onStart: () => api,
    onUpdate: () => api,
    onEnd: () => api,
    activeOffsetX: () => api,
    activeOffsetY: () => api,
    failOffsetX: () => api,
    failOffsetY: () => api,
  };
  return api;
}

module.exports = {
  Gesture: { Tap: chain, Pan: chain },
  GestureDetector: function GestureDetector(props) {
    return props.children ?? null;
  },
};
