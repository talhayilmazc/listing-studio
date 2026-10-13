# Runbook: Etsy access ended

What to do if Etsy suspends or ends Listyro's access to its API, or tells us to stop. Our Terms promise sellers that we then stop all use of Etsy data immediately. Do the steps in order; each one is safe to repeat.

Who: either developer. Where: the production server, in the project directory (`docs/deploy.md` has the path and how to get a shell).

## 0. When this applies

- Etsy writes that our API access is suspended, revoked or not renewed.
- Etsy asks us to stop using its data or to delete it.
- Our key starts being refused for every shop (401/403 on every request) and Etsy confirms it is deliberate. A single shop being refused is that seller's token, not this runbook.

If it is a security incident instead (a leaked key, a breach), follow `docs/security-incident.md` first; it may lead here.

## 1. Stop every call to Etsy (minutes)

One setting makes the app refuse every outgoing call before it reaches the network: API requests, token refreshes, image fetches and new shop connections.

```bash
# In the production env file (the one deploy/update.sh uses):
ETSY_CALLS_BLOCKED=true
```

Then restart the two services that talk to Etsy. Do **not** run `deploy/update.sh` for this: its preflight refuses a production with this setting on, by design.

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate api worker
docker compose -f docker-compose.prod.yml logs worker --tail 20 | grep -i "ETSY_CALLS_BLOCKED"
```

The worker's start-up log must say that it sends nothing to Etsy. From this moment no request is made for any shop. Jobs that need Etsy fail with a clear reason instead of calling it.

Also, in Admin → Usage → "Etsy data and shops", tick **Pause new shop connections**, so the Shops page tells sellers why they cannot connect.

## 2. Tell the sellers (the same day)

There is no e-mail service in the app: send from support@listyro.com. The addresses are in Admin → Users.

> Subject: Listyro has stopped working with Etsy
>
> Hi,
>
> Etsy has ended Listyro's access to its API, so Listyro can no longer read from or write to your Etsy shop. We stopped all requests to Etsy on [date, time, time zone].
>
> What this means for you:
> - Your listings and drafts on Etsy are not affected. They are in your shop and you manage them on Etsy as before.
> - Listyro will not create, change or publish anything in your shop. Anything you had queued or scheduled in Listyro will not run.
> - We are deleting the Etsy data we hold for your shop: the connection tokens, the copies of your listings, and the sales, fee and statistics figures. This will be finished by [date].
> - The designs you uploaded and the listing text you wrote in Listyro are yours and stay in your account. You can delete your account in Settings at any time.
>
> If you have questions, reply to this e-mail.
>
> Talha Yılmaz and Ahmet Düzgün, Listyro

Give sellers a short, stated time (a day or two) only if Etsy's message allows it. If Etsy says "immediately", go straight to step 3.

## 3. Delete all stored Etsy data

First look at what would be deleted. This changes nothing:

```bash
docker compose -f docker-compose.prod.yml exec api python -m app.cli purge-etsy-data
```

It prints a count per table and how many shops still hold tokens. Take a last backup if you want the counts on record (`bash deploy/backup.sh`); remember the backup itself contains the data and expires in 14 days.

Then delete:

```bash
docker compose -f docker-compose.prod.yml exec api python -m app.cli purge-etsy-data --apply
```

What it does, for every account at once:

- deletes every shop's access and refresh token first, so nothing could call Etsy even with the block off;
- cancels every queued job and planned draft;
- deletes the listing cache, the saved copies of listings, the sales totals and order lines, the ledger totals, the imported statement and Ads figures, the views and favourites, the text versions sent to Etsy, the records of drafts and live listings, the profiles (they are built from Etsy reference listings) and their links to shops;
- empties each shop's Etsy ids, name, scopes, section list and listing counts;
- writes one audit row with the counts.

What it leaves: the accounts, the sellers' own uploads and the listing text they wrote, audit rows and our own cost records.

Run the dry run again; every count must be 0.

## 4. Finish

- Backups made before the purge still hold Etsy data. They expire by themselves in 14 days. If Etsy asks for immediate deletion everywhere, delete them now (`docs/deploy.md`, section 7 "Backups") and say so in your answer to Etsy.
- Redis holds short-lived Etsy-derived keys (profile link choices, six hours). They expire by themselves; to clear them at once: `docker compose -f docker-compose.prod.yml exec redis redis-cli --scan --pattern 'link-plan:*' | xargs -r docker compose -f docker-compose.prod.yml exec -T redis redis-cli del`.
- Answer Etsy: when calls stopped, when the data was deleted, the counts from the audit row, when the last backup expires.
- Put a notice on the public site if the service is not coming back, with the 30 days the Terms promise for withdrawing the Service where that still applies.
- Leave `ETSY_CALLS_BLOCKED=true` until Etsy restores access in writing.

## If access is restored

Set `ETSY_CALLS_BLOCKED=false`, run `bash deploy/update.sh` (its preflight will pass again), untick "Pause new shop connections", and tell sellers they need to connect their shops again and choose their reference listings again: nothing from before the purge comes back.
