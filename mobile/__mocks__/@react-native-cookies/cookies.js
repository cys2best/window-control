// Manual mock, auto-applied by Jest for every test (see __mocks__/react-native-webrtc.js
// for the same convention). The real module reaches for NativeModules.RNCookieManagerIOS/
// Android via invariant(), which throws under Jest -- nothing under test needs the real
// native cookie jar, just a stand-in that resolves.
module.exports = {
  getAll: jest.fn(async () => ({})),
  clearAll: jest.fn(async () => true),
  get: jest.fn(async () => ({})),
  set: jest.fn(async () => true),
  clearByName: jest.fn(async () => true),
  flush: jest.fn(async () => {}),
  removeSessionCookies: jest.fn(async () => true),
};
