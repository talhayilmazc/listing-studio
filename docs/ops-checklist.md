# Operations checklist: what to run on the server yourself

This is the server work the October 2026 quality pass found (`docs/qa-2026-10.md`). Nothing here was run on the server. Do the steps in order; each says what you should see. Everything is run on the production VPS as the operator user, with `sudo` where shown, from the app folder (`cd /opt/listyro` or wherever `APP_DIR` is).

## 1. Deploy this release

```bash
bash deploy/update.sh
```

What it does: it takes a verified backup, pulls, builds, runs two new migrations and waits until every service is healthy.

- `0088_fk_indexes`: adds an index on 42 foreign-key columns. It's quick at today's size and changes no rows.
- `0089_profile_merge_scrub`: removes the Etsy listing content that migration 0046 kept inside `profile_merge`, and deletes that table's orphan rows.

You should see each step pass and preflight end with "Ready, with N warning(s) above." (no problems). The warnings from sections 2–4 stay until you finish them.

New since this release: `/health` answers 200 only when the API can reach Postgres and Redis. If the deploy waits on an unhealthy `api`, read its log (`docker compose -f docker-compose.prod.yml logs api --tail 50`). The log names which one failed.

## 2. Off-site backups (BACKUP_REMOTE)

Today the only copies of the database are on the server's own disk. The simplest off-site option is **Cloudflare R2**, through an rclone **crypt** remote:

- R2 is in the Cloudflare account you already use for the tunnel.
- At this size (14 dumps of a few MB each) it costs nothing: the free tier is 10 GB.
- `deploy/backup.sh` already syncs to any rclone remote named in `BACKUP_REMOTE`.

A crypt remote encrypts each file before it leaves the server. That matters because the dumps hold every seller's email address and their encrypted tokens.

1. In the Cloudflare dashboard:
   1. Create the bucket: R2 → Create bucket → name `listyro-backups` → location Automatic.
   2. Create its token: R2 → Manage R2 API tokens → Create API token, with permission **Object Read & Write** limited to the bucket `listyro-backups`.
   3. Note the **Access Key ID**, the **Secret Access Key** and the **S3 endpoint** (`https://<account-id>.r2.cloudflarestorage.com`).
2. Install rclone on the server (if `rclone version` is not found):

   ```bash
   sudo apt-get install -y rclone
   ```

3. Configure two remotes as root, because root runs the backup cron:

   ```bash
   sudo rclone config
   ```

   1. First remote (where the files go):
      - `n` (new remote), name `r2`;
      - storage `s3`, provider `Cloudflare`;
      - `env_auth` false; paste the access key ID and secret;
      - region `auto`; paste the endpoint;
      - ACL `private`;
      - leave the rest at the defaults.
   2. Second remote (the encryption):
      - `n`, name `offsite`, storage `crypt`;
      - remote `r2:listyro-backups`;
      - `filename_encryption` standard, `directory_name_encryption` true;
      - choose your own password, then a second (salt) password.
   3. **Put both crypt passwords in the password manager now.** Without them the off-site copies cannot be read, by anyone.

4. Tell the backup script about it:

   ```bash
   sudoedit /etc/listyro/ops.env      # add:  BACKUP_REMOTE=offsite:
   ```

5. Run one backup by hand and check that the copy landed:

   ```bash
   sudo bash deploy/backup.sh
   sudo rclone ls offsite: | head
   ```

   You should see `offsite:  synced to offsite:` at the end of the backup. The listing shows `db/db-<date>.dump` files with readable names (rclone decrypts them for you). In the R2 dashboard the same files have scrambled names. That is the encryption working.

The remote keeps the same 14 days as the server, because `rclone sync` removes what the server removed. That's what the Privacy Policy promises (`RETENTION.backupDays`).

## 3. Backup heartbeat (HEALTHCHECK_URL)

Without a heartbeat, a backup that stops running is not noticed. Free option: healthchecks.io.

1. Sign up, then Add Check:
   - name `listyro backup`;
   - schedule: period **1 day**, grace **2 hours**;
   - integrations: your email (and the same ntfy topic as `ALERT_WEBHOOK_URL`, if you like).
2. Copy its ping URL (`https://hc-ping.com/<uuid>`) and add it:

   ```bash
   sudoedit /etc/listyro/ops.env      # add:  HEALTHCHECK_URL=https://hc-ping.com/<uuid>
   sudo bash deploy/backup.sh         # the check turns green
   ```

   A failed backup pings `/fail`, so the check turns red at once rather than after the grace period.

## 4. SSH hardening

Preflight says "ssh hardening not found". These are the same settings `deploy/provision.sh` writes: keys only, no root login.

