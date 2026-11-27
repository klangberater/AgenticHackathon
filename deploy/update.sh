#!/usr/bin/env bash
# Ship the current checkout to a server already set up with setup.sh. Run from the repo on your machine:
#   bash deploy/update.sh root@nebula-new
# Copies the code and the service unit, then restarts the service. No secrets needed; the password,
# token, nginx site and certificate on the server are left as they are. Running checks are cut off.
set -euo pipefail
HOST="${1:?usage: bash deploy/update.sh <ssh login>}"
cd "$(dirname "$0")/.."
python3 -c "import taste.cli, taste.server"   # do not ship code that does not even import
rsync -a --delete --exclude __pycache__ taste web deploy "$HOST:/root/taste-src/"
ssh "$HOST" 'set -e
  rsync -a --delete /root/taste-src/taste /root/taste-src/web /opt/taste/app/
  chown -R taste:taste /opt/taste/app
  cp /root/taste-src/deploy/taste.service /etc/systemd/system/taste.service
  systemctl daemon-reload
  systemctl restart taste
  sleep 2
  systemctl is-active taste'
echo "Deployed $(git rev-parse --short HEAD) to $HOST"
