# Load test: 100 sellers on the production-sized stack

Synthetic data only, Etsy and the model stubbed, never a real account. Reports and logs go to
`load-output/` (git-ignored).

```
bash backend/scripts/load/run.sh --label X --cpuset 0-1 --accounts 100 -- --steps 25,50,100 --think 20,60
```

`docker-compose.load.yml` (project `listyro-load`) runs the production frontend image and the real
API and arq worker with production settings on an internal network, without volumes. `run.sh`
generates the keys. `ETSY_CALLS_BLOCKED` stays on: `scripts/load/stub.py` lets only the API client's
own calls through and answers them itself, and refuses to start otherwise.

## What a run is

- **Data:** 100 accounts × 3 shops × 500 listings = 150,000 listings, about 284 MB of database.
- **CPU:** `--cpuset 0-1` pins the app, Postgres, Redis and the frontend to 2 CPUs of a development
  machine; the driver runs on other CPUs. Production's CPUs are a different speed.
- **A seller, all starting within the same minute:** upload 15 photos (0.63 MB each), write 5 listings,
  approve them, send them to 2 shops (10 drafts, 3 images each), and read pages. One seller in three
  also prepares a 3-photo template and runs mockups. "Human" pace reads a page every 20–60 s, "fast"
  every 4–12 s.
- **Planted on top:** 12 scheduled go-lives per seller and a reconcile of each shop.
- **Etsy stub:** 120–350 ms, 0.2% 429, 0.5% 5xx, image uploads share a simulated 14 Mbps uplink.
- **Model stub:** 6–16 s a call. This is an assumption, not a measurement.

A page's time is all its requests made at once, as the screen makes them.

## Final results (code of commit 8a34a84)

### 2 CPUs, human pace

Every step had 0 errors.

| | 25 sellers | 50 | 75 | 100 |
|---|---|---|---|---|
| Overview p50 / p95 | 431 ms / 836 ms | 605 ms / 2.1 s | 615 ms / 4.4 s | 824 ms / 19.8 s |
| Scheduled | 84 ms / 293 ms | 86 ms / 982 ms | 97 ms / 2.7 s | 226 ms / 9.8 s |
| Review | 127 ms / 544 ms | 164 ms / 859 ms | 178 ms / 3.5 s | 218 ms / 15.4 s |
| Batches | 400 ms / 674 ms | 481 ms / 1.6 s | 554 ms / 4.0 s | 654 ms / 12.2 s |
| Batch page, 150 listings | 576 ms / 1.3 s | 711 ms / 1.5 s | 738 ms / 4.4 s | 867 ms / 15.7 s |
| Upload 15 photos | 0.9 s / 1.5 s | 2.5 s / 5.9 s | 35 s / 43 s | 111 s / 153 s |
| Write 5 listings (10 model calls) | 114 s / 121 s | 114 s / 128 s | 125 s / 135 s | 145 s / 187 s |
| Prepare a 3-photo template | 116 s / 122 s | 306 s / 382 s | 590 s / 690 s | 790 s / 920 s |
| Mockup run (15 mockups) | 55 s / 62 s | 153 s / 187 s | 211 s / 235 s | 245 s / 277 s |
| Drafts a minute | 44.4 | 41.5 | 37.9 | 43.3 |
| Go-lives late p50 / p95 / worst | 34 / 56 / 98 s | 39 / 62 / 108 s | 44 / 69 / 144 s | 52 / 79 / 135 s |
| How long it stays slow | none | none to speak of | about 4 minutes | about 6 minutes |

The page p95s cover the whole step, so they are set by its first minutes. From minute 7 of the
100-seller step, all requests have p95 of about 0.4–0.6 s.

### 100 sellers, other conditions

