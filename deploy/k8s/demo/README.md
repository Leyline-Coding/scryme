# scryme demo — k3s deployment

Runs a read-only public demo of scryme as a standalone deployment, fronted by an in-cluster
Cloudflare Tunnel at **demo.scryme.app**.

## What's here

| File | Purpose |
| --- | --- |
| `namespace.yaml` | `scryme-demo` namespace |
| `postgres.yaml` | PostgreSQL Deployment + PVC + Service |
| `backend.yaml` | scryme Deployment (GHCR image) + data PVC + Service + ConfigMap |
| `autodeploy.yaml` | CronJob (+ ServiceAccount/Role) that pins the Deployment to the digest behind `:latest` |
| `networkpolicy.yaml` | default-deny NetworkPolicies + the few allowed paths |
| `kustomization.yaml` | bundles all of the above |
| `examples/secret.example.yaml` | template holding the single DB password (copy to `secret.yaml`) |
| `examples/cloudflared-ingress.example.yaml` | tunnel ingress rule snippet |

> Deploy with **`kubectl apply -k .`** (kustomize), not `apply -f .`. The `examples/` files are
> templates, not resources to apply directly. `-k` applies only the bundled resources.

The backend runs with `SCRYME_READ_ONLY=true`, so every write is refused, the AI features are off
and a demo banner is shown (never add `SCRYME_LLM_*` settings to the demo's ConfigMap). An init container loads card data on first boot: it waits for Postgres,
applies migrations, ingests the Scryfall bulk file, and seeds a sample collection. Postgres and the
data volume are persistent, so restarts are fast (the 24h cache guard skips re-downloading).

## Hardening, and what it assumes

- **Pods**: non-root (app uid 1000, Postgres uid 70 — the Alpine image's `postgres` user),
  read-only root filesystem with `emptyDir` for `/tmp` (and `/var/run/postgresql`), all
  capabilities dropped, `RuntimeDefault` seccomp, no service-account token, CPU and memory limits.
- **Network**: `networkpolicy.yaml` denies everything in the namespace and then allows
  cloudflared → app:8000, app → Postgres:5432, app → public internet on 443 (not private ranges),
  DNS, and the autodeploy job → 443/6443. **It assumes cloudflared runs in a namespace named
  `cloudflared`** and that cluster DNS is `k8s-app: kube-dns` in `kube-system`. Check with
  `kubectl get pods -A --show-labels | grep -i cloudflared` and edit the file if not — a wrong
  guess makes the demo unreachable. Anything else that connects to the app in-cluster (for example
  a Prometheus scraping `/metrics`) needs its own allow rule.
- **App settings** (ConfigMap in `backend.yaml`): `SCRYME_ALLOWED_HOSTS=demo.scryme.app` (requests
  for any other hostname get `400` — change it if you host under another name),
  `SCRYME_CLIENT_IP_HEADER=CF-Connecting-IP` (per-visitor rate limiting; only valid while
  cloudflared is the sole way in), `SCRYME_STATEMENT_TIMEOUT_MS=5000`.
- **Auto-deploy** pins the Deployment to an image digest; see the notes at the top of
  `autodeploy.yaml` for holding a version and for the effect of a manual `kubectl apply -k .`.

### Upgrading an existing demo to these manifests

1. Confirm the cloudflared namespace (above) and edit `networkpolicy.yaml` if needed.
2. Confirm the existing Postgres data is owned by uid 70 (it is, if it was created by the
   `postgres:16-alpine` image): `kubectl -n scryme-demo exec deploy/scryme-demo-postgres -- stat -c %u /var/lib/postgresql/data/pgdata`
3. `kubectl apply -k .` — both Deployments restart (Recreate), and within one poll interval the
   autodeploy job pins the image digest, restarting the app once more.
4. `kubectl -n scryme-demo delete configmap scryme-demo-autodeploy-state` (no longer used).

If the app pod does not become Ready, `kubectl -n scryme-demo describe pod -l app=scryme-demo` and
the container logs will say why. To take the network policies out of the picture while debugging:
`kubectl -n scryme-demo delete networkpolicy --all` (re-apply afterwards).

## Prerequisites

- A GHCR image: published by the `publish-image` workflow on release/tag.
- An in-cluster `cloudflared` tunnel.
- A default StorageClass that supports `ReadWriteOnce` (k3s `local-path` works).

## Pulling the image from GHCR

The deployment references an `imagePullSecret` named `ghcr-pull`. You have two options:

- **Make the package public** (no secret needed): on the package page
  `github.com/orgs/Leyline-Coding/packages/container/scryme` → **Package settings** → Danger Zone →
  *Change visibility* → Public. The `imagePullSecrets` entry is then harmlessly ignored.
- **Use a pull secret** (works while the package is private). Create a
  [classic PAT](https://github.com/settings/tokens) with **`read:packages`** scope, then:

    ```bash
    kubectl -n scryme-demo create secret docker-registry ghcr-pull \
      --docker-server=ghcr.io \
      --docker-username=<your-github-username> \
      --docker-password=<YOUR_PAT> \
      --docker-email=you@example.com
    ```

## Deploy

```bash
# 1. Create the namespace + DB secret (do NOT commit secret.yaml)
kubectl apply -f namespace.yaml
cp examples/secret.example.yaml secret.yaml
$EDITOR secret.yaml            # set a real POSTGRES_PASSWORD
kubectl apply -f secret.yaml

# 1b. If the GHCR package is private, create the pull secret (see above)

# 2. Deploy the stack
kubectl apply -k .

# 3. Watch the data load (init container downloads ~550 MB on first boot)
kubectl -n scryme-demo logs -f deploy/scryme-demo -c ingest-and-seed
kubectl -n scryme-demo rollout status deploy/scryme-demo
```

## Expose via Cloudflare Tunnel

Add the rule from `examples/cloudflared-ingress.example.yaml` to your tunnel config, then:

```bash
cloudflared tunnel route dns <your-tunnel> demo.scryme.app
```

The tunnel targets `http://scryme-demo.scryme-demo.svc.cluster.local:8000`; Cloudflare handles TLS.

## Pin a version (recommended)

`kustomization.yaml` starts from the `latest` tag and the autodeploy CronJob keeps the Deployment
pinned to the digest behind it. To stay on one release instead, drop `autodeploy.yaml` from
`kustomization.yaml` (or suspend the CronJob) and change `newTag`:

```yaml
images:
  - name: ghcr.io/leyline-coding/scryme
    newTag: v0.28.0
```

## Refresh the demo data

```bash
kubectl -n scryme-demo rollout restart deploy/scryme-demo   # reruns ingest (guarded) + seed
```

## Troubleshooting

**`password authentication failed for user "scryme"`** — PostgreSQL sets the password only when it
**first initializes** its data directory. If the `scryme-demo-postgres-data` PVC was created during
an earlier attempt with a different password, changing the secret afterward has no effect. Reset
the database volume (this deletes only demo data) and redeploy:

```bash
kubectl -n scryme-demo delete deploy scryme-demo scryme-demo-postgres
kubectl -n scryme-demo delete pvc scryme-demo-postgres-data scryme-demo-data
kubectl apply -f secret.yaml
kubectl apply -k .
```

