# engine/test/README.md

`SignalingClient` tests require the repository's Node relay at
`ws://localhost:8443` and `wss://localhost:8444`, with auth disabled. The
secure tests trust only the checked-in test CA through OpenSSL's normal
`SSL_CERT_FILE` lookup; they never disable peer or hostname verification and
do not install that CA into Windows:

```powershell
Set-Location infra\vps\signaling
$repoRoot = (Resolve-Path ..\..\..).Path
$env:JWT_SECRET = ""
$env:SIGNALING_TLS_CERT_FILE = Join-Path $repoRoot "engine\test\tls\localhost-cert.pem"
$env:SIGNALING_TLS_KEY_FILE = Join-Path $repoRoot "engine\test\tls\localhost-key.pem"
$env:SIGNALING_TLS_PORT = "8444"
npm install
npm start
```

Keep that window running, then build and execute the live tests in another
window:

```powershell
$env:SSL_CERT_FILE = (Resolve-Path "engine\test\tls\ca-cert.pem").Path
$env:ENGINE_TEST_WSS_PORT = "8444"
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
```

The secure exchange test proves a verified WSS handshake and message relay.
The hostname-mismatch test connects to the same trusted certificate through
`127.0.0.1` and must remain disconnected because the certificate is valid
only for `localhost`. Production WSS additionally imports the Windows current
user and local machine ROOT stores; the test CA is not packaged.
