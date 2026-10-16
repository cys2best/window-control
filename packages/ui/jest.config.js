module.exports = {
  preset: "jest-expo",
  testTimeout: 20000,
  transformIgnorePatterns: [
    "node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo(nent)?|expo-.*|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|react-native-webrtc|react-native-gesture-handler|react-native-reanimated)/)",
  ],
};
