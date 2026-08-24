# infra/vps/signaling/README.md

## Deploy (Ubuntu 22.04/24.04)

    sudo apt update
    sudo apt install -y nodejs npm
    sudo useradd -r -s /bin/false webrtc
    sudo mkdir -p /opt/webrtc-signaling
    sudo cp server.js package.json /opt/webrtc-signaling/
    cd /opt/webrtc-signaling && sudo npm install --production
    sudo chown -R webrtc:webrtc /opt/webrtc-signaling

## Configure

    echo "PORT=8443" | sudo tee /opt/webrtc-signaling/.env
    echo "JWT_SECRET=$(openssl rand -hex 32)" | sudo tee -a /opt/webrtc-signaling/.env
    sudo chmod 600 /opt/webrtc-signaling/.env
    sudo chown webrtc:webrtc /opt/webrtc-signaling/.env

Save the generated `JWT_SECRET` value — Task 7's test-page setup needs it to
mint a matching test token.

## Install and start

    sudo cp webrtc-signaling.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable webrtc-signaling
    sudo systemctl start webrtc-signaling
    sudo systemctl status webrtc-signaling

**Important:** Before starting the service, verify the node binary location. Run:

    which node

If the output is `/usr/bin/node`, the systemd unit's `ExecStart` path is correct.
If node is installed elsewhere (e.g., via nvm or a custom PATH), edit
`/etc/systemd/system/webrtc-signaling.service` and update the `ExecStart` line
to point to the correct node binary path, then run `sudo systemctl daemon-reload`.

## Firewall

    sudo ufw allow 8443/tcp

## Verify

    sudo journalctl -u webrtc-signaling -n 50 --no-pager

Expect: `Signaling server listening on port 8443`, no `WARNING: JWT_SECRET
not set`.

Note: for the PoC this runs plain `ws://` on 8443. TLS termination
(`wss://`, required for browsers on non-localhost origins to allow camera/mic
— not needed here since we're not using getUserMedia, but required for
`RTCPeerConnection` to be usable from a page served over `https://`) is a
Phase 4+ concern once a real domain is in play. For this plan's test page,
either serve `test_page.html` from `file://` (browsers allow WS to
non-secure origins from `file://` pages) or from `http://` on the same VPS.
