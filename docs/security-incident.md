# Runbook: security incident or data breach

What to do when we discover, or suspect, that data was exposed or that a key or credential is no longer secret.

**Two deadlines, both counted from the moment we discover it:**

- **Within 24 hours: notify Etsy at dpo@etsy.com and notify every affected seller.**
- **At once: report any concern about an Etsy API key or credential to security@etsy.com.**

Do not wait to be certain. "We are investigating and will follow up" sent inside the deadline is right; silence is not. Who: either developer; the other is told immediately.

## 1. What counts

- Someone other than us had access to the server, the database, a backup or the storage volume.
- An Etsy API key, the shared secret, the token encryption key, a seller's OAuth token, the admin bootstrap token or a database password was exposed: committed, pasted, logged, sent to the wrong place, or left on a machine that was lost.
- One seller could see or change another seller's data.
- A database dump or a copy of production data is somewhere it should not be.
- A seller reports their Listyro account or shop being used by someone else.

If in doubt, treat it as an incident.

## 2. Checklist

Write the time beside each step as you do it; the notifications need those times.

- [ ] **Record the discovery time** (UTC and local), who discovered it and how. The 24 hours start here.
- [ ] **Contain.** Choose what applies:
  - A key or credential is exposed: rotate it now (section 4).
  - The server is suspected: stop outgoing Etsy calls (`ETSY_CALLS_BLOCKED=true`, as in `docs/etsy-access-ended.md` step 1) and block new sign-ins if needed by stopping the `frontend` service.
  - One account is being abused: Admin → Users → Suspend. That ends its sessions and cancels its queued jobs at once.
- [ ] **Keep the evidence.** Do not delete logs. Copy what you need out of the server: `docker compose -f docker-compose.prod.yml logs --since 72h > incident-logs.txt`, the `audit_log` rows for the period, Cloudflare's logs. Keep them outside the repository.
- [ ] **Find out what was reachable.** Which tables, which files, which accounts, for how long. The list of what we store and for how long is in `docs/etsy-compliance.md`, section 3. Note what we do **not** hold: no buyer names, addresses or e-mail addresses, no payment data, no passwords in readable form.
- [ ] **Report credential concerns to security@etsy.com** (template B) at once if an Etsy key, secret or token is involved.
- [ ] **Notify dpo@etsy.com within 24 hours** (template A).
- [ ] **Notify the affected sellers within 24 hours** (template C), from support@listyro.com. Addresses are in Admin → Users. If we cannot yet tell who is affected, notify everyone.
- [ ] **Fix the cause**, deploy with `bash deploy/update.sh`, and check it.
- [ ] **Follow up** with Etsy and the sellers when the facts are complete: what happened, what was affected, what we changed.
- [ ] **Write it down** in a short internal note: timeline, cause, what was exposed, what we did, what we changed so it cannot happen again.

