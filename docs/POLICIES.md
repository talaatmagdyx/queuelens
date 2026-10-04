# Replay policies

A replay policy makes QueueLens retry a dead-letter queue by itself. A message that
failed once is sent back after a while; one that keeps failing waits longer each time
and is eventually parked for a person to look at.

## What a run does

Every `interval_minutes`, the policy reads its DLQ once and sorts the messages, oldest
first:

| Message | What happens |
|---|---|
| Died `max_deaths` times or more | **Parked** in `<dlq>.parking`, with the usual parking headers |
| Dead for at least `backoff_minutes × 2^(deaths − 1)` | **Replayed** (moved) to the queue it died in, read from its x-death history; else to the DLQ's configured replay target (`QUEUELENS_REPLAY_TARGETS_JSON`) |
| Not dead that long yet | Waits for a later run |
| No death history, or nowhere to replay it | Skipped, and counted in the run's result |

With `backoff_minutes` 5 and `max_deaths` 3, a message is retried 5 minutes after its
first death and 10 minutes after its second, then parked at its third.

**Counting deaths.** RabbitMQ 3.x keeps adding to the x-death of a message that QueueLens
replays, but 4.x starts it again at 1. So every replay stamps the count so far in
`x-queuelens-deaths`, and a message that comes back with it has died at least once more.
That's how a message that keeps failing on 4.x still reaches `max_deaths` and is parked.

## Guard rails

- **At most `cap` messages per run**, never above the bulk limit. A big backlog drains
  over several runs.
- **No consumers, no replay.** A target queue with 0 consumers is held back: the message
  would only die again. The run notes it, and it isn't counted as a failure.
- **Pause after failures.** Three failed runs in a row disable the policy and send an
  Alert through the configured alert channels, as well as an in-app notification. A run
  fails when publishes fail, or when the policy's environment or vhost has been removed.
- **Admin-only.** Only Admins create, change, run or re-enable a policy, since it moves
  messages with no person in the loop. Operators can see policies, preview a run, and
  pause one.

A run goes through the same path as a bulk action: a dry run, publish-before-ack, the
per-queue lock (on PostgreSQL, across every replica), and an audit row per message as
the user `policy:<name>`, plus a `run_replay_policy` row whenever a run moved anything.
Creating, changing, pausing and deleting a policy are audited too.

That's the policy's history. **History** on the Replay Policies screen opens the Audit Log
showing only `policy:<name>`'s actions: each run with its counts, and every message it
replayed or parked. The API equivalent is `GET /api/audit?username=policy:<name>`. Runs
that moved nothing (all waiting, or held back for lack of consumers) aren't recorded;
the **Last Run** column shows the latest one.

## Where policies run

A policy belongs to the **environment and vhost it was created in**: the ones the console
tab (or the `X-QueueLens-Environment` / `X-QueueLens-Vhost` headers) named. It always
runs there, and its queue is checked there when it's changed, whichever tab you're
looking from. The list shows it under **Runs In**. Policies from before 0.18 belong to the
default environment and vhost, where they always ran.

Only the replica that leads the alert engine runs policies (with one replica, that's
the one). Policies are checked every 30 seconds. **Preview** reads the DLQ and shows what a run
would do, moving nothing; **Run now** runs one immediately.

## Metrics

| Metric | |
|---|---|
| `queuelens_policy_runs_total{policy, result}` | runs: `idle` (nothing due), `success`, `partial` (some publishes failed), `failed` (its environment or vhost is gone) |
| `queuelens_policy_messages_total{policy, outcome}` | messages `replayed`, `parked`, `failed`, or `held` (no consumers) |
| `queuelens_policy_paused{policy}` | 1 while a policy has paused itself; read from the database, so every replica reports it |

The bundled rules alert on a paused policy (`ReplayPolicyPaused`,
[ALERTING.md](ALERTING.md)).

## API

| Method | Path | Who | |
|---|---|---|---|
| `GET` | `/api/policies` | anyone | policies with `last_run_at`, `last_result`, `next_run_at` |
| `POST` | `/api/policies` | Admin | `{"name", "queue", "max_deaths", "backoff_minutes", "interval_minutes", "cap", "enabled"}`, in the request's environment and vhost; `404` for an unknown queue there |
| `PUT` | `/api/policies/{id}` | Admin | the same body; the queue is checked in the policy's own scope, `409` if that is gone |
| `PATCH` | `/api/policies/{id}` | Operator to pause, Admin to enable | `{"enabled"}` |
| `DELETE` | `/api/policies/{id}` | Admin | messages stay where they are |
| `POST` | `/api/policies/{id}/preview` | Operator | what a run would do now; moves nothing |
| `POST` | `/api/policies/{id}/run` | Admin | run now; returns the counts |

A quorum DLQ that isn't safe to browse (a delivery limit, or deeper than the browse
depth) answers `409`, as browsing it would.
