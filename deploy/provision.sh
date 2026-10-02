#!/usr/bin/env bash
# One-time server hardening and setup for Ubuntu 24.04 (production-spec F1).
#
#   sudo deploy/provision.sh
#
# Run as your own sudo-capable user, logged in with an SSH key. Idempotent: safe
# to run again. What it does:
#   * installs Docker Engine + Compose from Docker's own repository, and rclone
#   * firewall: deny everything inbound except SSH (the tunnel dials OUT, so
#     neither 80 nor 443 needs to be open)
#   * SSH: keys only, no passwords, no root login — after checking that your key
#     is in place, so this cannot lock you out
#   * automatic security updates
#   * directories for the app, backups and tunnel credentials
#   * vm.overcommit_memory = 1, which Redis needs to save in the background
#   * cron: nightly backup, hourly disk check, weekly build-cache prune
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2
  exit 1
fi
OPERATOR="${SUDO_USER:-}"
if [ -z "$OPERATOR" ] || [ "$OPERATOR" = "root" ]; then
  echo "Run as your own user with sudo, not as root: SSH will stop accepting root." >&2
  exit 1
fi

. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
  echo "warning: written for Ubuntu 24.04, found ${PRETTY_NAME:-unknown}" >&2
fi

APP_DIR=/opt/listyro
BACKUP_DIR=/var/backups/listyro
CREDS_DIR=/etc/listyro/cloudflared
step() { printf '\n== %s\n' "$*"; }

# --- Lockout guard: prove key login works before turning passwords off --------
OPERATOR_HOME="$(getent passwd "$OPERATOR" | cut -d: -f6)"
KEYS="$OPERATOR_HOME/.ssh/authorized_keys"
if [ ! -s "$KEYS" ] || ! grep -qE '^(ssh-|ecdsa-|sk-)' "$KEYS"; then
  echo "No SSH public key in $KEYS." >&2
  echo "Add one and log in with it before running this; otherwise disabling" >&2
  echo "password login would lock you out." >&2
  exit 1
fi

step "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl gnupg ufw unattended-upgrades rclone

step "docker (official repository)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
usermod -aG docker "$OPERATOR"

step "firewall: SSH only"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
# Note: Docker writes its own iptables rules for *published* ports, bypassing
# ufw. docker-compose.prod.yml publishes none, which is what keeps this true.

step "ssh: keys only, no root"
cat > /etc/ssh/sshd_config.d/60-listyro.conf <<'EOF'
# production-spec F1
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sshd -t  # refuse to reload a broken configuration
systemctl reload ssh

step "automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades

step "directories"
install -d -m 0755 -o "$OPERATOR" -g "$OPERATOR" "$APP_DIR"
install -d -m 0700 -o root -g root "$BACKUP_DIR"
# Owned by the cloudflared container's user (the image runs as 65532, not root);
# a root-only directory would leave the tunnel unable to read its credentials.
install -d -m 0700 -o 65532 -g 65532 "$CREDS_DIR"

step "kernel: let Redis save in the background"
# Without this a background save can fail under memory pressure ("Can't save in
# background: fork: Cannot allocate memory"), and Redis warns about it at start.
cat > /etc/sysctl.d/60-listyro.conf <<'EOF'
vm.overcommit_memory = 1
EOF
sysctl -q -p /etc/sysctl.d/60-listyro.conf

step "cron: nightly backup, hourly disk check, weekly build-cache prune"
# Each script is run through bash, so a checkout that loses the executable bit
# does not silently stop the backups.
cat > /etc/cron.d/listyro <<EOF
# production-spec F5 / F6. Output goes to syslog via logger.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
15 3 * * * root cd $APP_DIR && bash deploy/backup.sh 2>&1 | logger -t listyro-backup
5 * * * *  root cd $APP_DIR && bash deploy/disk-check.sh 2>&1 | logger -t listyro-disk
# Build cache from every deploy accumulates until the disk is full; a week is plenty.
30 4 * * 0 root docker builder prune -f --filter until=168h 2>&1 | logger -t listyro-prune
EOF
chmod 0644 /etc/cron.d/listyro

step "done"
cat <<EOF
Next (docs/deploy.md):
  1. log out and back in, so '$OPERATOR' can use docker without sudo
  2. put the code in $APP_DIR and create $APP_DIR/.env
  3. create the tunnel; its credentials go in $CREDS_DIR
  4. set ALERT_WEBHOOK_URL in /etc/listyro/ops.env (without it no alert reaches anyone)
  5. run deploy/preflight.sh, then start the stack; later updates: bash deploy/update.sh
EOF
