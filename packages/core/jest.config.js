module.exports = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
  testTimeout: 15000,
  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],
};
