# Deploy on Kubernetes

The official demo at **[demo.scryme.app](https://demo.scryme.app)** runs on k3s, fronted by an
in-cluster Cloudflare Tunnel. The manifests live in the repo under
[`deploy/k8s/demo/`](https://github.com/untraceablez/scryme/tree/main/deploy/k8s/demo) and are
bundled with kustomize.

## What gets deployed

- **PostgreSQL** — a Deployment with a PVC.
- **scryme** — a Deployment that pulls the published image
  `ghcr.io/untraceablez/scryme` and persists card data on a PVC.
- An **init container** that, on first boot, waits for Postgres, runs migrations, ingests the
  Scryfall bulk file, and seeds a sample collection (`alembic upgrade` → `ingest` → `seed-demo`).
  Persistent Postgres + the 24h cache guard make later restarts fast.
- The backend runs with `SCRYME_READ_ONLY=true`, so uploads and admin mutations are disabled and
  the demo banner is shown.

## Prerequisites

- A default StorageClass supporting `ReadWriteOnce` (k3s `local-path` works).
- The published image. Make the `ghcr.io/untraceablez/scryme` package **public**, or create a pull
  secret named `ghcr-pull` from a `read:packages` token:

    ```bash
    kubectl -n scryme-demo create secret docker-registry ghcr-pull \
      --docker-server=ghcr.io \
      --docker-username=<you> \
      --docker-password=<read:packages PAT>
    ```

## Deploy

```bash
kubectl apply -f deploy/k8s/demo/namespace.yaml

# One password, used by Postgres and by the app (which URL-encodes it).
cp deploy/k8s/demo/examples/secret.example.yaml deploy/k8s/demo/secret.yaml
$EDITOR deploy/k8s/demo/secret.yaml          # set POSTGRES_PASSWORD
kubectl apply -f deploy/k8s/demo/secret.yaml

kubectl apply -k deploy/k8s/demo             # use -k (kustomize), not -f
kubectl -n scryme-demo logs -f deploy/scryme-demo -c ingest-and-seed
```

!!! note "Pod runs as non-root"
    The Deployment sets `securityContext.fsGroup: 1000` so the non-root container can write the
    `/data` PVC. The DB password is supplied as a discrete part (`SCRYME_DB_PASSWORD`), so it may
    contain any characters — see [Configuration](configuration.md).

## Expose with Cloudflare Tunnel

Add an ingress rule pointing at the Service, then publish the DNS route:

```yaml
ingress:
  - hostname: demo.scryme.app
    service: http://scryme-demo.scryme-demo.svc.cluster.local:8000
  - service: http_status:404
```

```bash
cloudflared tunnel route dns <your-tunnel> demo.scryme.app
```

Cloudflare terminates TLS at the edge, so the tunnel targets the Service over plain HTTP.

## Pin a version

`kustomization.yaml` tracks the `latest` tag. To pin a release, set `newTag`:

```yaml
images:
  - name: ghcr.io/untraceablez/scryme
    newTag: v0.1.0
```

See the [deploy README](https://github.com/untraceablez/scryme/blob/main/deploy/k8s/demo/README.md)
for troubleshooting (including resetting the database volume).
