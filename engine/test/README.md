# engine/test/README.md

`SignalingClient` tests require a running signaling server at
`ws://localhost:8443` with auth disabled:

    cd infra/vps/signaling
    npm install
    npm start   # JWT_SECRET unset -> auth disabled, matches test expectations
