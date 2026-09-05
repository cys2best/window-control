/** @type {import('next').NextConfig} */
const path = require("path");

module.exports = {
  output: "export",
  transpilePackages: ["@wc/core", "@wc/ui", "expo-screen-orientation", "expo-modules-core"],
  webpack: (config, { webpack }) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "react-native$": "react-native-web",
    };
    config.resolve.extensions = [".web.js", ".web.ts", ".web.tsx", ...config.resolve.extensions];
    // react-native-web (and RN code transitively pulled in through @wc/ui)
    // reads the global `__DEV__`, which Metro injects automatically but a
    // plain webpack build never defines — without this, prerendering
    // (`next build`'s server-side pass to produce the static HTML shell)
    // throws "ReferenceError: __DEV__ is not defined".
    config.plugins.push(
      new webpack.DefinePlugin({ __DEV__: JSON.stringify(process.env.NODE_ENV !== "production") })
    );
    return config;
  },
};
