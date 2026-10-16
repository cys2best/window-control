#!/bin/bash
# Deploy one release of the shared remote service on the instance (run by SSM as root).
# Usage: shared-remote-deploy.sh <git-sha> <image-tag> <account-id>
# Secrets go from SSM straight into 0600 files; never into arguments, logs or the environment.
set -euo pipefail
umask 077

if [ "$#" -ne 3 ] || [[ ! "$1" =~ ^[0-9a-f]{40}$ ]] || [[ ! "$2" =~ ^[0-9a-f]{40}$ ]] || [[ ! "$3" =~ ^[0-9]{12}$ ]]; then
  echo "usage: shared-remote-deploy.sh <40-hex git sha> <40-hex image tag> <12-digit account id>" >&2
  exit 2
fi
GIT_SHA=$1
IMAGE_TAG=$2
ACCOUNT_ID=$3

REGION=ap-southeast-1
# Created by the instance bootstrap (infra/aws/user-data.sh.tftpl).
DATA=/srv/shared-remote/data
RELEASES=/srv/shared-remote/releases
SECRETS=/srv/shared-remote/secrets
RELEASE=$RELEASES/$GIT_SHA
CERTS=$SECRETS/certs
ENV_FILE=$SECRETS/operator.env
REGISTRY=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../shared-remote" && pwd)

imds() {
  curl -fsS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" "http://169.254.169.254/latest/meta-data/$1"
}

# Replace atomically: a failed fetch must leave the previous value for the running stack and reboots.
secret_to_file() {
  local tmp
  tmp=$(mktemp "$2.XXXXXX")
  if ! aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text > "$tmp" || [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    echo "could not read SSM parameter $1" >&2
    return 1
  fi
  chmod 0600 "$tmp"
  mv -f "$tmp" "$2"
}

IMDS_TOKEN=$(curl -fsS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 300" http://169.254.169.254/latest/api/token)
PRIVATE_IP=$(imds local-ipv4)
DOMAIN=$(imds tags/instance/remote-hostname)
MAX_STREAMS=$(imds tags/instance/max-active-streams)
[[ "$PRIVATE_IP" =~ ^[0-9.]+$ ]] || { echo "unexpected private address" >&2; exit 1; }
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "unexpected remote-hostname tag" >&2; exit 1; }
[[ "$MAX_STREAMS" =~ ^[1-9][0-9]*$ ]] || { echo "unexpected max-active-streams tag" >&2; exit 1; }
for directory in "$DATA" "$RELEASES" "$SECRETS"; do
  [ -d "$directory" ] || { echo "missing $directory; bootstrap unfinished (re-run: sudo bash /var/lib/cloud/instance/user-data.txt)" >&2; exit 1; }
done
mountpoint -q "$DATA" || { echo "data volume is not mounted" >&2; exit 1; }

rm -rf "$RELEASE"
mkdir -p "$RELEASE"
cp -R "$SOURCE/." "$RELEASE/"
chmod -R go+rX "$RELEASE"

mkdir -p "$CERTS"
secret_to_file /window-control/shared-remote/cloudflare-turn-key-id "$SECRETS/cloudflare-turn-key-id"
secret_to_file /window-control/shared-remote/cloudflare-turn-api-token "$SECRETS/cloudflare-turn-api-token"
secret_to_file /window-control/shared-remote/origin-cert "$CERTS/edge-fullchain.pem"
secret_to_file /window-control/shared-remote/origin-key "$CERTS/edge-key.pem"

cat > "$ENV_FILE" <<ENV
REMOTE_HTTPS_BIND_ADDRESS=$PRIVATE_IP
REMOTE_DOMAIN=$DOMAIN
REMOTE_MAX_ACTIVE_STREAMS=$MAX_STREAMS
REMOTE_DATA_DIRECTORY=$DATA
REMOTE_CERT_DIRECTORY=$CERTS
REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE=$SECRETS/cloudflare-turn-key-id
REMOTE_CLOUDFLARE_TURN_TOKEN_FILE=$SECRETS/cloudflare-turn-api-token
REMOTE_BROKER_IMAGE=$REGISTRY/window-control/broker:$IMAGE_TAG
REMOTE_EDGE_IMAGE=$REGISTRY/window-control/edge:$IMAGE_TAG
ENV
chmod 0600 "$ENV_FILE"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

compose() {
  docker compose -p shared-remote --env-file "$ENV_FILE" -f "$RELEASE/compose.cloudflare.yml" "$@"
}

report_failure() {
  echo "Deploy failed: $1" >&2
  compose ps -a || true
  # Drop any log line that contains a secret value; patterns come from files, not arguments.
  compose logs --no-color --tail 50 preflight broker edge 2>&1 \
    | grep -v -F -f "$SECRETS/cloudflare-turn-key-id" \
    | grep -v -F -f "$SECRETS/cloudflare-turn-api-token" || true
  exit 1
}

compose pull || report_failure "image pull"
compose up -d --remove-orphans || report_failure "preflight or start"
PREFLIGHT_EXIT=$(docker inspect -f '{{.State.ExitCode}}' shared-remote-preflight-1 2>/dev/null || echo missing)
[ "$PREFLIGHT_EXIT" = 0 ] || report_failure "preflight exit $PREFLIGHT_EXIT"

# Origin CA is not in the system trust store; this probe checks routing, not the certificate.
for _ in $(seq 1 30); do
  STATUS=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 --resolve "$DOMAIN:443:$PRIVATE_IP" "https://$DOMAIN/installations" || true)
  if [ "$STATUS" = 405 ]; then
    echo "Deployed $GIT_SHA (images $IMAGE_TAG) to $DOMAIN"
    find "$RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name "$GIT_SHA" -printf '%T@ %p\n' \
      | sort -rn | tail -n +5 | cut -d' ' -f2- | xargs -r rm -rf
    exit 0
  fi
  sleep 2
done
report_failure "health check did not return 405 within 60 s (last: ${STATUS:-none})"
