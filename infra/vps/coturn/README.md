# infra/vps/coturn/README.md

## Install (Ubuntu 22.04/24.04)

    sudo apt update
    sudo apt install -y coturn

## Deploy config

    sudo cp turnserver.conf /etc/turnserver.conf
    sudo sed -i 's/<VPS_PUBLIC_IP>/YOUR_ACTUAL_IP/' /etc/turnserver.conf

## Enable and start

    sudo sed -i 's/#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
    sudo systemctl enable coturn
    sudo systemctl restart coturn
    sudo systemctl status coturn

## Firewall

    sudo ufw allow 3478/udp
    sudo ufw allow 3478/tcp
    sudo ufw allow 5349/tcp
    sudo ufw allow 49160:49200/udp

## Verify

    sudo journalctl -u coturn -n 50 --no-pager

Expect log lines showing the listener bound on port 3478 with no bind
errors. Test STUN binding from any machine with `stunclient` or the
`test/test_page.html` from Task 6 (its ICE candidate gathering log will show
a `srflx` candidate if STUN works, and a `relay` candidate if TURN works).