Depending on where the affected sellers live, a data protection authority may also have to be told (for example within 72 hours under the GDPR, and under Türkiye's KVKK). Check this the same day.

## 3. Message templates

Fill every bracket. State facts and times; do not guess, and do not say "no data was affected" unless we have checked.

### A. To dpo@etsy.com (within 24 hours)

> Subject: Security incident notification: Listyro (Etsy app ID 1509913215439)
>
> We are Talha Yılmaz and Ahmet Düzgün, operating as Listyro, a third-party application using the Etsy Open API (app ID 1509913215439).
>
> We are notifying you of a security incident.
>
> - Discovered: [date and time, UTC]
> - What happened: [one or two factual sentences]
> - Etsy data involved: [which kinds: OAuth tokens (stored encrypted) / cached listing data / sales totals and order numbers / ledger totals / none identified yet]. We do not store buyer names, addresses, e-mail addresses or messages.
> - Sellers affected: [number, or "under investigation"]
> - What we have done: [contained at …; keys rotated at …; calls to Etsy stopped at … / not needed because …]
> - What we are doing next: [ ]
> - Affected sellers were notified at [time] / will be notified by [time].
>
> Contact: support@listyro.com, [phone]. We will send a follow-up by [date].

### B. To security@etsy.com (at once, for keys and credentials)

> Subject: Possible exposure of API credentials: Listyro (Etsy app ID 1509913215439)
>
> We believe the following credential for our application may have been exposed: [API keystring / shared secret / OAuth tokens of N shops].
>
> - Discovered: [date and time, UTC]
> - How it may have been exposed: [ ]
> - What we have done: [rotated at …; all sellers' tokens deleted at …, so no existing token can be used]
>
> Please tell us if you see use of this credential that is not ours, and whether you want the key revoked on your side. Contact: support@listyro.com, [phone].

### C. To affected sellers (within 24 hours)

> Subject: Important: a security incident at Listyro
>
> Hi,
>
> We are writing to tell you about a security incident at Listyro that affects your account.
>
> What happened: [plain description]. We discovered it on [date and time].
>
> What was involved for you: [your e-mail address / the connection between Listyro and your Etsy shop / the copies of your listings and your sales figures we hold / your uploaded designs]. We do not store your password in readable form, and we do not store anything about your buyers.
>
> What we have done: [ ]. [We have disconnected your shop from Listyro, which deletes the connection tokens; you can reconnect it on the Shops page.]
>
> What you should do: [change your Listyro password / check "Apps you've authorised" in your Etsy account settings and remove Listyro if you wish / nothing]. Your listings on Etsy are [not affected].
>
> We have notified Etsy. We will write again when we know more, and you can reply to this e-mail with any question.
>
> We are sorry.
>
> Talha Yılmaz and Ahmet Düzgün, Listyro (support@listyro.com)

## 4. Rotating keys

All secrets live in the production env file on the server, never in the repository. After changing any of them, restart with the commands below (not `deploy/update.sh` unless code changes too).

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate api worker
```

| Secret | How to rotate | What it does to sellers |
|---|---|---|
| **Etsy API key and shared secret** (`ETSY_CLIENT_ID`, `ETSY_CLIENT_SECRET`) | In Etsy's developer portal (etsy.com/developers/your-apps), regenerate the shared secret, or ask Etsy to if the portal does not offer it. Put the new values in the env file and restart. Run `python -m app.cli oauth-check`. | Existing shop connections keep working if only the secret changed. If Etsy issues a new keystring, every seller must reconnect. |
| **Sellers' OAuth tokens** | They cannot be rotated by us. To make them useless: disconnect the affected shops (Admin cannot do this for a seller; run `python -m app.cli purge-etsy-data --apply` only if **all** shops are affected, see `docs/etsy-access-ended.md`), or ask the sellers to press Disconnect. A seller can also remove Listyro under "Apps you've authorised" on Etsy. | They reconnect on the Shops page. |
| **Token encryption key** (`ENCRYPTION_KEY`) | Generate a new one (`bash deploy/generate-secrets.sh` shows how). Stored tokens were encrypted with the old key and cannot be read with the new one, so every shop must reconnect. If the old key leaked together with a database copy, treat every seller's token as exposed (row above) and report to security@etsy.com. | Every seller reconnects. |
| **All sign-in sessions** | Sessions are random tokens kept in Redis (`SECRET_KEY` signs nothing today; replace it anyway if it leaked). End every session: `docker compose -f docker-compose.prod.yml exec redis redis-cli --scan --pattern 'session:*' \| xargs -r docker compose -f docker-compose.prod.yml exec -T redis redis-cli del`. | Everyone signs in again. |
| **Database password** | Change it in PostgreSQL and in `DATABASE_URL`, restart every service. | None. |
| **Admin bootstrap token** (`ADMIN_TOKEN`) | Remove it or replace it; it does nothing once an admin exists. | None. |
| **AI provider key** (`LLM_API_KEY`) | Revoke in the Anthropic console, create a new one, restart. | Writing listings pauses until the new key is in. |
| **Alert webhook** (`ALERT_WEBHOOK_URL`) | Change the topic or webhook; update it in the env file and in `deploy/` cron settings. | None. |
| **Cloudflare Tunnel token** | Rotate in the Cloudflare dashboard, update `cloudflared`'s configuration, restart it. | A short interruption. |
| **A seller's password** | Admin → Users → temporary password; the seller must choose a new one at sign-in. | That seller only. |

After rotating: run `bash deploy/preflight.sh`, sign in, connect the test shop and create one draft.

## 5. If a secret was committed to the repository

Rotating it is the fix; removing it from history is clean-up. Rotate first, then rewrite history and force-push from one clone, and tell the other developer to re-clone (see the note on dumps in `CLAUDE.md`). Check GitHub's secret scanning alert and close it with the rotation time.
