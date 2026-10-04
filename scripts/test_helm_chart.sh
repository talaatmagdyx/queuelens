#!/usr/bin/env bash
# Tests the Helm chart in deploy/helm/queuelens.
#
#   scripts/test_helm_chart.sh render          lint, render several value sets, refuse bad
#                                              ones, validate the manifests with kubeconform
#   scripts/test_helm_chart.sh kind            install into a throwaway kind cluster, one
#                                              replica on SQLite, and check it
#   scripts/test_helm_chart.sh kind-replicas   two replicas on a throwaway PostgreSQL there:
#                                              they share logins, rules and one alert leader
#   scripts/test_helm_chart.sh                 all three
#
# Phases run in the order given, and the kind ones share one cluster and image build.
# Needs helm and docker (kubeconform runs from its image unless it is on PATH); the kind
# phases also need kind, kubectl, curl and openssl. They create their own cluster
# (KIND_CLUSTER, default ql-helm) with a kubeconfig in a temp dir, so your kubeconfig and
# current context are never touched, and delete the cluster when the script exits. Every
# credential is generated at run time.
#
#   IMAGE=queuelens:helm-test   image built from this checkout and loaded into the cluster
#   SKIP_BUILD=1                load IMAGE as it is instead of building it
#   LOCAL_PORT=18765            local end of the port-forward (kind-replicas: and the next)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="$ROOT/deploy/helm/queuelens"
KUBECONFORM_IMAGE=ghcr.io/yannh/kubeconform:v0.8.0
# the chart's minimum (Chart.yaml kubeVersion) and a current release
KUBE_VERSIONS="1.25.0 1.34.0"
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

say() { printf '\n== %s\n' "$*"; }
ok() { printf 'ok   %s\n' "$*"; }
die() { printf 'FAIL %s\n' "$*" >&2; exit 1; }
gen() { openssl rand -hex 16; }

# ------------------------------------------------------------------ render

validate() {
  local args=(-strict -summary -schema-location default -schema-location "$CRD_SCHEMAS" "$@")
  if command -v kubeconform >/dev/null 2>&1; then
    kubeconform "${args[@]}"
  else
    docker run --rm -i "$KUBECONFORM_IMAGE" "${args[@]}"
  fi
}

render_case() {  # NAME HELM_ARGS...: lint and render one value set into $OUT/NAME.yaml
  local name=$1 log
  shift
  log="$(helm lint --strict "$CHART" "$@" 2>&1)" || die "helm lint ($name): $log"
  helm template queuelens "$CHART" "$@" >"$OUT/$name.yaml" || die "helm template ($name)"
  ok "lint + template: $name"
}

has() { grep -qE -- "$2" "$OUT/$1.yaml" || die "$1: no match for /$2/"; }
lacks() { ! grep -qE -- "$2" "$OUT/$1.yaml" || die "$1: unexpected match for /$2/"; }

refuses() {  # WHAT MESSAGE HELM_ARGS...: rendering must fail with MESSAGE
  local what=$1 message=$2 err
  shift 2
  if err="$(helm template queuelens "$CHART" "$@" 2>&1)"; then die "rendered $what"; fi
  [[ "$err" == *"$message"* ]] || die "$what: unexpected error: $err"
  ok "refuses $what"
}

