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

*Quorum queues with a delivery limit are refused.* AMQP 0-9-1 has no browse, and quorum
queues count every return as a delivery — `basic.nack`/`basic.reject` with requeue and a
channel closing with unacked messages alike, and on RabbitMQ 4.x even AMQP 1.0 `released`
and `modified` (not failed). There is no non-counting way to put a message back. Past the
limit the broker drops the message, or dead-letters it with `reason: delivery_limit`.
There, a preview *is* a destructive read, so `QueueService.assert_browsable` makes every
preview, detail lookup, single action, and bulk scan fail with `409` before any
`basic_get`. The console never previews on page load or auto-refresh — only when a screen
is opened.

How the effective limit is worked out (measured against RabbitMQ 3.13.7 and 4.1.8, and
covered by the integration suite on both in CI):

| Broker | Nothing configured | `-1` | Argument and policy both set |
|---|---|---|---|
| 4.x | default **20** (not shown by the Management API) | no limit from that source | lowest non-negative value wins |
| 3.x | no limit | **drops on the first return** — not unlimited | lowest value wins |

A quorum queue whose first statistics haven't arrived yet (a few seconds after
declaration — the applied policy only shows up with them) is refused too: unknown means no.

To browse a quorum dead-letter queue, give it no limit — on RabbitMQ 4.x:

```bash
rabbitmqctl set_policy dlq-unlimited '\.dlq$' '{"delivery-limit": -1}' --apply-to quorum_queues
```

(only one policy applies per queue — merge `delivery-limit` into an existing DLQ policy
instead of adding a second one). On 3.x, remove `x-delivery-limit` / the policy key.

*Quorum queues are read whole, and requeued in order.* Measured on RabbitMQ 3.13 and 4.1, a
quorum queue puts every returned message at the **back**; a classic queue puts it back where
it was. So browsing part of a quorum queue rotates the browsed messages to the tail.
QueueLens used to do this, which had two effects:
- Every preview reordered the queue, and each refresh showed the next messages.
- Actions couldn't find a message the preview had just shown.

Now every scan of a quorum queue (preview, snapshot, lookup, single or bulk action) reads
the whole queue and requeues it in the order it was read: a full rotation, which leaves the
order as it was. The guard refuses (`409`) a quorum queue holding more than the browse depth
or more than one scan's byte budget, before anything is read. The scan also re-checks the
count from its own passive declare, and stops if the queue grew past the depth mid-scan.

Each scan still counts one delivery per message, which is why only queues with unlimited
deliveries are browsable at all. A quorum queue also stamps `x-delivery-count` on every
redelivery; that header is left out of fingerprints, so a message keeps its identity
across looks.

Scans of one queue are serialized (`QueueLocks`): a scan holds messages unacked until it
requeues them, so two at once would each see part of the queue. On PostgreSQL the lock is
a transaction-level advisory lock that spans every replica. It's keyed by broker, vhost
and queue, and it ends with its transaction, so no failure path can leave one held.

*Deep browsing works from a snapshot.*
- One scan down to the browse depth (default 5000), or until it has read 64 MiB of bodies,
  is kept in memory for 5 minutes. That's raw bodies only, with payloads decoded per page,
  and at most 4 snapshots per process.
- Pages, search and filters come from that copy, so paging costs no broker reads and no
  deliveries.
- An action on a message picked from a snapshot scans down to the message's position. In a
  classic queue a message only ever moves up, so its position is an upper bound.
- "Select all matching" sends the filter, not a list: the server picks the matches from
  the snapshot, and the bulk limit and the dry run apply as for a hand-picked selection.
