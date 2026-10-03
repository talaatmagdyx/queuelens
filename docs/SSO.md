# SSO behind an authenticating proxy

QueueLens doesn't speak OIDC or SAML itself. Put an authenticating proxy in front of it,
such as oauth2-proxy, Authelia behind Traefik, or an SSO ingress. The proxy signs people
in with your identity provider and passes the signed-in user's name in a request header.
The audit log then names the real person rather than a shared Basic Auth account, and
their role can come from their groups.

## How QueueLens decides who you are

1. **The identity header counts only from the proxy.** The TCP peer must be in
   `QUEUELENS_TRUSTED_PROXIES`. From any other address the header is ignored (and logged
   as a warning), and Basic Auth applies. The peer is checked before `X-Forwarded-For` is
   applied, so a forwarded address can't pass for the proxy.
2. **The role** is decided in this order:
   - **Local accounts first.** If the name matches a local account (an invited user, or
     one from `QUEUELENS_ADMIN_USERNAME` / `QUEUELENS_USERS_JSON`), that account's role
     applies, and a deactivated account is refused (`403`).
   - **Then groups.** Otherwise the highest role among the user's groups in
     `QUEUELENS_AUTH_PROXY_ROLES_JSON` applies.
   - **Then the default.** Otherwise `QUEUELENS_AUTH_PROXY_DEFAULT_ROLE` applies; empty
     means users in no mapped group are refused (`403`).
3. **Basic Auth keeps working.** The admin account stays a break-glass login, and scripts
   and Prometheus (`/metrics`) can reach QueueLens directly.

Local accounts and SSO names share one namespace. If your identity provider can issue the
name `admin`, either rename the local admin (`QUEUELENS_ADMIN_USERNAME`) or use the email
header.

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_AUTH_PROXY_HEADER` | *(empty: off)* | Header naming the user: `X-Forwarded-User`, `X-Forwarded-Email`, `X-Auth-Request-User`, `Remote-User`… |
| `QUEUELENS_AUTH_PROXY_GROUPS_HEADER` | *(empty)* | Header with comma-separated groups: `X-Forwarded-Groups`, `Remote-Groups`… |
| `QUEUELENS_AUTH_PROXY_ROLES_JSON` | `{}` | Group → role, e.g. `{"platform-admins": "Admin", "sre": "Operator"}` |
| `QUEUELENS_AUTH_PROXY_DEFAULT_ROLE` | `Viewer` | Role for users in no mapped group; empty refuses them |
| `QUEUELENS_TRUSTED_PROXIES` | `127.0.0.1,::1` | IPs or CIDRs of the proxies whose identity headers, `X-Forwarded-For` and `X-Forwarded-Proto` are believed. `*` and hostnames are refused |

`QUEUELENS_AUTH_ENABLED` must stay `true`: with auth off, everyone is the local Admin.

## What the proxy must do

- **Replace the identity and groups headers on every request**, never pass a client's
  copy through. oauth2-proxy strips client-supplied `X-Forwarded-*` user headers by
  default (`--skip-auth-strip-headers`). Traefik's `authResponseHeaders` and
  ingress-nginx's `auth-response-headers` replace them too.
- **Be the only way in.** The trust check already stops forged headers from other
  addresses; also make the proxy the only route to port 8000, through a sidecar, a
  private network or a NetworkPolicy.

**X-Forwarded-For:** QueueLens believes it only from `QUEUELENS_TRUSTED_PROXIES`, so the
audit log and the login limiter see the user's address rather than the proxy's. The image
runs uvicorn with `--no-proxy-headers` and QueueLens applies these headers itself. If you
run uvicorn yourself, pass `--no-proxy-headers` too. Otherwise uvicorn rewrites the
client address before QueueLens sees the proxy's, and identity headers from a loopback
proxy are ignored. That fails closed, but SSO won't work.

## oauth2-proxy as a sidecar (Kubernetes)

Run oauth2-proxy in the QueueLens pod with `--upstream=http://127.0.0.1:8000` and point
the Service at its port (4180), so nothing else reaches QueueLens. The Helm chart has
this as an example ([KUBERNETES.md](KUBERNETES.md#a-sidecar-in-front)). Loopback is
trusted by default:

```
QUEUELENS_AUTH_PROXY_HEADER=X-Forwarded-Email
QUEUELENS_AUTH_PROXY_GROUPS_HEADER=X-Forwarded-Groups
QUEUELENS_AUTH_PROXY_ROLES_JSON={"platform-admins": "Admin", "sre": "Operator"}
```

oauth2-proxy sends `X-Forwarded-User`, `-Email`, `-Groups` and `-Preferred-Username`
upstream (`--pass-user-headers`, on by default). Groups arrive when your provider puts
them in the token (`--oidc-groups-claim`).

## ingress-nginx with oauth2-proxy (external auth)

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/auth-url: "https://$host/oauth2/auth"
    nginx.ingress.kubernetes.io/auth-signin: "https://$host/oauth2/start?rd=$escaped_request_uri"
    nginx.ingress.kubernetes.io/auth-response-headers: "X-Auth-Request-Email, X-Auth-Request-Groups"
```

Run oauth2-proxy with `--set-xauthrequest`, then set
`QUEUELENS_AUTH_PROXY_HEADER=X-Auth-Request-Email`,
`QUEUELENS_AUTH_PROXY_GROUPS_HEADER=X-Auth-Request-Groups`, and
`QUEUELENS_TRUSTED_PROXIES` to the ingress controller pods' CIDR. Add a NetworkPolicy so
only the ingress controller reaches QueueLens.

## Authelia behind Traefik

```yaml
http:
  middlewares:
    authelia:
      forwardAuth:
        address: "http://authelia:9091/api/authz/forward-auth"
        authResponseHeaders: ["Remote-User", "Remote-Groups", "Remote-Email"]
```

Then set `QUEUELENS_AUTH_PROXY_HEADER=Remote-User`,
`QUEUELENS_AUTH_PROXY_GROUPS_HEADER=Remote-Groups`, and `QUEUELENS_TRUSTED_PROXIES` to
Traefik's address. In Docker Compose, give the network a fixed subnet and Traefik a fixed
`ipv4_address`, then trust that one address.

## Checking it

Open `https://<queuelens>/api/me` in a browser signed in through the proxy: it names you
and your role. A request to QueueLens directly with a forged
header gets `401`, and the QueueLens log shows
`ignored X-Forwarded-Email from <address>: not in QUEUELENS_TRUSTED_PROXIES`.
