module.exports = {
  testEnvironment: "jsdom",
  // `next build`/`next dev` unconditionally rewrite tsconfig.json (forcing
  // "jsx": "preserve", "incremental": true, and a ".next/types" include) —
  // ts-jest needs actual JSX-to-JS output and a single common source root,
  // so it uses its own tsconfig (tsconfig.jest.json) that Next never touches.
  transform: {
    "^.+\\.tsx?$": ["ts-jest", { tsconfig: "tsconfig.jest.json" }],
  },
  moduleNameMapper: {
    // `react-native` -> `react-native-web` mirrors next.config.js's webpack
    // alias so @wc/ui's RN-primitive components render under jsdom.
    "^react-native$": "react-native-web",
    // These RN-ecosystem packages' bare specifiers resolve, under Jest's
    // default resolver, to their package.json "react-native" field (raw
    // TypeScript source that calls native-module registries which don't
    // exist under jsdom) rather than their compiled/web-safe entry —
    // confirmed by direct experiment: redirecting the specifier to another
    // path under the SAME package name (even an absolute, already-resolved
    // file path) is silently ignored and the "react-native" field wins
    // again; only a target entirely outside node_modules bypasses it. This
    // is a Jest module-resolution quirk specific to the test environment —
    // it doesn't affect the real Next.js webpack build, which uses
    // next.config.js's own alias/extension config. See test/shims/*.js for
    // the minimal replacements (just enough surface for @wc/ui's usage).
    "^react-native-gesture-handler$": "<rootDir>/test/shims/react-native-gesture-handler.js",
    "^react-native-reanimated$": "<rootDir>/test/shims/react-native-reanimated.js",
    "^react-native-svg$": "<rootDir>/test/shims/react-native-svg.js",
    // expo-screen-orientation's real entry doesn't hit the "react-native"
    // field quirk above, but ships untransformed ESM under node_modules —
    // shimmed for the same practical reason (see file for detail).
    "^expo-screen-orientation$": "<rootDir>/test/shims/expo-screen-orientation.js",
  },
};
