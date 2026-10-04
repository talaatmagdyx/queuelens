# Kubernetes

The Helm chart in [`deploy/helm/queuelens`](../deploy/helm/queuelens) runs the
`ghcr.io/talaatmagdyx/queuelens` image (tag: the chart's `appVersion`) as one pod with:

- a **Deployment**: one replica with `Recreate` on SQLite, or several with rolling updates
  and a **PodDisruptionBudget** on PostgreSQL ([more than one replica](#more-than-one-replica));
- a **PersistentVolumeClaim** for `/app/data`, where the SQLite database lives;
- a **Secret** with the credentials, unless you bring your own (`existingSecret`);
- a **Service**, a **ServiceAccount** without an API token, and optionally an **Ingress**
  and a Prometheus Operator **ServiceMonitor**.

The pod runs as the image's `queuelens` user (uid/gid 999) with a read-only root
filesystem, no capabilities and no privilege escalation. Readiness is `GET /ready`,
liveness and startup are `GET /health` ([probes](OPERATIONS.md#health-probes)).

Every value is commented in [`values.yaml`](../deploy/helm/queuelens/values.yaml).

## Install

Every release publishes the chart to GHCR as an OCI artifact,
`oci://ghcr.io/talaatmagdyx/charts/queuelens`. Its versions are the chart's, not
QueueLens's: each chart version deploys the QueueLens release in its `appVersion`, and CI
publishes a chart only when that is the release tag. Pick one for `CHART_VERSION` below
(without `--version`, Helm takes the latest):

```bash
helm show chart oci://ghcr.io/talaatmagdyx/charts/queuelens   # the latest: version, appVersion
```

From a checkout, `deploy/helm/queuelens` works anywhere the `oci://` reference does.

GHCR creates a new package private, also when CI's push links it to the repository: it
inherits the repository's access, not its visibility. Until the owner makes the
`charts/queuelens` package public (package settings, as for the image), pulling it needs
`helm registry login ghcr.io` with a token that can read it.

QueueLens needs three credentials, and the chart has no defaults for them: an install
without them fails ([required values](#required-values)). Put them in a Secret, with the
broker user's password in `RABBITMQ_PASSWORD` (URL-encoded in the AMQP URL if it has
reserved characters):

```bash
kubectl create namespace queuelens
kubectl -n queuelens create secret generic queuelens-credentials \
  --from-literal=QUEUELENS_ADMIN_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=QUEUELENS_RABBITMQ_URL="amqp://queuelens:${RABBITMQ_PASSWORD}@rabbitmq.messaging:5672/" \
  --from-literal=QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD="${RABBITMQ_PASSWORD}" \
  --from-literal=QUEUELENS_SECRET_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"

helm install queuelens oci://ghcr.io/talaatmagdyx/charts/queuelens --version "$CHART_VERSION" \
  -n queuelens --set existingSecret=queuelens-credentials \
  --set env.QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://rabbitmq.messaging:15672 \
  --set env.QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens
```

`QUEUELENS_SECRET_KEY` is optional but recommended: it encrypts the delivery-channel and
environment credentials stored in the database. Back it up apart from the database.

Then open the console:

```bash
kubectl -n queuelens port-forward svc/queuelens 8000:8000   # http://127.0.0.1:8000/
kubectl -n queuelens get secret queuelens-credentials \
  -o jsonpath='{.data.QUEUELENS_ADMIN_PASSWORD}' | base64 -d   # sign in as admin
```

## Settings: `env` and the Secret

Every setting is a `QUEUELENS_*` environment variable ([CONFIGURATION.md](CONFIGURATION.md)).

- **Non-secret settings** go in `env`, a map of variable names to values: the Management
  API URL and username, the vhost, limits, `QUEUELENS_DATABASE_URL` without a password, ...
- **Secret settings** go in one Secret. Each of its keys becomes the env var of the same
  name, so name the keys after the variables: the three required ones, plus any of
  `QUEUELENS_SECRET_KEY`, `QUEUELENS_USERS_JSON`, `PGPASSWORD`, and
  `QUEUELENS_ENVIRONMENTS_JSON` when it carries broker credentials.
- `extraEnv` takes EnvVar objects, for a `valueFrom` that reads another Secret or
  ConfigMap.

The Secret is either one you manage, named by `existingSecret` (kubectl, sealed-secrets,
external-secrets, ...), or one the chart creates from `secretEnv`:

```yaml
# values file kept out of git: Helm stores these values in the release
secretEnv:
  QUEUELENS_ADMIN_PASSWORD: "..."
  QUEUELENS_RABBITMQ_URL: "amqp://USER:PASSWORD@rabbitmq.messaging:5672/"
  QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD: "..."
  QUEUELENS_SECRET_KEY: "..."
```

A change to the chart's Secret rolls the pod (a checksum annotation). A change to an
existing Secret does not: run `kubectl -n queuelens rollout restart deployment/queuelens`.

### Required values

`QUEUELENS_ADMIN_PASSWORD`, `QUEUELENS_RABBITMQ_URL` and
`QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD`. Without them the app would start on its built-in
`change-me` / `guest` defaults, so the chart refuses:

- with `secretEnv`, `helm install` fails:
  `secretEnv.QUEUELENS_ADMIN_PASSWORD is required, or set existingSecret to a Secret that has it`;
- with `existingSecret`, the container names those three keys explicitly, so a Secret
  without one stops the pod at `CreateContainerConfigError` (`couldn't find key ...`);
- one of them in `env` (plain text in the Deployment) is refused, and so is setting both
  `existingSecret` and `secretEnv`.

## Persistence

`/app/data` is a PersistentVolumeClaim (`persistence.size`, default `1Gi`;
`persistence.storageClass`, default the cluster's; `accessModes`, default `ReadWriteOnce`).
It holds the whole state on SQLite: audit history, settings, alert rules, invited users.

- `persistence.existingClaim` mounts a claim you created instead.
- `helm uninstall` deletes the chart's claim, and the audit history with it. To keep it,
  set `persistence.annotations: {helm.sh/resource-policy: keep}`.
- Backups: snapshot the volume, or see [OPERATIONS.md](OPERATIONS.md#backups--data).

## PostgreSQL

With PostgreSQL holding the data there is nothing to persist in the pod:

```yaml
existingSecret: queuelens-credentials   # with a PGPASSWORD key added
persistence:
  enabled: false                        # /app/data becomes an emptyDir
env:
  QUEUELENS_DATABASE_URL: postgresql+asyncpg://queuelens@postgres.databases:5432/queuelens
```

The URL has no password: asyncpg reads `PGPASSWORD` from the Secret. (A URL with the
password in it belongs in the Secret instead of `env`.) With PostgreSQL the chart can run
[more than one replica](#more-than-one-replica).

Moving an install that already has SQLite data: add `PGPASSWORD` to the Secret and restart
the pod, then copy inside it while nobody is working in the console (anything written
between the copy and the upgrade stays in SQLite), then upgrade:

```bash
kubectl -n queuelens exec deploy/queuelens -- python -m app.copy_db \
  sqlite+aiosqlite:///./data/queuelens.db postgresql+asyncpg://queuelens@postgres.databases:5432/queuelens
helm upgrade queuelens deploy/helm/queuelens -n queuelens --reuse-values \
  --set env.QUEUELENS_DATABASE_URL=postgresql+asyncpg://queuelens@postgres.databases:5432/queuelens
```

Keep persistence on, and the SQLite file with it, until you're satisfied. The copy itself
is described in [OPERATIONS.md](OPERATIONS.md#moving-to-postgresql).

## Ingress and TLS

QueueLens serves plain HTTP and authenticates with HTTP Basic, which sends the password
with every request: terminate TLS at the ingress (or in front of it).

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: queuelens.example.internal
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: queuelens-tls      # e.g. issued by cert-manager
      hosts:
        - queuelens.example.internal
```

Failed logins are rate-limited per client IP. Behind an ingress the client is the ingress
controller unless uvicorn trusts its `X-Forwarded-For`: set `env.FORWARDED_ALLOW_IPS` to
the controller's pod addresses (a comma-separated list or CIDRs).

## Prometheus: ServiceMonitor

`/metrics` sits behind the app's Basic Auth, so the ServiceMonitor needs an account.
Use a Viewer account (it reads metrics and can change nothing); invite it from the console
and replace its one-time password first, since until then it gets `403`:

```bash
kubectl -n queuelens create secret generic queuelens-metrics \
  --from-literal=username=metrics-viewer --from-literal=password="${VIEWER_PASSWORD}"
```

```yaml
serviceMonitor:
  enabled: true
  labels:
    release: kube-prometheus-stack   # whatever your Prometheus selects ServiceMonitors by
  basicAuth:
    secretName: queuelens-metrics    # keys: username, password (usernameKey / passwordKey)
```

The ServiceMonitor targets the QueueLens container's port, so it keeps working when the
Service fronts a [sidecar](#a-sidecar-in-front). It needs the `monitoring.coreos.com`
CRDs. The alert rules in [`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml)
are not part of the chart; load them like your other rules.

During a broker outage `/ready` fails and the pod leaves the Service's ready endpoints, so
the console is unreachable through the Service and the ingress until the broker is back
(pages that need the broker would answer 503 anyway). Prometheus still scrapes the running
pod and sees `queuelens_rabbitmq_ready` drop to 0. To keep the console reachable during
outages, set `readinessProbe.httpGet.path` to `/health`.

## A sidecar in front

`extraContainers`, `extraVolumes`, `extraVolumeMounts` and `extraEnv` let the pod run
another container, such as an authenticating proxy, and `service.targetPort` sends the
Service's traffic to it. Containers in a pod share its network, so the proxy reaches
QueueLens on `127.0.0.1:8000`:

```yaml
service:
  targetPort: proxy        # the sidecar's port name; anything but "http"
env:                       # QueueLens takes the user from the proxy (docs/SSO.md)
  QUEUELENS_AUTH_PROXY_HEADER: X-Forwarded-Email
  QUEUELENS_AUTH_PROXY_GROUPS_HEADER: X-Forwarded-Groups
  QUEUELENS_AUTH_PROXY_ROLES_JSON:
    platform-admins: Admin
    sre: Operator
extraContainers:
  - name: oauth2-proxy
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.13.0
    args:
      - --http-address=0.0.0.0:4180
      - --upstream=http://127.0.0.1:8000
      - --provider=oidc
      - --oidc-issuer-url=https://sso.example.internal/realms/ops
      - --email-domain=example.com
    env:
      - name: OAUTH2_PROXY_CLIENT_ID
        value: queuelens
      - name: OAUTH2_PROXY_CLIENT_SECRET
        valueFrom: {secretKeyRef: {name: queuelens-oauth2-proxy, key: client-secret}}
      - name: OAUTH2_PROXY_COOKIE_SECRET
        valueFrom: {secretKeyRef: {name: queuelens-oauth2-proxy, key: cookie-secret}}
    ports:
      - name: proxy
        containerPort: 4180
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities: {drop: [ALL]}
```

The probes and the ServiceMonitor still go to QueueLens's own port. The proxy connects
from loopback, which QueueLens trusts by default (`QUEUELENS_TRUSTED_PROXIES`), so the
signed-in user and their groups reach the audit log ([SSO.md](SSO.md)). The same
headers from any other address are ignored, and Basic Auth still works for scripts and
the admin. QueueLens's port stays reachable on the pod's IP from inside the cluster: add a
NetworkPolicy if only the proxy should reach it.

## More than one replica

On SQLite there is one replica: the database is a file on one pod's volume, and the chart
refuses `replicaCount` above 1 while `persistence.enabled` is on. The strategy is then
`Recreate`: an update stops the old pod before starting the new one, which lets the
`ReadWriteOnce` volume move. The cost is a few seconds without a console on every upgrade.

On PostgreSQL, set `replicaCount` and turn the volume off:

```yaml
replicaCount: 3
persistence:
  enabled: false
env:
  QUEUELENS_DATABASE_URL: postgresql+asyncpg://queuelens@postgres.databases:5432/queuelens
```

The replicas coordinate through PostgreSQL. Two of them never read or act on one queue at
once, the login limiter counts every replica's failures, and one replica (whichever holds
an advisory lock) evaluates the alert rules ([OPERATIONS.md](OPERATIONS.md#deployment-model--constraints-read-this-first)).
Updates roll one pod at a time (`maxUnavailable: 0`).

**Disruptions.** With more than one replica the chart adds a PodDisruptionBudget, so a
node drain evicts one pod at a time: `podDisruptionBudget.maxUnavailable` (default 1), or
`minAvailable`, a number or a percentage, which replaces it; `podDisruptionBudget.enabled:
false` leaves it out. It counts Ready pods, and `/ready` needs the broker: during a broker
outage no pod is Ready, and a drain waits for the broker to come back. If it must not, set
`readinessProbe.httpGet.path` to `/health` ([above](#prometheus-servicemonitor)). One
replica gets no budget: it could only block drains.

**Sticky sessions.** A browse snapshot lives on the replica that took it, so a client
should keep talking to the same one. The chart sets `sessionAffinity: ClientIP` on the
Service. An ingress controller routes to pods directly, though, so it needs its own; with
ingress-nginx:

```yaml
ingress:
  annotations:
    nginx.ingress.kubernetes.io/affinity: cookie
    nginx.ingress.kubernetes.io/session-cookie-name: queuelens
```

Without stickiness nothing breaks, but paging rescans the queue on whichever replica
answers, which costs broker reads (and, on a quorum queue, a delivery count per message).

**Metrics.** Every replica exports the same DLQ gauges and its own action counters. The
bundled rules take `max by (queue)` and `sum by (action)`, so the alerts come out once.

## Upgrade

```bash
helm upgrade queuelens oci://ghcr.io/talaatmagdyx/charts/queuelens --version "$CHART_VERSION" \
  -n queuelens -f my-values.yaml
```

- A new chart moves the image to its `appVersion`; pin `image.tag` to choose the version.
- QueueLens creates its tables, and adds columns from newer releases, when it starts:
  there is no migration step. Read the [CHANGELOG](../CHANGELOG.md) upgrade notes first.
- `helm rollback` rolls back the chart and the image, not the database: back the database
  up before an upgrade across app versions.
- `--reuse-values` keeps the values of the last release; without it, pass your values file
  again.

## Testing the chart

[`scripts/test_helm_chart.sh`](../scripts/test_helm_chart.sh) lints and renders the chart
with the value sets in [`ci/`](../deploy/helm/queuelens/ci), checks that bad value sets are
refused, and validates the manifests with kubeconform. `scripts/test_helm_chart.sh kind`
builds the image, installs it next to a throwaway RabbitMQ in a kind cluster of its own
(its own kubeconfig, deleted afterwards), and checks `/health`, `/ready`, `/api/me` and
`/metrics`, that data survives a pod restart and an upgrade, and that a Secret missing a
required key stops the pod. `scripts/test_helm_chart.sh kind-replicas` installs two
replicas next to a throwaway PostgreSQL, the password as `PGPASSWORD` in the Secret: both
start at once on the empty database, failed logins on one pod make the other answer `429`,
an alert rule created on one is listed by the other, exactly one of them holds the
alert-engine lock, and all of it holds after a `kubectl rollout restart`. Given several
phases, the script runs them in one cluster. CI runs all three.
