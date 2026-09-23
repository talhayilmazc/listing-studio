#!/usr/bin/env bash
# Print fresh production secrets (production-spec F3). Run on the server:
#
#   deploy/generate-secrets.sh
#
# Paste the output into /opt/listyro/.env. Nothing is written to disk here.
# Only openssl is needed, which Ubuntu ships.
set -euo pipefail

rand_hex() { openssl rand -hex "$1"; }

# A Fernet key is 32 random bytes, url-safe base64 encoded (padding kept).
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }

pg_password="$(rand_hex 32)"  # hex: no character needs escaping in DATABASE_URL

cat <<EOF
POSTGRES_PASSWORD=${pg_password}
DATABASE_URL=postgresql+asyncpg://listyro:${pg_password}@postgres:5432/listyro
ENCRYPTION_KEY=$(fernet_key)
ADMIN_TOKEN=$(rand_hex 32)
SECRET_KEY=$(rand_hex 32)
EOF

cat >&2 <<'EOF'

Back up ENCRYPTION_KEY now, somewhere other than this server and other than
the backup location (a password manager). Without it, every seller's Etsy
connection is lost; stored next to the backups, it unlocks them.
EOF
