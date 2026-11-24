# Deploy on Kubernetes

The official demo at **[demo.scryme.app](https://demo.scryme.app)** runs on k3s, fronted by an
in-cluster Cloudflare Tunnel. The manifests live in the repo under
[`deploy/k8s/demo/`](https://github.com/Leyline-Coding/scryme/tree/main/deploy/k8s/demo) and are
bundled with kustomize.

## What gets deployed

- **PostgreSQL** — a Deployment with a PVC.
- **scryme** — a Deployment that pulls the published image
  `ghcr.io/leyline-coding/scryme` and persists card data on a PVC.
- An **init container** that, on first boot, waits for Postgres, runs migrations, ingests the
  Scryfall bulk file, and seeds a sample collection (`alembic upgrade` → `ingest` → `seed-demo`).
  Persistent Postgres + the 24h cache guard make later restarts fast.
- The backend runs with `SCRYME_READ_ONLY=true`, so every write is refused, the AI features are
  off, and the demo banner is shown. The ConfigMap also sets `SCRYME_ALLOWED_HOSTS=demo.scryme.app`,
  `SCRYME_CLIENT_IP_HEADER=CF-Connecting-IP` (per-visitor rate limiting behind Cloudflare) and a
  5-second `SCRYME_STATEMENT_TIMEOUT_MS` — see [Configuration](configuration.md#security-and-network-variables).
- **NetworkPolicies** — default-deny for the namespace, then: cloudflared → app, app → Postgres,
  app → public HTTPS, and DNS.
- An **auto-deploy CronJob** that polls the registry and pins the Deployment to the image digest
  the `latest` tag currently resolves to.

!!! warning "Hosting your own copy? Change two things first"
    - `SCRYME_ALLOWED_HOSTS` in `backend.yaml` must be **your** hostname, or every request gets
      `400`.
    - `networkpolicy.yaml` assumes cloudflared runs in a namespace named `cloudflared`. If yours
      is elsewhere (or you use an ingress controller instead), edit the `scryme-demo-allow-cloudflared`
      rule, or nothing will be able to reach the app. If you expose the Service any other way,
      also remove `SCRYME_CLIENT_IP_HEADER`.

## Prerequisites

- A default StorageClass supporting `ReadWriteOnce` (k3s `local-path` works).
- The published image. Make the `ghcr.io/leyline-coding/scryme` package **public**, or create a pull
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

!!! note "Pods are locked down"
    Both pods run as non-root (uid 1000 for the app, 70 for Postgres) with a read-only root
    filesystem, all capabilities dropped, the default seccomp profile, and no Kubernetes API token;
    `/tmp` is an `emptyDir`. `fsGroup` lets the app write the `/data` PVC. The DB password is
    supplied as a discrete part (`SCRYME_DB_PASSWORD`), so it may contain any characters — see
    [Configuration](configuration.md).

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

`kustomization.yaml` starts from the `latest` tag, and the auto-deploy CronJob then keeps the
Deployment pinned to whatever digest `latest` points at. To stay on one release instead, remove
`autodeploy.yaml` from `kustomization.yaml` (or suspend the CronJob) and set `newTag`:

```yaml
images:
  - name: ghcr.io/leyline-coding/scryme
    newTag: v0.28.0
```

See the [deploy README](https://github.com/Leyline-Coding/scryme/blob/main/deploy/k8s/demo/README.md)
for troubleshooting (including resetting the database volume).
