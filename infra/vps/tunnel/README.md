# infra/vps/tunnel/README.md

## Deploy (Ubuntu 22.04/24.04)

    sudo apt update
    sudo apt install -y nodejs npm nginx certbot python3-certbot-nginx
    sudo useradd -r -s /bin/false webrtc-tunnel
    sudo mkdir -p /opt/webrtc-tunnel
    sudo cp server.js package.json /opt/webrtc-tunnel/
    cd /opt/webrtc-tunnel && sudo npm install --production
    sudo chown -R webrtc-tunnel:webrtc-tunnel /opt/webrtc-tunnel

## Configure

    echo "PORT=8444" | sudo tee /opt/webrtc-tunnel/.env
    echo "TUNNEL_SECRET=$(openssl rand -hex 32)" | sudo tee -a /opt/webrtc-tunnel/.env
    sudo chmod 600 /opt/webrtc-tunnel/.env
    sudo chown webrtc-tunnel:webrtc-tunnel /opt/webrtc-tunnel/.env

Save the generated `TUNNEL_SECRET` — the PC's `TUNNEL_SECRET` env var (Task
6) must match it exactly.

## Install and start

    sudo cp webrtc-tunnel.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable webrtc-tunnel
    sudo systemctl start webrtc-tunnel
    sudo systemctl status webrtc-tunnel

**Important:** Before starting the service, verify the node binary
location the same way as `infra/vps/signaling/README.md` describes
(`which node`, update `ExecStart` if it's not `/usr/bin/node`).

## DNS

Point an A record for your domain (e.g. `tunnel.example.com`) at the VPS's
public IP. Confirm propagation before running certbot:

    dig +short tunnel.example.com

## TLS (nginx + certbot)

Create `/etc/nginx/sites-available/webrtc-tunnel`:

    server {
        listen 80;
        server_name tunnel.example.com;
        location /.well-known/acme-challenge/ { root /var/www/certbot; }
        location / { return 301 https://$host$request_uri; }
    }

    server {
        listen 443 ssl;
        server_name tunnel.example.com;

        location / {
            proxy_pass http://127.0.0.1:8444;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_read_timeout 3600s;
        }
    }

Enable it and get the cert:

    sudo mkdir -p /var/www/certbot
    sudo ln -s /etc/nginx/sites-available/webrtc-tunnel /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo certbot --nginx -d tunnel.example.com

certbot rewrites the `server { listen 443 ssl; ... }` block in place to add
`ssl_certificate`/`ssl_certificate_key` lines and sets up auto-renewal
(`certbot renew` via its own systemd timer — no manual cron needed).

## Firewall

    sudo ufw allow 443/tcp
    sudo ufw allow 80/tcp

(`8444` stays internal — only nginx on the host talks to it, over
`127.0.0.1`; it should NOT be opened on the security group. Compare with
`infra/terraform/main.tf`, which only opens `443`/`80`, not `8444`.)

## PC-side configuration

On the Windows PC, set before starting the app:

    $env:PUBLIC_UI_URL = "wss://tunnel.example.com/__tunnel/register"
    $env:TUNNEL_SECRET = "<the value generated above>"
    $env:AUTH_TOKEN = "<a separate, strong shared secret for browser login>"
    $env:COOKIE_SECURE = "true"

`COOKIE_SECURE=true` is required here (unlike the LAN-only case) — the
session cookie is set by a response that ultimately reaches the browser
over HTTPS (via the VPS), so marking it Secure is safe and is the
correct hardening default for a public deployment.

## Verify

    sudo journalctl -u webrtc-tunnel -n 50 --no-pager

Expect: `Tunnel server listening on port 8444`, no `WARNING: TUNNEL_SECRET
not set`.

From a browser on a network that is neither Tailscale nor the PC's LAN
(e.g. mobile data): open `https://tunnel.example.com`, confirm the login
overlay appears, log in with `AUTH_TOKEN`, confirm the window list loads
and mouse/keyboard control works.
