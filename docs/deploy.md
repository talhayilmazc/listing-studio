# Deploying Listyro

The production runbook (production-spec F, G and H). Follow it top to bottom for
the first deploy. Later updates need only [Updating](#updating).

Shape of the thing: one Ubuntu 24.04 server in Türkiye (the Privacy Policy says
so), Docker Compose, and a Cloudflare Tunnel that dials out to Cloudflare. No port
but SSH is open. `listyro.com` serves the app; `api.listyro.com` exists only for
Etsy's OAuth callback and the uptime check.

## What you need before starting

- A fresh Ubuntu 24.04 server **located in Türkiye**. Log in as a sudo-capable
  user with an SSH key.
- The domain `listyro.com` with DNS on Cloudflare.
- The Etsy app keystring and shared secret (Personal App).
- An Anthropic API key.
- The operator details that the Terms and Privacy Policy print: legal name,
  location, governing law, dispute venue and a support address. **The app will
  not start in production without them.**
- A password manager. `ENCRYPTION_KEY` and the backup encryption passwords go in
  it and nowhere else.

## 1. Provision the server

```bash
git clone <repo> /tmp/listyro && sudo /tmp/listyro/deploy/provision.sh
```

This sets up:
- Docker and rclone
- `ufw` allowing SSH only
- key-only SSH with no root login (it checks for your key first, so it cannot lock
  you out)
- unattended security upgrades
- the directories `/opt/listyro`, `/var/backups/listyro` and
  `/etc/listyro/cloudflared`
- cron jobs for the nightly backup and the hourly disk check

It is idempotent. Log out and back in afterwards so that your user can run
`docker` without sudo.

## 2. Code and `.env`

```bash
git clone <repo> /opt/listyro && cd /opt/listyro
cp deploy/env.production.example .env && chmod 600 .env
deploy/generate-secrets.sh        # prints fresh values; nothing is written
```

Fill in `.env`:

- **Secrets.** Paste the values that `generate-secrets.sh` printed. Never copy any
  value from the development `.env`. `ADMIN_TOKEN=e2e-admin-token` and friends are
  refused by the preflight check.
- **`DATABASE_URL`.** Put the same `POSTGRES_PASSWORD` into it.
- **`ENCRYPTION_KEY`.** Store it in the password manager now, before anything is
  encrypted with it. If it is lost, every seller has to reconnect their shop. If
  it is stored beside the backups, a stolen backup exposes every Etsy token.
- **Etsy, Anthropic and the five operator fields.** `SUPPORT_EMAIL` appears on
  every page of the app and in both legal documents, so use an address you are
  happy to publish.
- **`GLOBAL_DAILY_LIMIT=5000`.** This is Etsy's app-wide ceiling, and the Admin →
  Usage screen measures against it. `TENANT_DAILY_QUOTA=1000` is the default
  ceiling per seller; you can change it per seller in Admin → Users.

## 3. Cloudflare Tunnel

Stop the development tunnel first. Delete any DNS records it created for
`listyro.com` and `api.listyro.com` in the Cloudflare dashboard (H: "the tunnel
on the development machine is shut down"). `route dns` below refuses to replace
existing records.

These commands run the same pinned `cloudflared` image as production. The
credentials land in `/etc/listyro/cloudflared`, which belongs to the container's
user (uid 65532):

```bash
cf() { sudo docker run --rm -it -v /etc/listyro/cloudflared:/home/nonroot/.cloudflared \
         cloudflare/cloudflared:2026.9.1 "$@"; }
cf tunnel login                          # open the printed URL, pick listyro.com
cf tunnel create listyro                 # prints the tunnel id
cf tunnel route dns listyro listyro.com
cf tunnel route dns listyro api.listyro.com
sudo rm /etc/listyro/cloudflared/cert.pem   # account-wide credential; not needed to run
```

Then replace both `<TUNNEL_ID>` placeholders in `deploy/cloudflared/config.yml`
with the printed id. The ingress rules in that file are the whole edge policy:
- `listyro.com` goes to the frontend.
- On `listyro.com`, `/api/account/admin/*` (the bootstrap token endpoints)
  returns 404.
- On `api.listyro.com`, only the OAuth callback and `/health` reach the backend.
  Everything else returns 404.

## 4. Etsy Developer Portal (F4)

Add the production callback to the app's registered redirect URIs, exactly:

```
https://api.listyro.com/api/auth/etsy/callback
```

Keep `http://localhost:8000/api/auth/etsy/callback` only while you still develop
locally.

## 5. Preflight and first start

```bash
sudo deploy/preflight.sh      # sudo: the tunnel credentials are only visible to root
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps     # every service "healthy"
```

Preflight prints which setting is wrong, never its value. Fix everything it
reports as `FAIL` before starting. The API container runs `alembic upgrade head`
on every start, so a fresh database gets its schema on this first start.

Check the edge from outside the server:

```bash
curl -s https://api.listyro.com/health                     # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' https://api.listyro.com/api/batches   # 404
curl -s -o /dev/null -w '%{http_code}\n' https://listyro.com/api/account/admin/invites  # 404
```

## 6. The first admin

```bash
deploy/admin.sh create-admin you@example.com
```

The password is prompted twice inside the container, never passed as an
argument. It must meet the same 12-character policy as every account. Then sign
in at `https://listyro.com/login` with the normal form. **Admin** appears in the
left rail. From now on, everything goes through that signed-in session:

- **Users:**
  - suspend or reactivate an account
  - issue a temporary password, which forces a change at the next sign-in
  - change a seller's daily quota ceiling
- **Invites:**
  - generate codes, optionally bound to an email address, with an expiry
  - each code is shown once, with a copy button
  - revoke unused codes
- **Usage:** the app-wide Etsy budget, per seller, over 7 days.

Once an admin exists, the `ADMIN_TOKEN` endpoints answer 404. They were only a
bootstrap fallback. `deploy/admin.sh demote-admin` and `list-admins` are the only
other role commands. It is impossible to demote the last admin.

Every admin action is written to the `audit_log` table: who, what, when and
against whom, stored as ids and never as email addresses. There is no screen for
it yet. Read it on the server:

```bash
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U listyro listyro -c "select created_at, action, actor_tenant_id, target_tenant_id, details from audit_log order by created_at desc limit 50"
```

## 7. Backups (F5)

Cron runs `deploy/backup.sh` at 03:15 every night:
- a verified `pg_dump` every night
- an archive of the upload directory every week
- 14 days kept

Off-server copies and alerts are configured in `/etc/listyro/ops.env`. It is
owned by root with mode 600, because cron starts with an empty environment:

```bash
BACKUP_REMOTE=offsite:listyro
HEALTHCHECK_URL=https://hc-ping.com/<uuid>
ALERT_WEBHOOK_URL=https://ntfy.sh/<private-topic>
DISK_ALERT_PERCENT=80
```

`BACKUP_REMOTE` must be an rclone **crypt** remote. The dumps contain every
seller's email address and encrypted tokens. Set it up as root, since root runs
the cron job:

```bash
sudo rclone config     # 1) a storage remote (R2, B2, S3...)  2) a "crypt" remote named offsite on top of it
```

Put both crypt passwords in the password manager. Without them the off-site
copies cannot be read.

Then prove the backup restores. This is H: "restore tested once", done on this
server, against real data:

```bash
sudo deploy/backup.sh
sudo deploy/restore-test.sh    # throwaway postgres, never production; compares every table
```

`deploy/restore.sh` performs a real restore. It destroys the current database and
refuses to run without `--i-understand-this-replaces-production`.

## 8. Monitoring (F6)

- **Containers.** Every service restarts automatically (`restart: unless-stopped`).
  Every service except the tunnel also has a health check.
- **Uptime.** Point an external monitor (UptimeRobot, healthchecks.io, and the
  like) at `https://api.listyro.com/health` every 5 minutes.
- **Disk.** Cron runs `deploy/disk-check.sh` hourly and posts to
  `ALERT_WEBHOOK_URL` above `DISK_ALERT_PERCENT`.
- **Backups.** A missed or failed night shows up at `HEALTHCHECK_URL`.
- **Errors (optional).** Set `SENTRY_DSN`. Reports are scrubbed of emails,
  cookies, tokens and request bodies. The Privacy Policy starts naming Sentry
  automatically once it is set.

## 9. Data: start clean (G)

Production starts with **empty volumes**. Nothing from development is carried
over: no database, no uploads, no Redis, no `.env`.

- Your cousin registers again with an invite code and reconnects their shop.
  Their development batches, profiles and generated content are not migrated.
  Reference profiles are rebuilt from their own Etsy listings.
- On the development machine, once production works:
  - Disconnect the shop in the app. This deletes the stored Etsy tokens and all
    Etsy-sourced content.
  - Or wipe development entirely with `docker compose down -v`.

**Five shops, total.** The Personal App can connect at most five shops,
including your own if your admin account connects one. A sixth seller, or any
charge, needs Commercial Access first (CLAUDE.md).

## Updating

```bash
cd /opt/listyro
sudo deploy/backup.sh                                   # before every update
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

- Migrations run when the API starts.
- `up --build` recreates the worker along with everything else. This matters:
  the worker has no reload, and an old worker silently runs old job code.
- If you bump the pinned `cloudflared` image, update it in both
  `docker-compose.prod.yml` and the `cf` helper above.

## Launch checklist (H)

| | Item | How |
|---|---|---|
| ☐ | B6 isolation tests pass | `docker compose exec api python -m pytest -q` |
| ☐ | No endpoint answers without a session | covered by the suite; spot-check with the step 5 `curl`s |
| ☐ | Dev-tenant code removed | done in step B; production has only invited accounts |
| ☐ | Terms and Privacy Policy are real text | `/terms` and `/privacy` show your operator details, no blanks |
| ☐ | Trademark notice, support email, quota indicator, Etsy back-links | visible in the footer, rail and listing cards |
| ☐ | `ENCRYPTION_KEY` backed up | in the password manager, not on the server |
| ☐ | Restore tested once | `sudo deploy/restore-test.sh` passes on this server |
| ☐ | Invite codes issued | Admin → Invites, one per beta seller, bound to their email |
| ☐ | End-to-end flow tested in production | see below |
| ☐ | Development tunnel shut down | step 3 |

End to end, once, as a real seller:
1. Register with an invite.
2. Connect the shop.
3. Detect or add a profile.
4. Upload a folder.
5. Generate content.
6. Create a draft.
7. Open it in Shop Manager.
8. Publish it only after approving it.
9. Disconnect, and confirm the shop's content is gone.
