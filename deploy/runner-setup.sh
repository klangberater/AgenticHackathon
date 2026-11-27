#!/usr/bin/env bash
# One-time: put a GitHub Actions runner on the server so pushes to main deploy themselves.
# Run as root on the server, after setup.sh, from the uploaded checkout:
#   RUNNER_TOKEN='...' bash deploy/runner-setup.sh
# Get the token (valid one hour) on your own machine with:
#   gh api -X POST repos/klangberater/AgenticHackathon/actions/runners/registration-token -q .token
set -euo pipefail
: "${RUNNER_TOKEN:?set RUNNER_TOKEN}"
REPO_URL="https://github.com/klangberater/AgenticHackathon"

# The only thing the runner may do as root: copy the app's two directories and restart the service.
cat > /usr/local/sbin/taste-deploy <<'DEPLOY'
#!/usr/bin/env bash
set -euo pipefail
SRC="$(realpath "${1:?usage: taste-deploy <checkout dir>}")"
[ -d "$SRC/taste" ] && [ -d "$SRC/web" ] || { echo "not a checkout: $SRC" >&2; exit 1; }
rsync -a --delete --no-links --exclude __pycache__ "$SRC/taste" "$SRC/web" /opt/taste/app/
chown -R taste:taste /opt/taste/app
systemctl restart taste
sleep 2
systemctl is-active taste
DEPLOY
chmod 755 /usr/local/sbin/taste-deploy
echo 'ghrunner ALL=(root) NOPASSWD: /usr/local/sbin/taste-deploy' > /etc/sudoers.d/taste-deploy
chmod 440 /etc/sudoers.d/taste-deploy
visudo -cf /etc/sudoers.d/taste-deploy

id ghrunner >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/ghrunner --shell /bin/bash ghrunner
cd /opt/ghrunner
if [ ! -x ./config.sh ]; then
    VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
    sudo -u ghrunner curl -fsSL -o runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${VERSION}/actions-runner-linux-x64-${VERSION}.tar.gz"
    sudo -u ghrunner tar xzf runner.tar.gz && rm runner.tar.gz
fi
./bin/installdependencies.sh >/dev/null
sudo -u ghrunner ./config.sh --unattended --replace --url "$REPO_URL" --token "$RUNNER_TOKEN" \
    --name "getfletcher-$(hostname)" --labels taste-deploy
./svc.sh install ghrunner
./svc.sh start
./svc.sh status | head -5
echo "Runner registered. Check it under $REPO_URL/settings/actions/runners"
