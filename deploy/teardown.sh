#!/usr/bin/env bash
# Removes the demo from the server. Run as root. Leaves nginx and its other sites untouched.
set -euo pipefail
systemctl disable --now taste 2>/dev/null || true
rm -f /etc/systemd/system/taste.service /etc/taste.env /etc/nginx/getfletcher.htpasswd
rm -f /etc/nginx/sites-enabled/getfletcher.ai /etc/nginx/sites-available/getfletcher.ai
systemctl daemon-reload
nginx -t && systemctl reload nginx
gpasswd -d www-data taste 2>/dev/null || true
userdel --remove taste 2>/dev/null || true
# The deploy runner, if runner-setup.sh was used. Remove its entry on GitHub too (repo settings, Actions, Runners).
if [ -d /opt/ghrunner ]; then
    (cd /opt/ghrunner && ./svc.sh stop && ./svc.sh uninstall) 2>/dev/null || true
    rm -f /etc/sudoers.d/taste-deploy /usr/local/sbin/taste-deploy
    userdel --remove ghrunner 2>/dev/null || true
fi
echo "Removed. A certificate for getfletcher.ai, if issued, can be deleted with: certbot delete --cert-name getfletcher.ai"