- An export hands over a snapshot's messages masked as the console shows them, and is
  audited with its message count.

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
Delete (and every other action) requires `"confirm": true` in the API; the UI makes you type
the queue name (bulk delete shows the server's dry-run count in the same prompt).

### 5b. Bulk actions cannot touch what you haven't seen
Bulk operations are two-phase (`BulkActionService`): the dry run records the exact
fingerprint set it observed behind a one-shot token; execute acts **only** on that set.
Messages that arrived after the dry run are ignored by construction. Additional guards:

- Hard cap: the scan window is the stored *Limits* override, else
  `QUEUELENS_MAX_BULK_SIZE` (default 500), never above 1000; the batch records its window and
  execution scans exactly that. One batch executes at a time (an asyncio lock).
- Per-message independence: each message publishes-before-acks on its own; an unroutable
  publish fails and requeues *that* message and the batch continues. When the broker closes
  the channel instead (it refused a publish, or the connection dropped), the batch **stops**
  there. The broker requeues everything still unacked, and the response still reports, and
  the audit still records, every message that already moved. That message is `failed` with
  RabbitMQ's reason, and the rest are `not_attempted` and stay in the queue.
- Duplicates are skipped and reported (`skipped_duplicate`), never guessed at — same
  ambiguity rule as single actions, degraded gracefully instead of aborting the batch.
- Tokens expire (`QUEUELENS_BULK_DRY_RUN_TTL_SECONDS`) and are stored in the database, so
  they survive restarts until then; an expired or used token fails safe with "run the
  dry-run again".
- A token executes only in the environment and vhost its dry run scanned. Environments are
  chosen per request and share the token store, and a same-named queue on another broker can
  hold identical (same-fingerprint) messages. A token presented elsewhere is spent and
  refused.
- Audit: a `bulk_<action>` `started` event before execution, one event per fingerprint, and
  a closing envelope (`success`/`partial`).

### 6. Audit is a precondition, not a log line
Every action — single, bulk, and composer publish — writes a `started` audit event
**before** touching the broker and a `success`/`failed` event after. If the attempt event
cannot be persisted, the action is rejected — an un-auditable action does not run.

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

- **Fingerprints are best-effort.** They identify messages within a scan, not as globally
  stable RabbitMQ IDs.
  - A message deeper than `QUEUELENS_REFETCH_WINDOW_SIZE` can be acted on when it was
    picked from a snapshot, which reaches down to the browse depth.
  - Messages deeper than the browse depth can't be reached until the queue drains or the
    depth is raised.
- **Scan-and-requeue is O(window) per action** and briefly holds the scanned messages
  unacked. Fine for operator workflows; bulk operations will need a different design.
- **Masking is key-based and display-only.** Values under configured sensitive keys
  (`QUEUELENS_MASKED_FIELDS`) render as `***` in the UI and read API, but values containing
  secrets under other keys are not detected, and replayed messages carry the original,
  unmasked payload by design. Deploy inside a trusted network
  (see [OPERATIONS.md](OPERATIONS.md)).
- **Replays gain a `message_id` when the original had none.** aiormq stamps a random id on
  every publish without one; it is how a broker return is matched to its publish (the
  mechanism behind guarantee 3). Everything else is copied as-is, including `expiration`
  to the millisecond. aio-pika on its own would truncate some TTLs (`"1001"` → `"1000"`).
- **A message carrying another broker user's `user_id` can't be replayed by QueueLens's
  user.** RabbitMQ validates `user_id` against the publishing connection unless that user
  has the `impersonator` tag. QueueLens copies `user_id` rather than silently dropping
  it, so the broker refuses the copy. The action answers `409` with RabbitMQ's reason, and
  nothing changes; in bulk, the batch stops there (see section 5).
- **Copy replay can duplicate.** By definition, copy leaves the original and creates a new
  message. Downstream consumers should be idempotent or use the
  `x-queuelens-original-fingerprint` header to deduplicate.

## Verification

These guarantees are exercised end-to-end against a real broker in
`tests/test_integration_rabbitmq.py` (real dead-lettering, failed replay to a missing queue
leaving the message intact, park creating its queue, provenance headers, audit pairs) and in
unit tests (`tests/test_actions.py`). CI runs both on every push.
