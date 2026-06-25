# scryme demo — k3s deployment

Runs a read-only public demo of scryme as a standalone deployment, fronted by an in-cluster
Cloudflare Tunnel at **demo.scryme.app**.

## What's here

| File | Purpose |
| --- | --- |
| `namespace.yaml` | `scryme-demo` namespace |
| `secret.example.yaml` | template for the DB password + app DB URL (copy to `secret.yaml`) |
| `postgres.yaml` | PostgreSQL Deployment + PVC + Service |
| `backend.yaml` | scryme Deployment (GHCR image) + data PVC + Service + ConfigMap |
| `kustomization.yaml` | bundles namespace + postgres + backend |
| `cloudflared-ingress.example.yaml` | tunnel ingress rule snippet |

The backend runs with `SCRYME_READ_ONLY=true`, so uploads and admin mutations are disabled and a
demo banner is shown. An init container loads card data on first boot: it waits for Postgres,
applies migrations, ingests the Scryfall bulk file, and seeds a sample collection. Postgres and the
data volume are persistent, so restarts are fast (the 24h cache guard skips re-downloading).

## Prerequisites

- A GHCR image: published by the `publish-image` workflow on release/tag.
- An in-cluster `cloudflared` tunnel.
- A default StorageClass that supports `ReadWriteOnce` (k3s `local-path` works).

## Pulling the image from GHCR

The deployment references an `imagePullSecret` named `ghcr-pull`. You have two options:

- **Make the package public** (no secret needed): on the package page
  `github.com/users/untraceablez/packages/container/scryme` → **Package settings** → Danger Zone →
  *Change visibility* → Public. The `imagePullSecrets` entry is then harmlessly ignored.
- **Use a pull secret** (works while the package is private). Create a
  [classic PAT](https://github.com/settings/tokens) with **`read:packages`** scope, then:

    ```bash
    kubectl -n scryme-demo create secret docker-registry ghcr-pull \
      --docker-server=ghcr.io \
      --docker-username=untraceablez \
      --docker-password=<YOUR_PAT> \
      --docker-email=you@example.com
    ```

## Deploy

```bash
# 1. Create the namespace + app/DB secret (do NOT commit secret.yaml)
kubectl apply -f namespace.yaml
cp secret.example.yaml secret.yaml
$EDITOR secret.yaml            # set a real password in both fields
kubectl apply -f secret.yaml

# 1b. If the GHCR package is private, create the pull secret (see above)

# 2. Deploy the stack
kubectl apply -k .

# 3. Watch the data load (init container downloads ~550 MB on first boot)
kubectl -n scryme-demo logs -f deploy/scryme-demo -c ingest-and-seed
kubectl -n scryme-demo rollout status deploy/scryme-demo
```

## Expose via Cloudflare Tunnel

Add the rule from `cloudflared-ingress.example.yaml` to your tunnel config, then:

```bash
cloudflared tunnel route dns <your-tunnel> demo.scryme.app
```

The tunnel targets `http://scryme-demo.scryme-demo.svc.cluster.local:8000`; Cloudflare handles TLS.

## Pin a version (recommended)

`kustomization.yaml` defaults to the `latest` tag. To pin a release, change `newTag`:

```yaml
images:
  - name: ghcr.io/untraceablez/scryme
    newTag: v0.1.0
```

## Refresh the demo data

```bash
kubectl -n scryme-demo rollout restart deploy/scryme-demo   # reruns ingest (guarded) + seed
```
