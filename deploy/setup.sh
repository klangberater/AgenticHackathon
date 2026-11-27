#!/usr/bin/env bash
# One-time setup on the server (Ubuntu with nginx). Run as root from the uploaded checkout:
#   SITE_USER=demo SITE_PASSWORD='...' CLAUDE_CODE_OAUTH_TOKEN='...' bash deploy/setup.sh
# Re-running it updates the code and restarts the service. Remove everything with deploy/teardown.sh.
set -euo pipefail
: "${SITE_USER:?set SITE_USER}" "${SITE_PASSWORD:?set SITE_PASSWORD}" "${CLAUDE_CODE_OAUTH_TOKEN:?set CLAUDE_CODE_OAUTH_TOKEN}"
cd "$(dirname "$0")/.."

id taste >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/taste --shell /usr/sbin/nologin taste
install -d -o taste -g taste /opt/taste/app
cp -r taste web /opt/taste/app/
chown -R taste:taste /opt/taste/app

# Claude Code CLI and a headless browser, both inside the service user's home.
sudo -u taste -H bash -c 'command -v ~/.local/bin/claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash'
apt-get install -y python3-venv >/dev/null
sudo -u taste -H bash -c 'test -d ~/pw || python3 -m venv ~/pw; ~/pw/bin/pip -q install playwright'
/opt/taste/pw/bin/playwright install-deps chromium >/dev/null
sudo -u taste -H /opt/taste/pw/bin/playwright install chromium-headless-shell

umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$CLAUDE_CODE_OAUTH_TOKEN" > /etc/taste.env
printf '%s:%s\n' "$SITE_USER" "$(openssl passwd -apr1 "$SITE_PASSWORD")" > /etc/nginx/getfletcher.htpasswd
chown root:www-data /etc/nginx/getfletcher.htpasswd && chmod 640 /etc/nginx/getfletcher.htpasswd
umask 022

cp deploy/taste.service /etc/systemd/system/taste.service
systemctl daemon-reload
systemctl enable --now taste
systemctl restart taste
usermod -aG taste www-data            # lets nginx open the service's socket

# Keep our own site file (certbot edits it in place); move anyone else's out of the way first.
SITE=/etc/nginx/sites-available/getfletcher.ai
if [ -e "$SITE" ] && ! grep -q "vendor-check service" "$SITE"; then
    mv "$SITE" "/root/getfletcher.ai.nginx.$(date +%s).old"
fi
test -e "$SITE" || cp deploy/getfletcher.ai.nginx "$SITE"
ln -sf /etc/nginx/sites-available/getfletcher.ai /etc/nginx/sites-enabled/getfletcher.ai
nginx -t
systemctl restart nginx
echo "Service is up. For HTTPS run:  certbot --nginx -d getfletcher.ai -d www.getfletcher.ai"