1. First make sure you can sign in **with your SSH key** in a second terminal (`ssh <operator>@<server>`), and keep that session open.
2. Then:

   ```bash
   sudo tee /etc/ssh/sshd_config.d/60-listyro.conf >/dev/null <<'EOF'
   # production-spec F1
   PasswordAuthentication no
   KbdInteractiveAuthentication no
   PermitRootLogin no
   PubkeyAuthentication yes
   EOF
   sudo sshd -t && sudo systemctl reload ssh
   ```

   `sshd -t` prints nothing when the configuration is valid. If it prints an error, don't reload. Fix the file first.
3. Open a **third** terminal and sign in again with your key. It must work before you close the second one.

## 5. Prove a restore works (monthly)

```bash
sudo bash deploy/restore-test.sh
```

This restores the newest dump into a throwaway Postgres container (never production) and compares every table's row count with the live database. You should see a table of counts with no `<- differs` lines, ending in `restore test ok: N tables, every row count matches`. If the site was used after the backup ran, a few counts differ and the script says so; run `sudo bash deploy/backup.sh` first, then this again.

Locally, the same check was run on synthetic data at migration head (0089): the dump was made the way `backup.sh` makes it, restored into a fresh Postgres 16, and all 54 tables had the same counts.

## 6. Check once after the deploy

```bash
bash deploy/preflight.sh
```

It should end in "Ready.": every warning in sections 2–4 should be gone. The ones that may remain are informational: the disk percentage, and `GLOBAL_DAILY_LIMIT` if you changed it.

Then run the 20-minute manual test in `docs/release-checklist.md` on the test shop.

## 7. Faster pages and a steadier tunnel (23 October 2026)

After `bash deploy/update.sh` with this change (no migration, no `.env` change):

1. The tunnel is on HTTP/2. Preflight prints `ok    tunnel protocol http2`. Then:

   ```bash
   docker compose -f docker-compose.prod.yml logs --since 10m cloudflared | grep -o 'protocol=[a-z0-9]*' | sort | uniq -c
   ```

   Every line should say `protocol=http2` (usually four, one per edge connection). Why it
   changed: `docs/deploy.md`, "Tunnel protocol and routing".
2. The small image copies, for images stored before this release. A dry run first: it says
   how many copies it would make, about how much space they take, and how many earlier
   previews it deletes (those usually free more than the copies take):

   ```bash
   docker compose -f docker-compose.prod.yml exec api python -m app.cli thumbnails
   docker compose -f docker-compose.prod.yml exec api python -m app.cli thumbnails --apply
   ```

   It stops if the disk would have less than 5 GB free (`--keep-free-gb`), and leaves out an
   account at its storage cap (listed). Screens make any copy they ask for that is missing, so
   this only saves the first visitor the wait.
3. In a browser, open the site with the developer tools' Network tab: images are `webp`, a
   second visit to the same page shows them "(disk cache)" or "(memory cache)", and an upload
   shows three files going at once.

## 8. Listings that did not go live, and personalization (25 October 2026)

After `bash deploy/update.sh` with this change (migration `0091_approval_record`: two empty columns on
`generated_content`, quick, no rows changed; no `.env` change):

1. The seller's report: for one account, every listing since a date, what happened to it and why.
   Read only (one READ ONLY transaction, SELECT only, no tokens or buyer data printed, titles cut at
   40 characters). `--check-etsy` adds one getListing per listing through the rate limiter and the
   daily budget (upkeep); nothing is written to Etsy:

   ```bash
   docker compose -f docker-compose.prod.yml exec api python -m app.cli publish-report <email> --since 2026-10-23
   docker compose -f docker-compose.prod.yml exec api python -m app.cli publish-report <email> --since 2026-10-23 --check-etsy
   ```

   The summary comes first: published, published here but still a draft on Etsy (only with
   `--check-etsy`), approved but not published, scheduled and waiting, scheduled and missed, never
   approved, refused by Etsy, held by our own check; each split by personalization on/off, with
   listing ids. Then one block per listing.
2. Listings whose personalization breaks Etsy's rules (instructions over 120 characters, more than
   one word in capitals) are now held before anything is sent; the seller sees why on the card and
   the profile, with the fix. Once fixed they go ahead by themselves. Nothing to run.
3. A publish Etsy answered 2xx but kept as a draft is no longer recorded as live: the job fails with
   Etsy's state and the seller is told. Listings recorded live before this release that are still
   drafts show up in step 1 with `--check-etsy` as "published here, still a draft on Etsy".

## What is already in place (no action)

- Error alerts go to `ALERT_WEBHOOK_URL`: failed deploy, failed backup, disk growth, an AI provider refusing us, a new invite request, and the weekly Etsy spec watcher.
- Logs are scrubbed by `app/core/logsafety.py` for every logger (uvicorn, arq and SQLAlchemy included): OAuth codes and state, bearer tokens, session cookies, API keys, password fields and email addresses.
- Disk: the hourly `disk-check.sh`, the weekly `docker builder prune`, and `update.sh` reclaiming build cache below 12 GB free.
- Stuck work recovers on its own:
  - jobs left "running" by a stopped worker are queued again;
  - since this release, jobs lost from Redis are queued again;
  - Replace images recovers too.
