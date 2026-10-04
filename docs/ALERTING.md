# Alerting with Prometheus and Alertmanager

QueueLens exports metrics at `/metrics`, and
[`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml) turns them into five
alerts. Prometheus evaluates the rules and Alertmanager delivers them — QueueLens is not in
the delivery path.

| Alert | Fires when | Severity |
|---|---|---|
| `QueueLensBrokerDown` | `queuelens_rabbitmq_ready` is 0 for 5 minutes | critical |
| `DLQAboveThreshold` | a DLQ holds more than 1000 messages for 15 minutes | warning |
| `DLQGrowing` | a DLQ grew by more than 100 messages in 30 minutes | warning |
| `QueueLensActionFailures` | more than 3 failed actions in 15 minutes | warning |
| `ReplayPolicyPaused` | a replay policy paused itself after 3 failed runs ([POLICIES.md](POLICIES.md)) | warning |

The DLQ alerts carry a `queue` label.

## In-app alerts or Alertmanager?

QueueLens also has its own alert rules (the Alerts screen), with in-app notifications and
experimental email / Slack / PagerDuty / webhook delivery.

- **You already run Prometheus:** use Alertmanager. The rules live in git and are tested,
  and you get grouping, silences, inhibition and your existing on-call routing.
- **You don't:** use the in-app rules. They also cover what `/metrics` doesn't export —
  consumers, publish rate, non-DLQ queues.
- **Don't route the same condition through both**, or people get paged twice. When you
  move a condition to Alertmanager, disable its in-app rule or clear its channels (it
  then only shows in QueueLens's notifications).

Neither one hears about QueueLens itself being down: `QueueLensBrokerDown` needs a
working `/metrics`. Add `up{job="queuelens"} == 0` to your rules for that.

## Wiring

1. **Prometheus** —
   [`deploy/prometheus/prometheus.yml`](../deploy/prometheus/prometheus.yml) scrapes
   `/metrics` with Basic Auth (password from a file), loads `alerts.yml`, and sends what
   fires to `alertmanager:9093`.
2. **Alertmanager** —
   [`deploy/alertmanager/alertmanager.yml`](../deploy/alertmanager/alertmanager.yml):

   | Severity | Receivers |
   |---|---|
   | critical | `webhook` and `slack` |
   | warning, or none | `slack` |

   Grouped by `alertname` and `queue`, so each DLQ gets its own message, with fire
   *and* resolve notifications. Slack titles come from the rules' `summary` annotation
   and the text from `description`; the webhook gets Alertmanager's standard JSON
   (`version: "4"`), for a pager, an incident bot or your own service. Set
   `--web.external-url` so the Slack title links to your Alertmanager.

### Secrets

Neither URL is in the config: Alertmanager reads them from files (`api_url_file`,
`url_file`). A Slack webhook URL is a credential — anyone holding it can post to the channel —
so keep both files out of git and mount them from your secret store:

```yaml
# docker compose
alertmanager:
  image: prom/alertmanager:v0.34.1
  volumes:
    - ./deploy/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    - ./secrets/slack-webhook-url:/etc/alertmanager/slack-webhook-url:ro  # the URL on one line
    - ./secrets/webhook-url:/etc/alertmanager/webhook-url:ro
```

On Kubernetes, mount the two keys of a Secret there (`subPath`), or point the two paths at
wherever the Secret is mounted. Prometheus's scrape password works the same way
(`password_file`).

## Testing

```bash
python scripts/test_alerting.py      # stdlib only; needs Docker
```

The CI job `alerting` runs it. Three stages, with pinned images
(`prom/prometheus:v3.15.0`, `prom/alertmanager:v0.34.1`):

1. **Rules** — `promtool check config` on `prometheus.yml`, then `promtool test rules` on
   [`alerts.test.yml`](../deploy/prometheus/alerts.test.yml): each alert fires when it
   should, stays quiet when it shouldn't, and waits out its `for:`.
2. **Routing** — `amtool check-config`, then `amtool config routes test` for every rule:
   critical reaches `webhook,slack`, warning only `slack`.
3. **Delivery** — starts Alertmanager with the example config, writes a local sink's URLs
   into the two files, posts an alert per rule (two DLQs for one of them) to
   `/api/v2/alerts`, and checks the sink got one Slack message per DLQ and rule (title,
   text, colour), one webhook call for the critical alert only, and both resolved
   notifications.

One stage on its own:

```bash
docker run --rm -v "$PWD/deploy/prometheus:/rules:ro" -w /rules --entrypoint promtool \
  prom/prometheus:v3.15.0 test rules alerts.test.yml
docker run --rm -v "$PWD/deploy/alertmanager:/cfg:ro" --entrypoint amtool \
  prom/alertmanager:v0.34.1 config routes test --config.file=/cfg/alertmanager.yml severity=critical
```

## Tuning

The thresholds are starting points; change them in `alerts.yml`, then update
`alerts.test.yml` to match and rerun the tests.

- **Per-queue limits:** split a rule by label —
  `queuelens_dlq_messages{queue=~"payments.*"} > 50` next to
  `queuelens_dlq_messages{queue!~"payments.*"} > 1000`.
- **`for:`** trades speed for noise. `DLQGrowing` and `QueueLensActionFailures` have none:
  their 30m / 15m windows already smooth things out.
- **`delta()` and `increase()` extrapolate** to the window's edges, so the value reads a
  little above the raw count: with one sample a minute, 3 failures inside 15 minutes read
  3.2 and trip `> 3`. Leave that margin when you pick a threshold.
- **Routing:** change `severity` labels in `alerts.yml`, or the matchers in
  `alertmanager.yml`, and check with `amtool config routes test`.
- **Timing:** `group_wait` 30s, `group_interval` 5m and `repeat_interval` 4h suit chat;
  a pager often wants a shorter `repeat_interval` on the critical route.

The gauges are read from the broker at every scrape (one Management API call), so a
shorter scrape interval costs broker load; see [OPERATIONS.md](OPERATIONS.md#monitoring).
