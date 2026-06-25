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

- A GHCR image: published by the `publish-image` workflow on release/tag. Make the
  `ghcr.io/untraceablez/scryme` package **public**, or add an `imagePullSecret` to the deployment.
- An in-cluster `cloudflared` tunnel.
- A default StorageClass that supports `ReadWriteOnce` (k3s `local-path` works).

## Deploy

```bash
# 1. Create the secret (do NOT commit it)
cp secret.example.yaml secret.yaml
$EDITOR secret.yaml            # set a real password in both fields
kubectl apply -f secret.yaml

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