render() {
  OUT="$(mktemp -d)"  # removed by cleanup
  say "helm lint + template"
  render_case defaults \
    --set-string "secretEnv.QUEUELENS_ADMIN_PASSWORD=$(gen)" \
    --set-string "secretEnv.QUEUELENS_RABBITMQ_URL=amqp://queuelens:$(gen)@rabbitmq:5672/" \
    --set-string "secretEnv.QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=$(gen)" \
    --set-string "secretEnv.QUEUELENS_SECRET_KEY=$(gen)"
  render_case existing-secret -f "$CHART/ci/existing-secret-values.yaml"
  render_case postgres -f "$CHART/ci/postgres-values.yaml"
  render_case ingress-servicemonitor -f "$CHART/ci/ingress-servicemonitor-values.yaml"
  render_case sidecar -f "$CHART/ci/sidecar-values.yaml"
  render_case replicas -f "$CHART/ci/replicas-values.yaml"
  render_case replicas-min-available -f "$CHART/ci/replicas-values.yaml" \
    --set-string podDisruptionBudget.minAvailable=50%
  render_case replicas-no-pdb -f "$CHART/ci/replicas-values.yaml" --set podDisruptionBudget.enabled=false

  say "rendered manifests"
  for name in defaults existing-secret postgres ingress-servicemonitor sidecar; do
    has "$name" '^  replicas: 1$'
    has "$name" 'type: Recreate'
    has "$name" 'readOnlyRootFilesystem: true'
    has "$name" 'runAsUser: 999'
    has "$name" 'path: /ready'
    lacks "$name" '^kind: PodDisruptionBudget$'
  done
  has defaults '^kind: Secret$'
  has defaults 'QUEUELENS_SECRET_KEY: '
  has defaults '^kind: PersistentVolumeClaim$'
  has defaults 'checksum/secret: '
  lacks defaults '^kind: (Ingress|ServiceMonitor)$'
  lacks existing-secret '^kind: (Secret|PersistentVolumeClaim)$'
  has existing-secret 'claimName: queuelens-data'
  has existing-secret 'name: queuelens-credentials'
  has existing-secret 'value: "1048576"'  # not 1.048576e+06
  has existing-secret 'value: "true"'
  has existing-secret 'value: "\{\\"staging\\":\{\\"vhosts\\":\[\\"/\\",\\"staging\\"\]\}\}"'
  lacks postgres '^kind: PersistentVolumeClaim$'
  has postgres 'value: "postgresql\+asyncpg://'
  has ingress-servicemonitor '^kind: Ingress$'
  has ingress-servicemonitor '^kind: ServiceMonitor$'
  has ingress-servicemonitor 'storageClassName: "standard"'
  has ingress-servicemonitor 'helm.sh/resource-policy: keep'
  has sidecar 'targetPort: proxy'
  has sidecar 'name: oauth2-proxy'
  has sidecar 'mountPath: /etc/ssl/internal'
  has sidecar 'name: QUEUELENS_USERS_JSON'
  lacks sidecar 'sessionAffinity'
  has replicas '^  replicas: 3$'
  has replicas 'type: RollingUpdate'
  has replicas 'maxUnavailable: 0'
  has replicas 'sessionAffinity: ClientIP'
  lacks replicas '^kind: PersistentVolumeClaim$'
  has replicas '^kind: PodDisruptionBudget$'
  has replicas '^  maxUnavailable: 1$'
  has replicas-min-available '^  minAvailable: 50%$'
  lacks replicas-min-available '^  maxUnavailable: '
  lacks replicas-no-pdb '^kind: PodDisruptionBudget$'
  ok "one replica, Recreate, non-root, read-only root, /ready probe, no PodDisruptionBudget; each case renders what it should"

  say "refused value sets"
  refuses "an install without credentials" "secretEnv.QUEUELENS_ADMIN_PASSWORD is required"
  refuses "a partial set of credentials" "secretEnv.QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD is required" \
    --set-string "secretEnv.QUEUELENS_ADMIN_PASSWORD=$(gen)" \
    --set-string "secretEnv.QUEUELENS_RABBITMQ_URL=amqp://queuelens:$(gen)@rabbitmq:5672/"
  refuses "existingSecret together with secretEnv" "not both" \
    --set existingSecret=queuelens-credentials --set-string "secretEnv.QUEUELENS_SECRET_KEY=$(gen)"
  refuses "a credential in plain env" "is a secret" \
    --set existingSecret=queuelens-credentials --set-string "env.QUEUELENS_ADMIN_PASSWORD=$(gen)"
  refuses "a ServiceMonitor without scrape credentials" "serviceMonitor.basicAuth.secretName is required" \
    --set existingSecret=queuelens-credentials --set serviceMonitor.enabled=true
  refuses "several replicas on one SQLite volume" "replicaCount above 1 needs PostgreSQL" \
    --set existingSecret=queuelens-credentials --set replicaCount=2

  for version in $KUBE_VERSIONS; do
    say "kubeconform, Kubernetes $version"
    cat "$OUT"/*.yaml | validate -kubernetes-version "$version"
  done
}

# ------------------------------------------------------------------ kind

NS=queuelens
SELECTOR=app.kubernetes.io/instance=queuelens,app.kubernetes.io/name=queuelens

expect() {  # WHAT NEEDLE CURL_ARGS...: the response must contain NEEDLE
  local what=$1 needle=$2 body
  shift 2
  body="$(curl -s --max-time 15 "$@" || true)"
  [[ "$body" == *"$needle"* ]] || die "$what; got: ${body:0:400}"
  ok "$what"
}

stop_forwards() {
  local pid
  for pid in ${PF_PIDS:-}; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  PF_PIDS=""
}

forward() {  # [TARGET=PORT]...: replace the port-forwards, by default svc/queuelens=$PORT;
             # a forward ends with the pod it reached
  stop_forwards
  [ $# -gt 0 ] || set -- "svc/queuelens=$PORT"
  local spec
  for spec in "$@"; do
    kubectl -n "$NS" port-forward "${spec%=*}" "${spec##*=}:8000" >>"$WORK/port-forward.log" 2>&1 &
    PF_PIDS="${PF_PIDS:-} $!"
  done
  for spec in "$@"; do
    for _ in $(seq 1 30); do
      curl -sf "http://127.0.0.1:${spec##*=}/health" >/dev/null 2>&1 && continue 2
      sleep 1
    done
    cat "$WORK/port-forward.log"
    die "port-forward to ${spec%=*} did not come up"
  done
}

kind_down() {  # STATUS: diagnostics if it is not 0, then delete the cluster
  stop_forwards
  if [ "$1" -ne 0 ]; then
    say "diagnostics"
    kubectl get pods -A -o wide || true
    kubectl -n "$NS" describe pods || true
    kubectl -n "$NS" logs -l app.kubernetes.io/name=queuelens --prefix --tail=100 || true
    kubectl -n "$NS" logs deploy/rabbitmq --tail=60 || true
    kubectl -n "$NS" logs deploy/rabbitmq --previous --tail=60 || true
    kubectl -n "$NS" logs deploy/postgres --tail=60 2>/dev/null || true
  fi
  say "deleting kind cluster $CLUSTER"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" || true
  rm -rf "$WORK"
}

kind_up() {  # once per run: the cluster, a throwaway RabbitMQ and the QueueLens image
  [ -z "${WORK:-}" ] || return 0
  CLUSTER="${KIND_CLUSTER:-ql-helm}"
  PORT="${LOCAL_PORT:-18765}"
  URL="http://127.0.0.1:$PORT"
  IMAGE="${IMAGE:-queuelens:helm-test}"
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    die "a kind cluster named $CLUSTER already exists; delete it or set KIND_CLUSTER"
  fi
  WORK="$(mktemp -d)"  # cleanup deletes the cluster once this is set
  # every kind, kubectl and helm call below uses this file, never ~/.kube/config
  export KUBECONFIG="$WORK/kubeconfig"

  say "kind cluster $CLUSTER"
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 120s
  kubectl create namespace "$NS"

  say "throwaway RabbitMQ"
  RMQ_PASS="$(gen)"
  AMQP_URL="amqp://queuelens:${RMQ_PASS}@rabbitmq:5672/"
  kubectl -n "$NS" create secret generic rabbitmq \
    --from-literal=RABBITMQ_DEFAULT_USER=queuelens --from-literal="RABBITMQ_DEFAULT_PASS=$RMQ_PASS"
  kubectl -n "$NS" apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rabbitmq
spec:
  replicas: 1
  selector:
    matchLabels: {app: rabbitmq}
  template:
    metadata:
      labels: {app: rabbitmq}
    spec:
      containers:
        - name: rabbitmq
          image: rabbitmq:3.13-management
          envFrom:
            - secretRef: {name: rabbitmq}
          ports:
            - {name: amqp, containerPort: 5672}
            - {name: management, containerPort: 15672}
          # a TCP check, not rabbitmq-diagnostics: the CLI runs as root, and when it beats
          # the broker to /var/lib/rabbitmq/.erlang.cookie it creates the cookie root-owned
          # and the broker exits on eacces (seen in CI as a CrashLoopBackOff)
          readinessProbe:
            tcpSocket: {port: amqp}
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: rabbitmq
spec:
  selector: {app: rabbitmq}
  ports:
    - {name: amqp, port: 5672}
    - {name: management, port: 15672}
EOF

  # the broker image pulls while QueueLens builds
  if [ -z "${SKIP_BUILD:-}" ]; then
    say "docker build $IMAGE"
    docker build -t "$IMAGE" "$ROOT"
  fi
  kind load docker-image "$IMAGE" --name "$CLUSTER"
  kubectl -n "$NS" rollout status deploy/rabbitmq --timeout=300s
  BASE=(
    --set "image.repository=${IMAGE%:*}" --set "image.tag=${IMAGE##*:}"
    --set env.QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://rabbitmq:15672
    --set env.QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens
  )
}

kind_test() {
  local admin_pass operator_pass fernet_key
  admin_pass="$(gen)"
  operator_pass="$(gen)"
  fernet_key="$(openssl rand -base64 32 | tr '+/' '-_')"  # what QUEUELENS_SECRET_KEY takes

  say "helm install, credentials in an existing Secret"
  kubectl -n "$NS" create secret generic queuelens-credentials \
    --from-literal="QUEUELENS_ADMIN_PASSWORD=$admin_pass" \
    --from-literal="QUEUELENS_RABBITMQ_URL=$AMQP_URL" \
    --from-literal="QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=$RMQ_PASS" \
    --from-literal="QUEUELENS_SECRET_KEY=$fernet_key"
  helm install queuelens "$CHART" -n "$NS" --wait --timeout 5m "${BASE[@]}" \
    --set existingSecret=queuelens-credentials
  local auth=(-u "admin:$admin_pass")
  forward
  expect "/health answers" '"status":"ok"' "$URL/health"
  expect "/ready answers: the broker is connected" '"status":"ok"' "$URL/ready"
  expect "/api/me refuses a request without credentials" 401 -o /dev/null -w '%{http_code}' "$URL/api/me"
  expect "/api/me signs the admin in" '"role":"Admin"' "${auth[@]}" "$URL/api/me"
  expect "/metrics reports the broker connected" 'queuelens_rabbitmq_ready 1.0' "${auth[@]}" "$URL/metrics"
  [ "$(kubectl -n "$NS" exec deploy/queuelens -c queuelens -- id -u)" = 999 ] || die "pod does not run as uid 999"
  ok "the app runs as uid 999"
  if kubectl -n "$NS" exec deploy/queuelens -c queuelens -- touch /app/probe 2>/dev/null; then
    die "the root filesystem is writable"
  fi
  ok "the root filesystem is read-only"
  kubectl -n "$NS" exec deploy/queuelens -c queuelens -- test -s /app/data/queuelens.db \
    || die "no SQLite database on the volume"
  ok "the SQLite database is on the volume"
  expect "an alert rule is created" '"name":"helm-persistence-check"' "${auth[@]}" \
    -H 'content-type: application/json' \
    -d '{"name":"helm-persistence-check","pattern":"*.dlq","threshold":5}' "$URL/api/alerts"

  say "pod restart"
  local old
  old="$(kubectl -n "$NS" get pod -l "$SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
  kubectl -n "$NS" delete pod "$old" --wait=true
  kubectl -n "$NS" wait --for=condition=Ready pod -l "$SELECTOR" --timeout=180s
  forward
  expect "the alert rule survived the restart" '"name":"helm-persistence-check"' "${auth[@]}" "$URL/api/alerts"

  say "helm upgrade to a chart-managed Secret"
  (
    umask 077
    cat >"$WORK/secret-values.yaml" <<EOF
secretEnv:
  QUEUELENS_ADMIN_PASSWORD: "$admin_pass"
  QUEUELENS_RABBITMQ_URL: "$AMQP_URL"
  QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD: "$RMQ_PASS"
  QUEUELENS_SECRET_KEY: "$fernet_key"
  QUEUELENS_USERS_JSON: '{"helm-operator": "$operator_pass"}'
EOF
  )
  helm upgrade queuelens "$CHART" -n "$NS" --wait --timeout 5m "${BASE[@]}" -f "$WORK/secret-values.yaml"
  forward
  expect "the alert rule survived the upgrade" '"name":"helm-persistence-check"' "${auth[@]}" "$URL/api/alerts"
  expect "an optional Secret key reaches the app" '"role":"Operator"' -u "helm-operator:$operator_pass" "$URL/api/me"
  expect "/ready answers after the upgrade" '"status":"ok"' "$URL/ready"

  say "an existing Secret without a required key"
  kubectl -n "$NS" create secret generic incomplete \
    --from-literal="QUEUELENS_RABBITMQ_URL=$AMQP_URL" \
    --from-literal="QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=$RMQ_PASS"
  helm install incomplete "$CHART" -n "$NS" "${BASE[@]}" \
    --set existingSecret=incomplete --set persistence.enabled=false >/dev/null
  local reason=""
  for _ in $(seq 1 60); do
    reason="$(kubectl -n "$NS" get pod -l app.kubernetes.io/instance=incomplete \
      -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)"
    [ "$reason" = CreateContainerConfigError ] && break
    sleep 2
  done
  [ "$reason" = CreateContainerConfigError ] || die "pod started without QUEUELENS_ADMIN_PASSWORD (state: ${reason:-none})"
  ok "the pod stops at CreateContainerConfigError instead of starting on default credentials"
  helm uninstall incomplete -n "$NS" >/dev/null

  say "kind test passed"
}

two_pods() {  # SELECTOR: set A and B to the names of its two pods
  set -- $(kubectl -n "$NS" get pod -l "$1" -o jsonpath='{.items[*].metadata.name}')
  [ $# = 2 ] || die "expected two pods, got: ${*:-none}"
  A=$1 B=$2
}

one_alert_leader() {  # SELECTOR: one of its pods, and nothing else, holds an advisory lock
  local held="" leader=""
  for _ in $(seq 1 30); do
    held="$(kubectl -n "$NS" exec deploy/postgres -- psql -U queuelens -tAc \
      "SELECT host(a.client_addr) FROM pg_locks l JOIN pg_stat_activity a USING (pid)
       WHERE l.locktype = 'advisory' AND l.granted" || true)"
    if [ -n "$held" ] && [[ "$held" != *$'\n'* ]]; then
      leader="$(kubectl -n "$NS" get pod -l "$1" \
        -o jsonpath="{.items[?(@.status.podIP==\"$held\")].metadata.name}")"
      [ -z "$leader" ] || { ok "one replica evaluates the alert rules: $leader holds the lock"; return 0; }
    fi
    sleep 2
  done
  die "expected one replica holding the alert-engine lock; advisory locks held from: ${held:-nowhere}"
}

kind_replicas_test() {
  local release=replicas deploy=replicas-queuelens pg_pass admin_pass got="" i code
  local selector="app.kubernetes.io/instance=$release,app.kubernetes.io/name=queuelens"
  local url_b="http://127.0.0.1:$((PORT + 1))"

  say "throwaway PostgreSQL"
  pg_pass="$(gen)"
  admin_pass="$(gen)"
  kubectl -n "$NS" create secret generic postgres \
    --from-literal=POSTGRES_USER=queuelens --from-literal="POSTGRES_PASSWORD=$pg_pass"
  kubectl -n "$NS" apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels: {app: postgres}
  template:
    metadata:
      labels: {app: postgres}
    spec:
      containers:
        - name: postgres
          image: postgres:17
          envFrom:
            - secretRef: {name: postgres}
          ports:
            - {name: postgres, containerPort: 5432}
          # over TCP: the server initdb runs first listens on the socket only
          readinessProbe:
            exec: {command: [pg_isready, -h, 127.0.0.1, -U, queuelens]}
            periodSeconds: 2
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector: {app: postgres}
  ports:
    - {name: postgres, port: 5432}
EOF
  kubectl -n "$NS" rollout status deploy/postgres --timeout=300s

  say "helm install, two replicas on PostgreSQL, the password as PGPASSWORD in the Secret"
  kubectl -n "$NS" create secret generic replicas-credentials \
    --from-literal="QUEUELENS_ADMIN_PASSWORD=$admin_pass" \
    --from-literal="QUEUELENS_RABBITMQ_URL=$AMQP_URL" \
    --from-literal="QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=$RMQ_PASS" \
    --from-literal="PGPASSWORD=$pg_pass"
  # both pods start at once, on an empty database
  helm install "$release" "$CHART" -n "$NS" --wait --timeout 5m "${BASE[@]}" \
    --set existingSecret=replicas-credentials --set replicaCount=2 --set persistence.enabled=false \
    --set-string env.QUEUELENS_DATABASE_URL=postgresql+asyncpg://queuelens@postgres:5432/queuelens
  two_pods "$selector"
  [ "$(kubectl -n "$NS" get pod -l "$selector" \
    -o jsonpath='{.items[*].status.containerStatuses[*].restartCount}')" = "0 0" ] \
    || die "a replica restarted: they could not start together on an empty database"
  ok "two replicas started together on an empty database, neither restarted"
  for _ in $(seq 1 30); do
    got="$(kubectl -n "$NS" get pdb "$deploy" -o jsonpath='{.status.currentHealthy}/{.status.disruptionsAllowed}' || true)"
    [ "$got" = 2/1 ] && break
    sleep 2
  done
  [ "$got" = 2/1 ] || die "PodDisruptionBudget: healthy/allowed disruptions is $got, not 2/1"
  ok "the PodDisruptionBudget covers both pods and lets one go at a time"

  say "the replicas share state: each pod port-forwarded on its own"
  local auth=(-u "admin:$admin_pass")
  forward "pod/$A=$PORT" "pod/$B=$((PORT + 1))"
  for i in $(seq 1 10); do
    code="$(curl -s --max-time 15 -o /dev/null -w '%{http_code}' -u "helm-intruder:$(gen)" "$URL/api/me" || true)"
    [ "$code" = 401 ] || die "failed login $i on $A answered $code, not 401"
  done
  ok "10 failed logins on $A"
  expect "the next one, on $B, answers 429: the login limiter is shared" 429 \
    -o /dev/null -w '%{http_code}' -u "helm-intruder:$(gen)" "$url_b/api/me"
  expect "an alert rule is created on $A" '"name":"helm-replicas-check"' "${auth[@]}" \
    -H 'content-type: application/json' \
    -d '{"name":"helm-replicas-check","pattern":"*.dlq","threshold":5}' "$URL/api/alerts"
  expect "$B lists it" '"name":"helm-replicas-check"' "${auth[@]}" "$url_b/api/alerts"
  one_alert_leader "$selector"

  say "rollout restart"
  local old_a=$A old_b=$B
  kubectl -n "$NS" rollout restart "deploy/$deploy"
  kubectl -n "$NS" rollout status "deploy/$deploy" --timeout=300s
  kubectl -n "$NS" wait --for=delete "pod/$old_a" "pod/$old_b" --timeout=120s
  kubectl -n "$NS" wait --for=condition=Ready pod -l "$selector" --timeout=180s
  two_pods "$selector"
  ok "both pods were replaced and are Ready"
  forward "pod/$A=$PORT" "pod/$B=$((PORT + 1))"
  expect "the alert rule survived the restart, on $A" '"name":"helm-replicas-check"' "${auth[@]}" "$URL/api/alerts"
  expect "and on $B" '"name":"helm-replicas-check"' "${auth[@]}" "$url_b/api/alerts"
  one_alert_leader "$selector"

  say "kind-replicas test passed"
}

cleanup() {
  local status=$?
  [ -z "${OUT:-}" ] || rm -rf "$OUT"
  [ -z "${WORK:-}" ] || kind_down "$status"
}

trap cleanup EXIT
[ $# -gt 0 ] || set -- render kind kind-replicas
for phase in "$@"; do
  case "$phase" in
    render | kind | kind-replicas) ;;
    *) echo "usage: $0 [render] [kind] [kind-replicas]" >&2; exit 2 ;;
  esac
done
for phase in "$@"; do
  case "$phase" in
    render) render ;;
    kind) kind_up; kind_test ;;
    kind-replicas) kind_up; kind_replicas_test ;;
  esac
done
