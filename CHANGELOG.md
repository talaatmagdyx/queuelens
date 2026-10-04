# Changelog

## v0.18.1 — 2026-10-04

### Added
- **A replay policy's history.** **History** on the Replay Policies screen opens the Audit
  Log showing only that policy's actions: each run with its counts, and every message it
  replayed or parked.
- **The Audit Log can show one account's actions.** It fetches them from the server, so
  they aren't limited to the latest 500 actions of everyone. **Clear Filters** goes back
  to all accounts.

### Upgrade notes
- **No breaking changes, and no schema changes.**
- **Helm chart 0.5.1** deploys QueueLens 0.18.1.

## v0.18.0 — 2026-10-04

### Fixed
- **A replay policy could act on the wrong broker.**
  - **The problem:** the Policies screen offered the DLQs of the tab's environment and
    vhost. The server checked the queue, and later ran the policy, against the default
    ones. So a policy created while looking at `staging` moved messages in the default
    environment's queue of the same name. Preview and Run now did the same.

### Changed
- **A replay policy belongs to the environment and vhost it was created in.**
  - **Where it runs:** always in that environment and vhost, from any tab. Its audit rows
    name them, and the list shows them under **Runs In**.
  - **Editing:** checks the queue in the policy's own environment and vhost.
  - **Existing policies:** they belong to the default environment and vhost, where they
    always ran.
- **A removed environment or vhost counts as a failed run.** After three, the policy
  pauses and notifies. A new `failed` value of `queuelens_policy_runs_total{result}`
  counts these runs.
- **One policy that can't run no longer stops the others in the same pass.**

### Upgrade notes
- **No action needed.** Two columns are added to `replay_policies` at startup. Existing
  policies keep running where they always did: the default environment and vhost.
- **Check your policies if you use several environments or vhosts.** A policy created
  before this release while looking at another one ran in the default environment and
  vhost. It still does, and the new **Runs In** column now says so. If you meant it for the
  other environment, recreate it from a tab on that environment and delete the old one.
- **Helm chart 0.5.0** deploys QueueLens 0.18.0.

## v0.17.2 — 2026-10-04

### Fixed
- **On RabbitMQ 4.x, replay policies never parked a message that kept failing.**
  - **The cause:** RabbitMQ 4.x starts x-death again at 1 when a client republishes a
    message. So every time a policy replayed a failing message and it died again, it
    looked like its first death.
  - **The effect:** the message never reached `max_deaths`, and its backoff never grew.
    It was replayed every `backoff_minutes`, without end.
  - **The fix:** every replay now stamps the death count so far in a new
    `x-queuelens-deaths` header. The count combines that header with x-death.
  - **`"annotate": false`:** a replay sent this way still adds no `x-queuelens-*`
    header at all, including this one. So on 4.x, its death count starts again.
- **The same count is used everywhere:** the console's x-death column, the `min_deaths`
  filter, "select all matching", the CSV export, and the new `deaths` field on API
  messages.
- **A new integration test** sends a message to a consumer that rejects it every time,
  on RabbitMQ 3.13 and 4.1, and checks that the policy parks it at the third death.

### Upgrade notes
- **If you run replay policies on RabbitMQ 4.x, upgrade.** Before this release, a message
  that kept failing was replayed forever. Messages already caught in that loop start
  counting from the upgrade, so they are parked within `max_deaths` more failures. Their
  earlier deaths were never recorded.
- **New header:** replays add `x-queuelens-deaths`. API messages gain a `deaths` field.
- **Helm chart 0.4.2** deploys QueueLens 0.17.2.

## v0.17.1 — 2026-10-04

### Added
- **Replay policy metrics:**
  - `queuelens_policy_runs_total{policy,result}`: one count per run, with `result` one
    of idle, success or partial.
  - `queuelens_policy_messages_total{policy,outcome}`: messages counted by outcome
    (replayed, parked, failed, or held because the target had no consumers).
  - `queuelens_policy_paused{policy}`: 1 while a policy has paused itself after failed
    runs.
- **`ReplayPolicyPaused` Prometheus alert rule** in deploy/prometheus/alerts.yml. It has
  a promtool test and is covered by the alerting pipeline check.
