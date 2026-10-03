#!/usr/bin/env bash
# Tests the Helm chart in deploy/helm/queuelens.
#
#   scripts/test_helm_chart.sh render   lint, render several value sets, refuse bad ones,
#                                       validate the manifests with kubeconform
#   scripts/test_helm_chart.sh kind     install into a throwaway kind cluster and check it
#   scripts/test_helm_chart.sh          both
#
# Needs helm and docker (kubeconform runs from its image unless it is on PATH); the kind
# test also needs kind, kubectl, curl and openssl. That test creates its own cluster
# (KIND_CLUSTER, default ql-helm) with a kubeconfig in a temp dir, so your kubeconfig and
# current context are never touched, and deletes the cluster when it exits. Every
# credential is generated at run time.
#
#   IMAGE=queuelens:helm-test   image built from this checkout and loaded into the cluster
#   SKIP_BUILD=1                load IMAGE as it is instead of building it
#   LOCAL_PORT=18765            local end of the port-forward
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
  OUT="$(mktemp -d)"
  trap 'rm -rf "$OUT"' EXIT
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

  say "rendered manifests"
  for name in defaults existing-secret postgres ingress-servicemonitor sidecar; do
    has "$name" '^  replicas: 1$'
    has "$name" 'type: Recreate'
    has "$name" 'readOnlyRootFilesystem: true'
    has "$name" 'runAsUser: 999'
    has "$name" 'path: /ready'
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
  ok "one replica, Recreate, non-root, read-only root, /ready probe; each case renders what it should"

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

  for version in $KUBE_VERSIONS; do
    say "kubeconform, Kubernetes $version"
    cat "$OUT"/*.yaml | validate -kubernetes-version "$version"
  done
  rm -rf "$OUT"
  trap - EXIT
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

forward() {  # (re)start the port-forward; a forward ends with the pod it reached
  if [ -n "${PF_PID:-}" ]; then
    kill "$PF_PID" 2>/dev/null || true
    wait "$PF_PID" 2>/dev/null || true
  fi
  kubectl -n "$NS" port-forward svc/queuelens "$PORT:8000" >"$WORK/port-forward.log" 2>&1 &
  PF_PID=$!
  for _ in $(seq 1 30); do
    curl -sf "$URL/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  cat "$WORK/port-forward.log"
  die "port-forward to svc/queuelens did not come up"
}

kind_cleanup() {
  local status=$?
  if [ -n "${PF_PID:-}" ]; then
    kill "$PF_PID" 2>/dev/null || true
    wait "$PF_PID" 2>/dev/null || true
  fi
  if [ "$status" -ne 0 ]; then
    say "diagnostics"
    kubectl get pods -A -o wide || true
    kubectl -n "$NS" describe pods || true
    kubectl -n "$NS" logs -l "$SELECTOR" --tail=100 || true
    kubectl -n "$NS" logs deploy/rabbitmq --tail=60 || true
    kubectl -n "$NS" logs deploy/rabbitmq --previous --tail=60 || true
  fi
  say "deleting kind cluster $CLUSTER"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" || true
  rm -rf "$WORK"
}

kind_test() {
  CLUSTER="${KIND_CLUSTER:-ql-helm}"
  PORT="${LOCAL_PORT:-18765}"
  URL="http://127.0.0.1:$PORT"
  local image="${IMAGE:-queuelens:helm-test}"
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    die "a kind cluster named $CLUSTER already exists; delete it or set KIND_CLUSTER"
  fi
  WORK="$(mktemp -d)"
  # every kind, kubectl and helm call below uses this file, never ~/.kube/config
  export KUBECONFIG="$WORK/kubeconfig"
  trap kind_cleanup EXIT

  say "kind cluster $CLUSTER"
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 120s
  kubectl create namespace "$NS"

  say "throwaway RabbitMQ"
  local rmq_pass admin_pass operator_pass amqp_url fernet_key
  rmq_pass="$(gen)"
  admin_pass="$(gen)"
  operator_pass="$(gen)"
  amqp_url="amqp://queuelens:${rmq_pass}@rabbitmq:5672/"
  fernet_key="$(openssl rand -base64 32 | tr '+/' '-_')"  # what QUEUELENS_SECRET_KEY takes
  kubectl -n "$NS" create secret generic rabbitmq \
    --from-literal=RABBITMQ_DEFAULT_USER=queuelens --from-literal="RABBITMQ_DEFAULT_PASS=$rmq_pass"
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
    say "docker build $image"
    docker build -t "$image" "$ROOT"
  fi
  kind load docker-image "$image" --name "$CLUSTER"
  kubectl -n "$NS" rollout status deploy/rabbitmq --timeout=300s

  say "helm install, credentials in an existing Secret"
  kubectl -n "$NS" create secret generic queuelens-credentials \
    --from-literal="QUEUELENS_ADMIN_PASSWORD=$admin_pass" \
    --from-literal="QUEUELENS_RABBITMQ_URL=$amqp_url" \
    --from-literal="QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=$rmq_pass" \
    --from-literal="QUEUELENS_SECRET_KEY=$fernet_key"
  local base=(
    --set "image.repository=${image%:*}" --set "image.tag=${image##*:}"
    --set env.QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://rabbitmq:15672
    --set env.QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens
  )
  helm install queuelens "$CHART" -n "$NS" --wait --timeout 5m "${base[@]}" \
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
  QUEUELENS_RABBITMQ_URL: "$amqp_url"
  QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD: "$rmq_pass"
  QUEUELENS_SECRET_KEY: "$fernet_key"
  QUEUELENS_USERS_JSON: '{"helm-operator": "$operator_pass"}'
EOF
  )
  helm upgrade queuelens "$CHART" -n "$NS" --wait --timeout 5m "${base[@]}" -f "$WORK/secret-values.yaml"
  forward
  expect "the alert rule survived the upgrade" '"name":"helm-persistence-check"' "${auth[@]}" "$URL/api/alerts"
  expect "an optional Secret key reaches the app" '"role":"Operator"' -u "helm-operator:$operator_pass" "$URL/api/me"
  expect "/ready answers after the upgrade" '"status":"ok"' "$URL/ready"

  say "an existing Secret without a required key"
  kubectl -n "$NS" create secret generic incomplete \
    --from-literal="QUEUELENS_RABBITMQ_URL=$amqp_url" \
    --from-literal="QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=$rmq_pass"
  helm install incomplete "$CHART" -n "$NS" "${base[@]}" \
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

case "${1:-all}" in
  render) render ;;
  kind) kind_test ;;
  all) render && kind_test ;;
  *) echo "usage: $0 [render|kind]" >&2; exit 2 ;;
esac
