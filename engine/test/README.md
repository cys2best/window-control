# engine/test/README.md

`SignalingClient` tests require a running signaling server at
`ws://localhost:8443` with auth disabled. Use the repository's Node relay:

```powershell
Set-Location infra\vps\signaling
Remove-Item Env:JWT_SECRET -ErrorAction SilentlyContinue
npm install
npm start
```

Keep that window running, then build and execute the live tests in another
window:

```powershell
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
```
