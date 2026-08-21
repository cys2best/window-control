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

Order matters here: nginx refuses to load a `listen 443 ssl` block that has
no certificate configured, so the port-443 block must not exist until certbot
has created it. Follow these steps in sequence.

**1. Write only the port-80 block** to
`/etc/nginx/sites-available/webrtc-tunnel`:

    server {
        listen 80;
        server_name tunnel.example.com;
        location /.well-known/acme-challenge/ { root /var/www/certbot; }
        location / { return 301 https://$host$request_uri; }
    }

**2. Enable it and reload** (this must pass before certbot runs):

    sudo mkdir -p /var/www/certbot
    sudo ln -s /etc/nginx/sites-available/webrtc-tunnel /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx

**3. Get the certificate.** certbot writes a working
`server { listen 443 ssl; ... }` block into this same file, with real
`ssl_certificate`/`ssl_certificate_key` paths, and sets up auto-renewal
(`certbot renew` via its own systemd timer — no manual cron needed):

    sudo certbot --nginx -d tunnel.example.com

**4. Add the proxy directives to the 443 block certbot just created.** Edit
`/etc/nginx/sites-available/webrtc-tunnel` — do NOT recreate the file, or you
lose certbot's `ssl_certificate` lines — and put this `location` block inside
the `server { listen 443 ssl; ... }` block, replacing whatever placeholder
`location /` certbot left there:

        location / {
            proxy_pass http://127.0.0.1:8444;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_read_timeout 3600s;
        }

The `Upgrade`/`Connection` headers are what let the browser's `/input`
WebSocket through; `proxy_read_timeout 3600s` keeps nginx from cutting an
idle control connection.

**5. Validate and reload again:**

    sudo nginx -t && sudo systemctl reload nginx

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

`AUTH_TOKEN` is mandatory whenever `PUBLIC_UI_URL` is set — the app refuses
to start otherwise (`create_app()` raises), and so does the VPS server
without its `TUNNEL_SECRET`.

### `COOKIE_SECURE` — optional hardening, with a real tradeoff

`COOKIE_SECURE` is **not required** for the public deployment to work, and
leaving it unset is the right default if you also use Tailscale/LAN access.

**⚠️ Setting `COOKIE_SECURE=true` breaks LAN/Tailscale login.** With it on,
the session cookie is marked `Secure`, so a browser will only store and send
it over HTTPS. A user who visits `http://<tailscale-ip>:8080` still sees the
login overlay and still gets `200 OK` from `POST /login`, but the browser
silently discards the cookie — the next request is unauthenticated and the
login overlay comes straight back, with no error message anywhere. There is
no way to tell from the browser that this is what happened.

Guidance:

- **Want both public and Tailscale/LAN access (the normal case)?** Leave
  `COOKIE_SECURE` unset. A cookie without the `Secure` flag is still sent
  over HTTPS — `Secure` only *restricts* it to HTTPS, it is not needed for
  HTTPS to work. So the public tunnel path works exactly the same either way.
  What you give up is hardening against a hypothetical plaintext hop leaking
  the cookie; on the tunnel path there is no plaintext hop (browser→nginx is
  HTTPS, nginx→tunnel and tunnel→PC are the PC's own outbound WSS link).
- **Public access only, with LAN/Tailscale access unused?** Set
  `COOKIE_SECURE=true` for the extra hardening — and remember that plain-HTTP
  access to `:8080` will no longer be able to log in.

Whichever you choose, test both access paths after changing it: the failure
mode is invisible unless you actually try to log in over plain HTTP.

## Verify

    sudo journalctl -u webrtc-tunnel -n 50 --no-pager

Expect: `Tunnel server listening on port 8444`. If you instead see
`FATAL: TUNNEL_SECRET is not set` and the unit in a restart loop, the `.env`
file wasn't read or the variable is empty — the server refuses to run
unauthenticated, because any client could otherwise register as "the PC" and
harvest `AUTH_TOKEN` at the operator's next login.

From a browser on a network that is neither Tailscale nor the PC's LAN
(e.g. mobile data): open `https://tunnel.example.com`, confirm the login
overlay appears, log in with `AUTH_TOKEN`, confirm the window list loads
and mouse/keyboard control works.

Then re-check the LAN/Tailscale path (`http://<tailscale-ip>:8080`) and log
in there too — see the `COOKIE_SECURE` note above for the one setting that
silently breaks it.

### Known limitation: no MJPEG fallback over the tunnel

`GET /stream` (the `multipart/x-mixed-replace` MJPEG fallback the client uses
when WebRTC negotiation fails) is **not** forwarded through the tunnel. The
PC-side forwarder buffers each response in full before wrapping it in one
JSON envelope, and an MJPEG stream never ends — forwarding it would grow in
the PC's RAM until the process died. Requests for `/stream` are answered with
`501 Not Implemented` instead (`src/server/http_tunnel.py`,
`_UNSUPPORTED_STREAMING_PATHS`).

Practical consequence: over the public tunnel, video works only via WebRTC.
If WebRTC negotiation fails, the client keeps retrying `/stream` in the
background (cheap: each attempt is an immediate 501) and the video area stays
blank — the window list and mouse/keyboard control still work normally. If
you see that, the thing to fix is the WebRTC signaling path
(`VPS_SIGNALING_URL` / `infra/vps/signaling/`), which is a separate service
from this tunnel; the tunnel itself is working.
