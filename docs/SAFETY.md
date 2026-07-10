# Safety Model

QueueLens operates on production dead-letter queues, so its core design constraint is:
**no operation may lose a message unless the user explicitly deleted it.** This document
states each guarantee and how the code enforces it.

## Guarantees and their mechanisms

### 1. Browsing never consumes messages
Preview uses `basic_get(no_ack=False)` and nack-requeues every fetched message in a
`finally` block (`MessageBrowser.list_messages`). If the process dies mid-preview, the
channel closes and the broker requeues everything itself — unacked deliveries are never lost.

*Side effect:* the broker marks previewed messages `redelivered`. This is inherent to
`basic_get` and does not affect message content or ordering guarantees consumers rely on.

### 2. Publish happens before ack — always
For move, park, and copy (`MessageOperator.operate`), the outgoing publish completes
**before** the original is acked (move/park) or requeued (copy). A failed publish leaves the
original unacked; the error path nack-requeues it explicitly, and if the channel already
died, the broker requeues on channel close.

### 3. An unroutable publish is a failure, not a silent drop
Two independent mechanisms:

- **Target verification** — park declares its durable `{queue}.parking` queue before
  publishing; replay queue targets are passively declared, so a missing target queue fails
  with `404` before anything is consumed.
- **Mandatory publish + returned-message errors** — channels are opened with
  `on_return_raises=True` and publishes are mandatory, so a message the broker cannot route
  (e.g. an exchange with no matching binding) raises `DeliveryError` → `400`, and the
  original stays in the DLQ.

Either mechanism alone closes the data-loss window; together they cover both queue and
exchange targets.

### 4. Ambiguity blocks mutation
Fingerprints are content-derived and can collide for byte-identical messages. Every mutating
action re-scans a bounded window and requires **exactly one** match; zero or multiple
matches → `409`, everything requeued, nothing changed. Correctness is chosen over
convenience: QueueLens refuses to guess which duplicate you meant.

### 5. Delete is explicit, twice
Delete (and every other action) requires `"confirm": true` in the API and a browser
confirmation dialog in the UI.

### 5b. Bulk actions cannot touch what you haven't seen
Bulk operations are two-phase (`BulkActionService`): the dry run records the exact
fingerprint set it observed behind a one-shot token; execute acts **only** on that set.
Messages that arrived after the dry run are ignored by construction. Additional guards:

- Hard cap: the scan window is `QUEUELENS_MAX_BULK_SIZE` (default 500); one batch executes
  at a time (an asyncio lock serializes executions).
- Per-message independence: each message publishes-before-acks on its own; an unroutable
  publish fails and requeues *that* message and the batch continues. A channel-level broker
  failure aborts the whole batch and the broker requeues everything unacked.
- Duplicates are skipped and reported (`skipped_duplicate`), never guessed at — same
  ambiguity rule as single actions, degraded gracefully instead of aborting the batch.
- Tokens expire (`QUEUELENS_BULK_DRY_RUN_TTL_SECONDS`) and live in process memory; an app
  restart voids them and execution fails safe with "run the dry-run again".
- Audit: one event per fingerprint plus a `bulk_<action>` envelope (`success`/`partial`).

### 6. Audit is a precondition, not a log line
Every action writes a `started` audit event **before** touching the broker and a
`success`/`failed` event after. If the attempt event cannot be persisted, the action is
rejected — an un-auditable action does not run.

### 7. Failures degrade honestly
Unknown queues → `404`. Broker down → `503`, and `/ready` reports it (connection liveness is
tracked via close/reconnect callbacks because `RobustConnection.is_closed` lies during
reconnection). Management API errors → `502`/`503`. No failure mode returns a fake success.

## Failure matrix

| Failure | When | Outcome |
|---|---|---|
| Source queue missing | Before scan | `404`, nothing consumed |
| Fingerprint matches 0 or 2+ | After scan | `409`, all messages requeued |
| Replay target queue missing | Before publish | `404`, all messages requeued |
| Target exchange routes nowhere | At publish | `400`, all messages requeued |
| Broker publish error | At publish | `502`, original requeued (by us or by channel close) |
| Process crash mid-action | Any point | Channel closes → broker requeues all unacked messages |
| Audit store down | Before action | Action rejected |
| Broker down | Any request | `503`, `/ready` fails |

## Known limits (Phase 1, by design)

- **Fingerprints are best-effort.** They identify messages within a preview batch and a
  bounded re-fetch window — not globally stable RabbitMQ IDs. On queues deeper than
  `QUEUELENS_REFETCH_WINDOW_SIZE`, a message beyond the window cannot be acted on.
- **Scan-and-requeue is O(window) per action** and briefly holds the scanned messages
  unacked. Fine for operator workflows; bulk operations will need a different design.
- **Masking is key-based and display-only.** Values under configured sensitive keys
  (`QUEUELENS_MASKED_FIELDS`) render as `***` in the UI and read API, but values containing
  secrets under other keys are not detected, and replayed messages carry the original,
  unmasked payload by design. Deploy inside a trusted network
  (see [OPERATIONS.md](OPERATIONS.md)).
- **Copy replay can duplicate.** By definition, copy leaves the original and creates a new
  message. Downstream consumers should be idempotent or use the
  `x-queuelens-original-fingerprint` header to deduplicate.

## Verification

These guarantees are exercised end-to-end against a real broker in
`tests/test_integration_rabbitmq.py` (real dead-lettering, failed replay to a missing queue
leaving the message intact, park creating its queue, provenance headers, audit pairs) and in
unit tests (`tests/test_actions.py`). CI runs both on every push.