| | 2 CPUs, human | 4 CPUs, human | 2 CPUs, fast |
|---|---|---|---|
| Requests a second | 17.0 | 17.2 | 29.7 |
| Errors | 0 | 0 | 13 |
| Overview p95 | 19.8 s | 5.9 s | 30.9 s |
| Scheduled p95 | 9.8 s | 3.3 s | 20.1 s |
| Review p95 | 15.4 s | 4.1 s | 22.9 s |
| Batches p95 | 12.2 s | 4.0 s | 22.5 s |
| Batch page p95 | 15.7 s | 4.7 s | 27.6 s |
| Upload 15 photos p50 / p95 | 111 / 153 s | 9 / 27 s | 165 / 203 s |
| Write 5 listings p50 / p95 | 145 / 187 s | 127 / 144 s | 276 / 345 s |
| Prepare a template p50 / p95 | 790 / 920 s | 616 / 726 s | 1,603 / 1,804 s (the driver's cap) |
| Drafts a minute | 43.3 | 42.1 | 43.7 |
| Go-lives late p50 / p95 / worst | 52 / 79 / 135 s | 44 / 71 / 113 s | 48 / 74 / 149 s |

The 4-CPU run still has 2 API processes. A third API process was not measured.

The fast run is about 3× the reading rate of the human run, and it is over capacity. Its 13 errors:

- **5 requests gave up waiting 60 s for a database connection.** They were internal errors in that run;
  they now answer 503 with "try again" and `Retry-After` (not rerun under load, covered by a test).
- **5 `generate` requests hit the driver's 120 s limit.** They carried on writing on the server.
- **3 `generate` requests were refused with 409 "being written in another tab".** They were the next
  group sent while the timed-out one still ran. Those 3 listings were not written or counted, and the
  seller is told. Cloudflare's own limit is about 100 s, so at this pace a seller would see that error
  first. At human pace the longest `generate` was 62 s and the longest upload 58 s.

## Where it breaks first, in order

1. **The database pool: fixed.** Connections sat idle in transactions across slow work.
2. **Redis commands queuing on one lock: fixed.** redis-py before 6 holds the pool's lock across a
   connection check, so a process started one Redis command per event-loop turn.
3. **API CPU: the limit now.** About 30 requests a second at 1.4–1.5 cores on 2 CPUs at the fast pace.
   Surges end when it stops being saturated.
4. **The 14 Mbps uplink: caps drafts at about 43 a minute.** 4 CPUs did not raise it. It alone needs
   1,161 s for the 2,031 MB of one run's images. Larger real photos would lower it in proportion.
5. **Analysis CPU: caps templates at about 7.3 photos a minute.** One analysis at a time per process.

Not limits in these runs:

- **Etsy's rate:** mean 6.0–6.8 requests a second, busiest second 11, against our limiter's 10.
- **Etsy's daily budget:** about 90–100 requests per seller per run.
- **Redis:** at most 6 MB and 74 clients.
- **Memory:** see below.

## What was held across slow work

Commit or release before slow work, now central:

- `core/db_release.py::before_slow()` is called by `etsy/api.py::_request`, `llm.complete_json` and
  `core/image_work.run`, and `ready_to_save()` takes a connection back with a longer wait.
- Routes take `Depends(get_session, scope="function")`, which ends before the answer is sent. Before,
  a connection stayed held during the client's whole download.

Line numbers are from before the fix:

| Where | Held across |
|---|---|
| `workers/mockups.py:87-88` | template analysis, up to 37 s |
| `workers/mockups.py:692, 724, 745, 780` | image work in `run()` |
| `api/batches.py:~1657` | model call |
| `api/batches.py:~1447-1468` (`generate_content`) | Redis |
| `api/batches.py` (`get_asset_image`) | thumbnail work |
| `api/another_shop.py:661-662` | model call |
| `api/personalization_fix.py:109` | model call |
| `pipeline/ingest.py:150, 259` | image processing |
| `api/mockups.py:1148-1150` (`add_photo`), `1713` (`_store_design`), `1306` (`find_at`) | image work |
| `api/mockups.py:1450, 1453, 1467, 1873, 1885` (design back, brush) | image work |
| `api/mockups.py` (`list_templates`) | Redis |
| `api/meta.py:72-80` (quota) | Redis |
| every route | the client's download |
| every worker Etsy and model call | the call |

`idle_in_transaction_session_timeout` is 60 s (`DB_IDLE_TRANSACTION_SECONDS`) and is logged when it
fires. It did not fire in the three final runs.

Still under a row lock, on purpose: `asyncio.to_thread(storage_cap.used…)` in `pipeline/ingest.py:210`
and `api/mockups.py:~759`. It is a rare scan, cached 5 minutes per process.

At saturation transactions still show as idle (29 of 41 connections at 100 sellers, longest 5.9 s; 30
of 40 at the fast pace, longest 13 s). All follow quick queries: the event loop is behind, under full
CPU, between two statements. At 25 and 50 sellers none lasted over 2 s.

## Memory

The limits live in one block at the top of `docker-compose.prod.yml` (`x-memory`).

| Container | Peak measured | Can't be given back | Limit, 5.8 GB VM | Headroom | Limit, 8 GB VM |
|---|---|---|---|---|---|
| api | 1,668 MB RSS | 1,575 MB | 1,815 MB | 15% | 2,400 MB |
| worker | 2,136 MB | 2,136 MB | 2,460 MB | 15% | 3,000 MB |
| postgres | 812 MB with file cache | 462 MB | 585 MB | 27% | 1,000 MB |
| frontend | 104 MB | 104 MB | 120 MB | 15% | 200 MB |
| redis | 11 MB | 11 MB | 120 MB (maxmemory 96 MB) | over 900% | 384 MB (maxmemory 256 MB) |
| together | | | 5,100 MB | | 6,984 MB |
| cloudflared | 29 MB idle | | 128 MB | | 128 MB |

"Can't be given back" is what a limit really has to hold. The API's RSS counts shared pages once for each
of its three processes, and the kernel charges them once. Postgres's peak is mostly file cache, which the
kernel takes back at the limit (334 MB its own + 128 MB shared buffers remain).

**15% over the plain peaks does not fit 5.8 GB:** 4,731 MB × 1.15 = 5,441 MB, plus 700 MB for cloudflared
and the OS, is 6,141 MB. So on this VM the 15% is over what can't be given back. What that costs:

- Postgres's limit is under its measured peak: less room for file cache, so reads may be slower under load.
  Not measured at this limit.
- Redis's `maxmemory` falls from 256 MB to 96 MB, about 9× its peak.

The 5.8 GB limits leave 700 MB for cloudflared and the OS. A container that reaches its limit is killed
inside itself and restarts; the others keep running. With 4 GB of swap a container can also swap before it
is killed (Docker's default allows swap equal to the limit), so it slows down first.

The busiest moment of the final runs, without the driver: 4,466 MB (app 3,727, postgres 626, frontend 103,
redis 10). In this test the API and worker shared one container limit, so their separate limits were not
tested by reaching them, and photos were small.

## Mockups

20 accounts × 100 listings, 8 sellers making mockups, 20 reading:

| Priority | Photos a minute | Mockups a minute | Page p95 |
|---|---|---|---|
| Whole worker at nice 10 | 6.6 | 33.0 | 945 ms |
| Normal | 7.2 | 36.1 | 1,931 ms |
| Image threads only at nice 10 (the default) | 7.3 | 36.6 | 569 ms |

Heavy jobs take turns per account (`workers/fair.py`: 2 at once, 1 per account) and sellers see their
position. Tests prove the order. No run measured one seller with 100 mockups against the others.

## Uploads: why there is no small cap

A cap of 4 uploads per process made pages better at 100 sellers (Overview p95 10.6 s against 19.8 s),
but the queue behind it grew past any client's patience: 1,104 of 1,500 uploads timed out at the fast
pace, and Cloudflare would end them at about 100 s. The browser already sends 3 at a time, so the
server now limits only what uploads hold in memory: 100 MB per API process, first come first served
(`core/image_work.py::upload_turn`, `UPLOAD_MEMORY_MB`).

## Slowest queries

| Query | Cost in the fast run | State |
|---|---|---|
| `SELECT DISTINCT batch_id, connection_id` from publications (batch list) | 25 ms × 2,884 = 74 s | Largest remaining, left as is |
| The job-queue fingerprint (three subqueries on `job`) | 2.0 ms × 9,720 = 19 s | Left as is |
| Full `generated_content` for a batch | 12 ms × 1,381 = 16 s (1,500 rows each) | Left as is |
| `SELECT mockup_template … FOR UPDATE` | 17 ms × 102 | Lock wait, on purpose |
| asyncpg's `typeinfo_tree` | was 42,294 calls | Fixed: connections are kept |
| `UPDATE etsy_checked_at` | was 45,751 calls, 187 s | Fixed: one bulk update, 5–16 ms |

## Not done

- Nightly upkeep crons (stats, sales) are not spread out.
- `rerender_mockups` is outside the fair heavy queue.
- The batch list, rows and content could use per-account caching, at some risk of staleness.