- **The acceptance suite covers replay policies.** It checks:
  - previews and Admin-only creation;
  - holding back replays when the target has no consumers;
  - replays to the origin queue, each audited as `policy:<name>`;
  - parking at the death limit;
  - who may pause a policy and who may re-enable it;
  - the new metrics.
- **A Replay Policies screenshot** in the README and on the site.

### Fixed
- **Audit Log table:**
  - A long user name such as `policy:orders retry` no longer squeezes the Queue column
    until it breaks names mid-word.
  - The table no longer pushes the row's details button off the card.

### Changed
- **docs/ARCHITECTURE.md is up to date.** It now covers:
  - per-request environments, snapshots and queue locks;
  - Basic Auth and SSO;
  - how replicas coordinate on PostgreSQL;
  - the background tasks;
  - replay policies.

### Upgrade notes
- **No breaking changes.** There is nothing to migrate.
- **Helm chart 0.4.1** deploys QueueLens 0.17.1.
- **To get the new alert,** add the `ReplayPolicyPaused` rule from
  deploy/prometheus/alerts.yml to your Prometheus rules.

## v0.17.0 — 2026-10-04

### Added
- **Replay policies: QueueLens retries a DLQ by itself** (docs/POLICIES.md).
  - **What a run does:** each run replays the messages that are due back to the queue
    they died in (read from their x-death, else the DLQ's configured target), and parks
    the ones that died too often.
  - **Backoff:** a message is due after `backoff_minutes × 2^(deaths − 1)`.
  - **Guard rails:** a cap per run; no replay into a queue with no consumers; the policy
    pauses after 3 failed runs and notifies the alert channels; only Admins may create,
    change, run or re-enable a policy.
  - **Same path as a person's bulk run:** dry run, publish-before-ack, the per-queue
    lock, and a per-message audit trail as `policy:<name>`.
  - **Where it runs:** only on the replica that leads the alert engine, against the
    default environment.
  - **Console:** a Replay Policies screen with Preview and Run now.

### Upgrade notes
- **No breaking changes.** Replay policies are opt-in: nothing moves until an Admin
  creates one. They use a new `replay_policies` table, created at startup.
- **Helm chart 0.4.0** deploys QueueLens 0.17.0.
- **New audit actions:** `create_replay_policy`, `update_replay_policy`,
  `delete_replay_policy`, `run_replay_policy`, `pause_replay_policy`. Runs act as the
  user `policy:<name>`.

## v0.16.1 — 2026-10-04

### Fixed
- **The Audit Log showed every action it didn't know as "Replay (Move)".** Removing a
  user, changing a role, exporting a snapshot and adding, removing or switching an
  environment all read as message replays. Each action now has its own label and icon,
  an unknown one shows its own name, and the action filter has Publish, Export, Users and
  Environments. The target column says which account or environment changed.
- **Inviting a user and changing your password weren't audited.** They now write
  `invite_user` (with the role) and `change_password` rows, never the password.
- **Configuration changes weren't audited**, so an admin could shorten retention or
  redirect alert delivery without a trace. Changing settings now writes `update_settings`
  with the keys that changed. Values are recorded only for `retention` and `limits`;
  channels and custom headers can carry credentials, so only their key is. Creating,
  editing, toggling and deleting an alert rule writes `create_alert_rule` /
  `update_alert_rule` / `delete_alert_rule`. The Audit Log labels them and filters them
  as Configuration.
- **The Audit Log's details panel claimed "Published First"** for actions that move no
  message, and offered "View Message" without one.
- **The Audit Log table overflowed its card** by about 130 px; times, actions and queue
  names now wrap, queue names at their dots.
- The README and landing-page screenshots are fresh from this release.

## v0.16.0 — 2026-10-04

### Added
- **Admins can change roles, deactivate, reactivate and remove accounts.** Until now
  nothing could take away an invited user's access short of editing the database.
  - Users page: a role menu, Deactivate / Reactivate and Remove on every local account.
  - API: `PATCH /api/users/{username}` (`role`, `active`) and `DELETE /api/users/{username}`.
  - Each change is audited (`update_user` with `new_role` / `active`, and `delete_user`)
    and applies on the account's next request, on every replica.
  - A deactivated account is refused through SSO too.
  - Your own account and accounts set by environment variables can't be changed this way.
- `GET /api/users` reports `managed` (`env` or `local`) and `must_change_password`.
- **Select all matching.** With a page fully selected, "Select all N matching" selects
  every message in the snapshot that the current filters match. The bulk dry run takes
  `match` (the filters) with `snapshot`, and the server picks the messages. More than the
  bulk limit is refused, and the console says so before you try.
- **Export a snapshot** as JSON or CSV (the Export menu, or
  `GET /api/queues/{q}/snapshots/{id}/export`), with the page filters applied. Messages
  are masked as the console shows them, CSV cells can't open as formulas, and every
  export is audited.
- **The Helm chart is published to GHCR** on every release:
  `helm install queuelens oci://ghcr.io/talaatmagdyx/charts/queuelens --version 0.3.0`. A
  tag must match both the app version and the chart's `appVersion` before any image is
  pushed, and a published chart version is never replaced.
- **The Helm chart adds a PodDisruptionBudget with more than one replica**
  (`maxUnavailable: 1`; `podDisruptionBudget.minAvailable`, or `enabled: false`). One
  replica gets none.
- **CI tests two replicas on a throwaway PostgreSQL in kind.** They start together on an
  empty database, share the login limiter and alert rules, elect one alert leader, and
  survive a rollout restart.

### Fixed
- The Users page marked an account "Invited" only when it was inactive, which never
  happened. Now "Invited" means the one-time password hasn't been replaced yet, and
  "Deactivated" means switched off.
- The Users page's row menu button did nothing; it's replaced by the actions above.
- **The Alerts table overflowed its card** by about 400 px. Channel chips now show just the
  channel (unconfigured ones in amber, with the details in a tooltip), and the rule and
  condition cells wrap.
- **The acceptance suite's failed-login timing check failed on shared CI runners**, where
  bursts of load land on one account's samples. It now compares the fastest of 7
  interleaved attempts per account; the code paths were equal all along (36 vs 36 ms locally).

### Upgrade notes
- **No breaking changes.**
- **Helm chart 0.3.0** deploys QueueLens 0.16.0. The first chart package on GHCR starts
  private: the owner makes `charts/queuelens` public once in its package settings.
- **New audit actions:** `update_user`, `delete_user`, `export_snapshot`.

## v0.15.0 — 2026-10-03

### Added
- **More than one replica, on PostgreSQL.**
  - **One queue at a time.** A per-queue advisory lock keeps two replicas from reading
    or acting on one queue at once. Side by side, two scans each saw half the queue; with
    the lock they take turns and each sees all of it.
  - **One alert leader.** An advisory lock elects the replica that evaluates the alert
    rules; another takes over within one interval if it dies.
  - **A shared login limit.** Failures go in a table (hashed keys) and count on every
    replica.
  - **Settings sync.** Runtime environments and the audit-stream switch reach every
    replica within 5 s, and a removed environment stops working everywhere.
  - **The Helm chart** takes `replicaCount` with persistence off. It then rolls updates
    one pod at a time and pins clients to a pod (`sessionAffinity: ClientIP`), because
    browse snapshots stay on the replica that took them.

### Fixed
- **Replicas starting together on an empty PostgreSQL crashed** on concurrent
  `CREATE TABLE` (`UniqueViolation`). Schema creation now takes an advisory lock, and
  seeding runs one replica at a time.
- **A password changed on one replica took up to a minute to stop working on another**
  (the login cache). The cache now checks the stored password hash on every request.
- **The bundled DLQ alerts fired once per replica, and action failures weren't added up
  across replicas.** The rules take `max by (queue)` and `sum by (action)`.

### Upgrade notes
- **No breaking changes** for one replica, on SQLite or PostgreSQL. The login limiter
  uses a new `login_failures` table, created at startup.
- **More than one replica** needs PostgreSQL, `replicaCount` with
  `persistence.enabled: false`, and sticky sessions at the ingress.
- **If you copied the bundled alert rules,** take the new expressions. Their alerts lose
  the `instance` and `job` labels (and `result` for action failures).

## v0.14.0 — 2026-10-03

### Added
- **SSO behind an authenticating proxy** (#2). Set `QUEUELENS_AUTH_PROXY_HEADER` and
  oauth2-proxy, Authelia or an SSO ingress names the signed-in user. The audit log then
  records real people, and roles come from local accounts, then from groups
  (`QUEUELENS_AUTH_PROXY_ROLES_JSON`), then a default. The header counts only from
  `QUEUELENS_TRUSTED_PROXIES`, and Basic Auth keeps working. See docs/SSO.md.
- **Helm chart** (#5): `deploy/helm/queuelens` runs QueueLens on Kubernetes. It runs one
  replica (`Recreate`), keeps `/app/data` on a PVC, and puts readiness on `/ready`; an
  Ingress and a ServiceMonitor are optional. Credentials come from an existing or a
  chart-created Secret with no defaults, so an install without them fails. Hooks let a
  sidecar such as oauth2-proxy sit in front. CI lints the chart, validates it with
  kubeconform and installs it in kind. See docs/KUBERNETES.md.
- **Alertmanager example for the bundled rules** (#6). `deploy/alertmanager/alertmanager.yml`
  sends critical alerts to a webhook and Slack, and warnings to Slack: one message per
  rule and DLQ, resolved ones included. Both URLs are read from files, and
  `deploy/prometheus/prometheus.yml` now points at it. `scripts/test_alerting.py` (CI job
  `alerting`) tests the rules with promtool, the routing with amtool, and delivery
  through a real Alertmanager. See docs/ALERTING.md.

### Changed
- `X-Forwarded-For` / `-Proto` are applied by QueueLens from `QUEUELENS_TRUSTED_PROXIES`
  (default `127.0.0.1,::1`, as before), and the image runs uvicorn with
  `--no-proxy-headers`. If you set uvicorn's `FORWARDED_ALLOW_IPS`, move the value to
  `QUEUELENS_TRUSTED_PROXIES` (`*` is no longer accepted).
- The image's `queuelens` user is pinned to uid 999, which the Helm chart runs as.
- Alert descriptions in `deploy/prometheus/alerts.yml` round their values: `increase()`
  and `delta()` extrapolate, which gave messages like "3.2142857142857144 failed actions".
- `deploy/prometheus/prometheus.yml` sends its alerts to `alertmanager:9093`.

### Upgrade notes
- **If you set uvicorn's `FORWARDED_ALLOW_IPS`,** move the value to
  `QUEUELENS_TRUSTED_PROXIES`, which takes IPs and CIDRs only.
- **If you run uvicorn yourself and want SSO,** pass `--no-proxy-headers` as the image
  does. Otherwise identity headers from a loopback proxy are ignored.
- **SSO is off** until `QUEUELENS_AUTH_PROXY_HEADER` is set.
- **If you copied `prometheus.yml` and run no Alertmanager,** drop its `alerting:` block.

## v0.13.0 — 2026-10-02

### Added
- **PostgreSQL as the datastore** (#1): set `QUEUELENS_DATABASE_URL` to
  `postgresql+asyncpg://...`. The driver is in the image, `docker-compose.postgres.yml` adds
  a database to the bundled compose, and CI runs the unit suite's persistence tests and a
  full acceptance run on PostgreSQL 17. SQLite stays the default. Still one replica: the
  per-queue locks and browse snapshots live in the process.
- `python -m app.copy_db SOURCE_URL [TARGET_URL]` moves an existing install's users, alert
  rules, settings and audit history across, in one transaction, with row counts checked.

### Fixed
- **Two simultaneous executions of a bulk dry run could both run it** on SQLite, which
  ignores `SELECT ... FOR UPDATE`. In the app a lock already kept them apart; the token
  is now taken with one `DELETE ... RETURNING`, safe on a shared database too.
- An alert's fired / recovered flip is a compare-and-set, so overlapping evaluations
  notify once.
- Audit fields longer than their column are clipped (PostgreSQL would refuse the row,
  and an attempt that can't be audited is refused).

### Upgrade notes
- **No breaking changes.** SQLite installs carry on as they are.
- **New dependency:** `asyncpg` (in the image already; `pip install .` pulls it).
- **Moving to PostgreSQL:** stop QueueLens, run `python -m app.copy_db` once into the empty
  database, then start it. Keep the same `QUEUELENS_SECRET_KEY`. See
  [docs/OPERATIONS.md](docs/OPERATIONS.md#moving-to-postgresql).
- **Still one replica**, on either database.

## v0.12.1 — 2026-10-02

### Added
- **`docker compose up` ships the demo dead-letter queues the README promised.** The new
  one-shot `demo` service runs `python -m app.demo`, which dead-letters realistic messages
  into five DLQs: real `x-death` (on RabbitMQ 3.x some died 3 or 5 times; 4.x restarts the
  count for a republished message), gzip and plain-text bodies, and
  a quorum DLQ with a delivery limit to show the "not browsable" badge. It does nothing
  when they already exist. Until now the quickstart started an empty broker.
- `scripts/screenshots.py` regenerates the README and landing-page screenshots from a
  real console, and all nine are new.
- Audit rows record the acting user's role (`metadata.role`: Admin / Operator / Viewer), so
  the log says with which rights an action was taken, not only by whom (#3).

### Fixed
- **Alert rules' "last triggered" read three hours (your UTC offset) in the past.** The
  Alerts screen parsed the stored UTC time as local time.
- The dashboard and queue list still said "message preview is limited to 100 per queue";
  since 0.12 a queue is read once, down to the browse depth.
- The snapshot banner showed its time in local 12-hour format beside UTC message times; it
  is UTC now too.
- The quorum delivery-limit integration test could miss the loss it guards against. It
  polled the message count on one robust channel, and a robust channel hands back the
  cached `Declare-Ok` of its first declare. A message lost after that first look would
  still read as present. It now counts on a fresh channel each time.

## v0.12.0 — 2026-10-02

### Added
- **Deep browsing (#4).** The Messages screen pages through the whole queue, down to a
  browse depth (`QUEUELENS_MAX_BROWSE_DEPTH`, default 5000, adjustable under Limits).
  - One scan builds a short-lived snapshot, and pages, search, the payload-format filter
    and "x-death ≥ 3" are served from it, with no further broker reads or deliveries.
  - Anything a snapshot shows can be acted on: single and bulk actions picked from it scan
    down to the message instead of a fixed 100-message window.
  - API: `GET /api/queues/{q}/messages?snapshot=new|<id>&offset=&limit=&contains=&payload_format=&min_deaths=`,
    plus an optional `snapshot` on actions and bulk dry runs.

### Fixed
- **Browsing a quorum queue no longer reorders it.** A quorum queue puts returned messages
  at the back, so every preview rotated what it read to the tail. Each refresh showed
  different messages, and the queue's order drifted with every look. Quorum queues are now
  read whole and requeued in order, which leaves them as they were. One deeper than the
  browse depth, or larger than 64 MiB, is refused with an explanation instead.
- **Actions on quorum DLQs can find the message again.** A quorum queue stamps
  `x-delivery-count` on every redelivery, and fingerprints hashed it, so a message got a
  new identity each time it was previewed. Replaying, parking or deleting a previewed
  quorum message always failed with "not found uniquely". That header is now left out of
  fingerprints.
- The x-death count in the message table counts deaths, not x-death entries. One entry per
  queue and reason carries its own count, so a message rejected five times from one queue
  used to show 1.
- The preview banner said "latest" messages. A preview shows the head of the queue, the
  oldest messages.

### Upgrade notes
- **Quorum DLQs are now always read whole,** on every preview and action. Before, they
  were browsed in part, which reordered them.
  - A quorum queue holding more than the browse depth (default 5000) or more than 64 MiB
    now answers `409` until it drains.
  - Fixes: raise `QUEUELENS_MAX_BROWSE_DEPTH` (Limits allows up to 50 000), or shovel the
    queue to a classic one.
  - Previews of large quorum DLQs take longer, because they read the whole queue (about
    2 s per 5000 messages locally).
- **Fingerprints change once** for messages carrying `x-delivery-count`, which on quorum
  queues means any message that has been redelivered.
  - Bulk dry runs on quorum queues created before the upgrade find nothing. Run the dry
    run again.
  - Audit rows written before the upgrade keep the old fingerprints.
- A bulk selection larger than the bulk limit is now refused with `400`. Before, it was
  silently cut to the scan window.
- New setting: `QUEUELENS_MAX_BROWSE_DEPTH`. Additive API: the snapshot query parameters,
  `snapshot` on actions and bulk dry runs, and `max_browse_depth` in `/api/config`.

## v0.11.0 — 2026-10-02

### Changed
- **Environments are chosen per request, not per instance.** Each request names its
  environment and vhost (`X-QueueLens-Environment` / `X-QueueLens-Vhost`; default
  environment without them), and the console keeps the choice per tab. One operator's
  switch no longer re-points everyone else's views and actions, and two vhosts can be
  browsed at once. `POST /api/environments/activate` now only checks a scope is reachable;
  the "Environment switched" notification is gone (nothing changes for anyone else).
- An environment in use can be removed; its connections close and requests naming it get
  `404`. The Metrics screen shows the tab's environment; `/metrics` and alert rules use the
  default one.

### Fixed
- **A bulk run that the broker stopped midway no longer hides what it already did.** A
  refused publish (e.g. another user's `user_id`) or a dropped connection closes the
  channel. The batch used to answer `502 Bulk operation failed`, while the messages before
  that point had already moved, without per-message audit rows. Now the batch stops, the
  response reports every outcome (`failed` with RabbitMQ's reason, `not_attempted` for the
  rest, which stay queued), and each one is audited. The result is `partial`.
- **Replays keep `expiration` to the millisecond.** aio-pika decodes the TTL to float
  seconds and truncates it when re-encoding, so some values changed (`"1001"` became
  `"1000"`, `"65526"` became `"65525"`). This affects aio-pika 9 and 10.
- **A replay the broker refuses explains itself.** `409` carries RabbitMQ's reason instead of
  a bare `502 Message operation failed`, including the hint for `user_id`: RabbitMQ only
  accepts another user's `user_id` from that user or an `impersonator`. Nothing changes.
- Describing a broker error can no longer break a failure path. aiormq 7's
  `DeliveryError.__str__` raises when the error carries no frame. That could abort a whole
  bulk batch with a 502, and it lost the error text from failed actions' audit rows.

### Safety
- Audit rows for broker actions record `metadata.environment` and `metadata.vhost`.
- A bulk dry run executes only in the environment/vhost it scanned. The batch store is
  shared, and a same-named queue elsewhere can hold identical messages.

### Security
- The verified-login cache key is an HMAC-SHA256 under a random per-process key, instead of
  SHA-256(pepper + password). It never stored passwords, but a keyed lookup is the right
  construction.
- CI workflows run with a read-only token by default; only the image publish job can write
  packages.
- CodeQL code scanning (Python, the console's JavaScript, the workflows) runs on every PR.
  Dependabot proposes weekly updates for Python packages, Actions and Docker base images.

### Dependencies
- **aio-pika 10 / aiormq 7 are supported** (`aio-pika>=9.4,<11`). The suite and the
  acceptance run pass on 9.6 and 10.1.
- `cryptography<51`. For development: pytest 9, pytest-asyncio 1.x, mypy 2, and newer
  GitHub Actions.

### Docs
- README and site say where "browsing never consumes" stops: quorum queues with a delivery
  limit, which QueueLens refuses to browse.

### Upgrade notes
- **Scripts that called `POST /api/environments/activate` to point the instance at another
  environment must now send `X-QueueLens-Environment` / `X-QueueLens-Vhost` on their
  requests.** `activate` only checks reachability, and requests without the headers use the
  default environment.
- Bulk dry runs created before the upgrade carry no environment, so they are refused at
  execute. Run the dry run again.
- Additive API fields: `default` on `/api/environments` entries, the `not_attempted`
  per-message status and summary key on bulk execute, and `metadata.environment` /
  `metadata.vhost` on audit rows.

## v0.10.2 — 2026-10-01

### Fixed
- PagerDuty `dedup_key` is per incident (`queuelens-rule-<id>-<fired at>`). Alert-rule ids
  are reused after a delete, so a new rule could merge into — or resolve — a deleted rule's
  still-open incident.

### Security
- Invited accounts must replace their one-time password before anything else: every
  endpoint except `/api/me` and `POST /api/users/me/password` answers `403` until then,
  and the console shows only the password form.

### Added
- The queue list reports each quorum queue's effective `delivery_limit` and whether it is
  `browsable`; the console badges "not browsable" queues (with the fix) on the Dashboard,
  Queues and queue-detail screens — no more discovering the refusal by clicking.
- `tests/acceptance/run.py`: the black-box acceptance run (real broker, real QueueLens
  process, ~200 checks across every feature group), run in CI on RabbitMQ 3.13 and 4.1.

### Performance
- Successful Basic-auth checks for database users are remembered for 60 s (keyed hash, never
  the password; cleared on password change) — PBKDF2 no longer runs on every request
  (~34 ms → ~2 ms per request).

## v0.10.1 — 2026-10-01

### Security / data safety
- **RabbitMQ 3.x: `x-delivery-limit: -1` is no longer treated as unlimited.** On 3.x it drops
  a message on its first return, so 0.10.0 allowed previews that destroyed messages there.
- **RabbitMQ 4.x: a `-1` from the policy (or argument) no longer cancels a real limit from
  the other source** — the lowest non-negative value wins, as the broker does.
- Quorum queues are refused until their first statistics (and applied policy) are visible.
- Invite emails no longer contain the initial password — the inviting admin sees it once
  and hands it over directly.
- `/docs`, `/redoc` and `/openapi.json` require the same authentication as the API.

### Added
- CI runs the integration suite against RabbitMQ 3.13 **and 4.1**, including a real-broker
  check that previews never cost a quorum queue a message.
- Quiet hours follow a configurable time zone (`ui.quiet_tz`, IANA name, validated on save;
  default UTC) — picker in Alerts → Quiet Hours.
- PagerDuty Events API v2 routing key can be set in the UI (write-only, like the URLs), and
  PagerDuty can be selected as a rule channel when creating alert rules.

## v0.10.0 — 2026-10-01

### Security / data safety
- **Quorum DLQs with a delivery limit are no longer browsed.** A preview is basic.get +
  requeue, which quorum queues count as a delivery — previewing could drop messages, and
  the console previewed the largest DLQ on every page load and 30s auto-refresh. Previews,
  detail lookups, single actions and bulk scans now refuse such queues with `409`, and the
  console fetches message bodies only when a screen asks for them.
- Bulk execute and composer publish now write their `started` audit event before touching
  the broker (no audit → no action), like single actions always did.
- Slack/webhook/PagerDuty URLs and the PagerDuty routing key are write-only in
  `GET /api/settings` (they were readable by every role).
- SMTP TLS now verifies the server certificate and hostname.
- Activating an environment only accepts its listed vhosts — an Operator could create
  arbitrary vhosts (and grant permissions) on the broker by switching.
- The raw `payload_encoded` view is withheld when masking hid a value or the payload was
  truncated.
- Failed logins take the same time for unknown and known usernames; throttling is per
  (IP, username) plus an IP-wide ceiling.
- Audit CSV export neutralises spreadsheet formulas.

### Fixed
- Dashboard summary cards show live numbers (they were hard-coded sample values).
- `?limit` on previews can only lower the configured cap; bulk execution scans the same
  window its dry run approved; single actions use the same re-fetch window as detail lookup.
- Topology cache is cleared on environment switch.
- `add_environment` audit events name the acting admin (not the AMQP username).
- PagerDuty: one incident per rule (`dedup_key`), resolved on recovery; recoveries follow the
  rule's severity for quiet hours.
- Bulk replay/park stamp the same provenance (and custom) headers as single actions.
- Audit events record the request's IP and user agent.
- Concurrent previews of one queue no longer see partial results (per-queue scan lock).
- Startup failures report their real cause (no masking `UnboundLocalError`).
- Users screen shows Viewers as Viewers.

### Added
- Composer: per-message headers and properties (`message_id`, `correlation_id`, …).
- `queuelens_alert_deliveries_total{channel,result}` metric.
- `deploy/prometheus/prometheus.yml` example scrape config.
- UI: delete is type-to-confirm; bulk flows show the server's dry-run result before executing;
  icon-only buttons have accessible names.

### Removed
- **The legacy server-rendered console.** Every old page (`/config`, `/queues`,
  `/audit`, `/classic`, …) now 301-redirects to the SPA at `/app`, which has
  been the default and the only UI receiving features for several releases.
  The Jinja templates are gone except for the standalone error page.
- The `outputs/` scoping-notes folder.

## v0.9.0 — 2026-07-11

### Added
- **Topology caching**: the exchanges/bindings/queues snapshot (three
  management-API calls) is now served from a 30-second cache — part of the
  documented "light on your broker" contract.
- README and the landing page now document how QueueLens avoids loading the
  broker, plus a feature-status table (Stable / Experimental / Roadmap).
- **Compressed-payload decode**: messages with `content_encoding: gzip` or
  `deflate` now display their inflated payload (JSON pretty-printed), with a
  decoded/encoded toggle showing the original bytes as base64. Decompression
  is capped at 4 MiB (zip-bomb guard) and is display-only — replay publishes
  the original compressed body unchanged.
- **Metrics screen in the console**: live `queuelens_*` values (broker status,
  DLQ backlog per queue, action counters, average broker-operation latency),
  a ready-to-paste Prometheus scrape config, and the bundled alert rules with
  copy buttons. Backed by `GET /api/metrics/summary` and
  `GET /api/metrics/alert-rules`.

## v0.8.0 — 2026-07-11

### Added
- **Precompiled front end in the container image**: JSX is compiled at image
  build (two-stage Dockerfile) and Babel standalone is removed from the
  browser entirely — faster first paint, CSP-compatible. Local development
  still uses in-browser compilation; the design-system bundle can be built
  anywhere with `python scripts/build_ds_bundle.py`.
- **Durable state**: bulk dry-run tokens now live in the database (they
  survive restarts), and alert fired-state persists on the rule — a restart
  no longer re-sends notifications for conditions that already fired.
- **Full-history audit export**: `GET /api/audit/export?format=csv|json`
  streams the complete audit log; the UI export button now uses it.
- **Browser e2e smoke suite** (Playwright) running in CI against a real
  broker: dashboard, all screens, wizard confirmation gating.
- GHCR images now also carry `vX.Y.Z`-style tags alongside `X.Y.Z`.
- Rewritten README with a fresh screenshot tour of the console.
- App version is now kept in sync between `pyproject.toml` and the API
  (both previously reported 0.5.0).

## v0.7.0 — 2026-07-11

The platform release: everything configurable is now server-backed and real.

### Added
- **Alert engine**: rule CRUD, background evaluation against live queue stats,
  fire + recovery notifications, per-channel delivery with retry (email via
  SMTP incl. auth/TLS, Slack, PagerDuty Events v2, generic webhook), quiet
  hours, test buttons.
- **Multi-environment support**: profiles via `QUEUELENS_ENVIRONMENTS_JSON`
  or created in the UI (own broker host + split AMQP/management credentials),
  vhosts created on first activation, live Set Active, every switch audited
  and broadcast.
- **Server-backed settings**: custom headers (stamped on every publish),
  preview/refetch/bulk limit overrides applied server-side, retention with
  hourly pruning, audit-to-stdout streaming, export format.
- **Users**: DB-backed accounts, Invite User with one-time password (emailed
  when a channel is configured), self-service password change.
- **RBAC**: Viewer (read-only) / Operator (recover) / Admin (delete,
  configuration, users) enforced server-side; `/api/me`.
- **Auth hardening**: failed-login rate limiting (10/min per IP).
- **Secrets at rest**: optional Fernet encryption via `QUEUELENS_SECRET_KEY`;
  all stored secrets are write-only through the API.
- New screens: Parking, Queue Detail, Topology, Composer, Alerts; ⌘K palette.
- `.env` file support + documented `.env.example`; configuration precedence docs.
- Container: non-root user, `HEALTHCHECK`.

### Changed
- SPA is fully live-data driven; wizard executes real actions with honest
  failure states; audit log records full context (source, target, mode) for
  failures too.

### Upgrade notes
- The container now runs as a non-root user. Volumes created by older
  releases are root-owned — run once before upgrading:
  `docker compose exec -u root queuelens chown -R queuelens /app/data`
  (fresh installs are unaffected).

### Known limitations
- Single-instance deployment only (in-memory dry-run tokens, alert state,
  env bundles) — see docs/OPERATIONS.md.
- Environment switching is instance-global (broadcast to all users).
- Alerts evaluate the active environment only.
- Front-end compiles JSX in the browser (Babel standalone); precompilation
  is on the roadmap.

## v0.5.0 and earlier

See GitHub releases.
