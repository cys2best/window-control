# engine/test/README.md

`SignalingClient` tests require a running signaling server at
`ws://localhost:8443` with auth disabled. The recommended Windows test server
uses the repository's existing Python environment and does not require Node:

```powershell
# Run from the window-control repository root.
uv run python .\engine\test\local_signaling_server.py
```

Keep that window running, then build and execute the live tests in another
window:

```powershell
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
```

The production Node relay remains available for environments with Node/npm:

```powershell
Set-Location infra\vps\signaling
Remove-Item Env:JWT_SECRET -ErrorAction SilentlyContinue
npm install
npm start
```
