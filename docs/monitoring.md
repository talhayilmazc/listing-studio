# Monitoring

No seller finds a drafting, publishing or shop-connection problem before we do, and nothing fails
silently. From 2026-10-23 to 2026-10-29 every shop connection was refused and we learned it from a
customer; `tests/test_monitor.py::test_replay_of_2026_10_23_…` replays that week.

Messages go to `ALERT_WEBHOOK_URL`. They carry no token and no buyer data, and emails are masked.

## How it runs

| Part | Where | Why there |
|---|---|---|
| `monitor/tick.py`, every minute | API (one process at a time, a Redis turn) | still runs when the worker is dead |
| `monitor/signals.py` | every process, flushed every 15 s | a page pays no Redis round trip to be counted |
| `workers/preflight.py`, every 10 minutes | worker | it reads Etsy, so it goes through the queue and limiter |
| `deploy/watch.sh`, every minute | host cron | only the host sees memory, restarts, OOM kills, and a dead API |

If the API itself is down the app can say nothing, so `watch.sh` posts to the webhook directly: after 3
minutes unhealthy, then hourly, then once when it recovers.

## Alerts

| Problem | Fires when | Clears when |
|---|---|---|
| go-live failed | a go-live job failed, or our own check refused it, in the last day | it goes live, or a day passes |
| missed slot | a time passed 10 minutes ago with nothing sent (not paused) | it is sent, moved or removed |
| job stuck | running or due for over 30 minutes (not waiting for its own time, not held for the seller) | it moves |
| shop token stopped working | Etsy answered `invalid_grant`; the shop is marked expired | the seller reconnects |
| shop connection refused | any refused callback, grouped by account and reason | that account connects, or a day passes |
| shop connection failing | 2 or more tries in 24 hours and none succeeded | that account connects |
| API errors | over 2% 5xx in 10 minutes, with at least 20 requests | the share falls |
| slow endpoints | an endpoint's p95 over the last hour is above 10 s, with at least 5 requests (`rules.SLOW_P95_MS`, `SLOW_MIN_REQUESTS`) | the hour's p95 falls |
| worker / scheduler not running | its heartbeat is over 5 minutes old | the heartbeat returns |
| database connections | a process used over 80% of its pool | a report at or under 80% |
| idle transaction timeout | fired in the last hour | an hour without it |
| disk nearly full | over 85% | under it |
| container memory | over 90% of its limit | under it |
| container restarted | the restart count rose, or OOM-killed | stayed up for an hour |
| container watch not running | no numbers for 5 minutes (production) | numbers return |
| scheduled run will fail | the check an hour before found something | the next check, or the seller's fix |

## No flooding

- The same problem, grouped per account, is said once until it is fixed or a day passes ("Still: …").
- At most 20 messages an hour. The rest go into the next digest and are said once there is room.
- "Resolved: …" comes after a problem has stayed away for 10 minutes, so one that flickers is one problem.
- A message that could not be delivered is tried again next minute and never counted as said.

## The digest

09:00 Istanbul. It goes out between 09:00 and 13:00, once, and is retried if delivery fails.

- **Per account, only accounts with something to say:** failed drafts, failed go-lives, held listings and
  why, missed slots, slots due in 24 hours that will fail, records out of step with Etsy, queue depth.
- **Overall:** shop connections tried, succeeded and refused with reasons; sign-ups; Etsy calls of the daily
  limit; slowest endpoints by p95; memory peak per container; disk; open problems; what the hourly cap kept
  back.

**Endpoint times (fix/perf-and-memory, 14 Nov 2026).** Each endpoint is "METHOD /path" with its ids taken out.
The p95 is a time a request really took: the latest 2,000 requests per endpoint and day (and 1,000 per hour,
for the alert) keep their own time in Redis (`monitor:ms:*`, 3 days; `monitor:ms-hour:*`, 3 hours). Counts
from before that say "up to 3,200 ms" (the bucket's top was all that was known) or "over 25.6 s" for the
last bucket. The time is ours: the wait for the request's body is left out (`core/security.py`, `waited`),
so a seller's 50 MB photo on a slow line does not read as a slow endpoint. Before this, the digest's
"60,000 ms" for uploads was the top bucket (anything over 25.6 s) and included that wait.

## The check before a run

Every 10 minutes the worker looks for runs in the next 70 minutes and queues one read per shop, at most
once per shop per hour (upkeep: the app's budget, not the seller's). It reads the shop's shipping
profiles, return policies, processing profiles and partners (4 requests), never writes, and reports:

| Finding | The seller sees | Link |
|---|---|---|
| Etsy no longer accepts the sign-in | Connect the shop again | Shops & groups |
| The shop is disconnected (no Etsy call) | Connect it again | Shops & groups |
| The profile was deleted and there is no draft yet | Choose a profile or take it off the schedule | Scheduled |
| A mapped shipping, return or processing setting is gone | Choose another with "change" | Profiles |

A setting the app made itself is made again when the draft needs it, so that alone is not a failure for a
listing with no draft yet. An unanswered read says nothing about the shop and is asked again next time.

## Commands

```
python -m app.cli health-digest          # prints the digest; nothing is sent or changed
python -m app.cli health-digest --send   # also sends it
python -m app.cli alert-test             # one test message to the webhook
```
