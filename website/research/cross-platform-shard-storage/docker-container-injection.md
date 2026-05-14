# Docker Container Shard Injection: Cross-Platform Research

> How to inject Shard B (~50 bytes) into containers with REAL isolation from
> filesystem-based Shard A. No sudo beyond Docker. No user interaction after
> setup. Must work with `docker compose up` in under 5 minutes.

## 1. Docker Compose Secrets (non-Swarm)

### How it works

Compose v2 supports a `secrets:` top-level key without Swarm mode:

```yaml
secrets:
  shard_b:
    file: ./shard_b.bin      # host path

services:
  proxy:
    secrets:
      - shard_b              # mounted at /run/secrets/shard_b
```

### Reality check

**Non-Swarm Compose secrets are bind-mounts, not tmpfs.** The Docker daemon
bind-mounts the host file into the container at `/run/secrets/<name>`. This is
a cosmetic convenience, not a security boundary.

- The file exists on the host filesystem at `./shard_b.bin`.
- Inside the container, `/run/secrets/shard_b` is a bind-mount of that file.
- `docker inspect` reveals the bind-mount source path.
- If the host volume is compromised and the secrets file is on the same disk,
  both Shard A and Shard B are exposed.

### Isolation assessment

**Weak.** The secret is on the same host filesystem as volumes. The path
differs, but the trust domain is identical. An attacker with host filesystem
access gets both shards.

However: it IS a different path from volumes. A volume-only compromise (e.g.,
backup leak, snapshot exposure) would not include the secrets file unless it
was co-located in the volume directory. This provides **path isolation** but
not **medium isolation**.

### UX

Excellent. Users understand `file:` references. No daemon changes needed.
Works with `docker compose up` out of the box on Compose v2.3+.


## 2. Docker Swarm Secrets

### How it works

In Swarm mode, secrets are stored in the Raft log (encrypted at rest with
AES-256-CBC, or AES-256-GCM in newer versions) and distributed to nodes that
need them. Inside the container, they appear at `/run/secrets/<name>` on a
**real tmpfs mount**.

```bash
# Create secret
echo -n "base64-shard-b-bytes" | docker secret create shard_b -

# Reference in stack deploy
docker stack deploy -c docker-compose.yml worthless
```

### Reality check

- **Requires Swarm mode.** `docker swarm init` is needed, even on a single
  node. This is a real UX cost -- users must understand Swarm.
- The Raft log on the manager node contains the secret encrypted. On worker
  nodes, only containers assigned the secret receive it.
- **tmpfs mount is real.** The secret never hits the container's filesystem
  layer or any volume. A volume compromise does not expose it.
- `docker inspect` on the container shows the secret is assigned but does NOT
  reveal the value (unlike env vars).
- **Cannot use `docker compose up`** -- must use `docker stack deploy`.

### Isolation assessment

**Strong.** Real medium isolation: Shard A on a volume (ext4/overlay2), Shard
B on tmpfs backed by the Raft log. Different storage backends. A volume
snapshot, backup, or overlay2 exploit does not capture Shard B.

Threat: Swarm manager node disk compromise exposes the encrypted Raft log.
But the encryption key is separate from the volume encryption key, so this
requires a different attack than volume compromise.

### UX

Moderate friction. Requires `docker swarm init` and `docker stack deploy`
instead of `docker compose up`. Single-node Swarm works but adds conceptual
overhead. Not suitable for our "5 minutes with docker compose up" target.


## 3. Environment Variables

### How it works

```yaml
services:
  proxy:
    environment:
      - SHARD_B=base64encodedvalue
```

Or at runtime:
```bash
docker run -e SHARD_B=... worthless-proxy
```

### Reality check

**Environment variables are the worst option for secrets.**

- **`docker inspect` exposes them.** Anyone with access to the Docker API
  (socket or TCP) can read every env var on every container:
  ```bash
  docker inspect proxy --format '{{json .Config.Env}}'
  ```
- **`/proc/1/environ` inside the container** is readable by any process in
  the container (and by `docker exec`):
  ```bash
  docker exec proxy cat /proc/1/environ | tr '\0' '\n'
  ```
- **Inherited by child processes.** Any subprocess spawned by PID 1 inherits
  all environment variables. A dependency vulnerability that spawns a shell
  leaks the shard.
- **Logged everywhere.** Docker events, orchestrator logs, CI/CD pipelines,
  and crash dumps frequently capture environment variables.
- **No file permission control.** You cannot `chmod 400` an env var. Any user
  in the container reads it.

### Isolation assessment

**None.** Env vars provide zero isolation from anything. They are worse than
a file on a volume because they leak through more channels (inspect, proc,
child processes, logs). The secret is in the same trust domain as everything
else and is more broadly readable.

### When it is acceptable

Only when the orchestrator provides env-var injection with backend isolation
(see PaaS section below). The env var itself is a delivery mechanism; the
isolation comes from the platform's secret store, not from the env var.


## 4. Kubernetes Secrets

### How it works

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: shard-b
type: Opaque
data:
  shard_b.bin: <base64-encoded-value>
---
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: proxy
      volumeMounts:
        - name: shard-b-vol
          mountPath: /run/secrets
          readOnly: true
  volumes:
    - name: shard-b-vol
      secret:
        secretName: shard-b
```

### Reality check

**Kubernetes Secret volumes are tmpfs.** The kubelet mounts the secret data
into the pod as a tmpfs volume. The data never touches the node's disk (unless
the node is swapping, which is disabled by default on K8s nodes).

**etcd storage:**
- By default, Secrets are stored **base64-encoded but NOT encrypted** in etcd.
- With `EncryptionConfiguration`, Secrets can be encrypted at rest using
  AES-CBC, AES-GCM, or a KMS provider (AWS KMS, GCP KMS, Azure Key Vault,
  HashiCorp Vault).
- KMS-backed encryption means etcd contains only ciphertext; the decryption
  key is in the external KMS and never stored on the K8s node.

**Access control:**
- RBAC controls who can `kubectl get secret`. A well-configured cluster
  restricts this to operators, not workloads.
- Secrets are namespace-scoped. Cross-namespace access requires explicit RBAC
  grants.
- The kubelet only fetches secrets needed by pods scheduled on that node.

### Isolation assessment

**Strong (with KMS encryption).** This is the gold standard for container
secret injection:

| Property | PersistentVolume (Shard A) | Secret volume (Shard B) |
|---|---|---|
| Backing store | Cloud disk (EBS/PD/AzureDisk) | etcd + KMS |
| On-node storage | ext4/xfs on disk | tmpfs (RAM only) |
| Survives reboot | Yes | No (re-fetched from etcd) |
| Backup exposure | Yes (disk snapshots) | Only if etcd backup leaked AND KMS key compromised |
| Container escape | Disk accessible | tmpfs accessible (different mount) |

A PersistentVolume snapshot or disk image does not contain the secret. An etcd
backup without the KMS key does not contain the secret. These are genuinely
different trust domains.

**Without KMS encryption:** Secrets in etcd are base64-encoded plaintext. An
etcd backup or direct etcd access exposes them. Still better than co-locating
on a volume, but the isolation depends on etcd access control rather than
cryptographic separation.

### UX

Standard Kubernetes workflow. No additional tooling needed beyond `kubectl`.
For KMS encryption, requires one-time cluster configuration.


## 5. PaaS Platforms (Railway, Render, Fly.io)

### Railway

- Secrets are set via dashboard or CLI: `railway variables set SHARD_B=...`
- Injected as **environment variables** at container start.
- Stored in Railway's backend (encrypted at rest in their infrastructure).
- **Not visible in the container image or Dockerfile.**
- No `/proc` exposure beyond what env vars normally have.
- Railway does not support file-based secret injection; env vars only.

**Isolation:** The secret is stored in Railway's control plane, separate from
any volume. A volume compromise does not expose env vars. But inside the
container, the env var is readable via `/proc/1/environ` and `docker inspect`
equivalent (Railway's API). The isolation is at the **platform level**, not
the container level.

### Render

- Secrets set via dashboard or `render.yaml`:
  ```yaml
  services:
    - type: web
      envVars:
        - key: SHARD_B
          sync: false  # manual, not from repo
  ```
- Injected as environment variables.
- Render supports **secret files** (dashboard only): uploaded files mounted
  into the container at a specified path. These are bind-mounted from Render's
  secret store, not from user-visible volumes.
- Stored encrypted in Render's backend.

**Isolation:** Similar to Railway. Secret files on Render provide slightly
better isolation than env vars because they don't appear in `/proc/1/environ`,
but they are still bind-mounted from the host and accessible to `docker exec`.

### Fly.io

- Secrets set via CLI: `fly secrets set SHARD_B=...`
- Injected as environment variables into the Fly Machine.
- Stored encrypted in Fly's control plane.
- Fly also supports **mounted secrets** via `fly.toml`:
  ```toml
  [mounts]
    source = "shard_data"
    destination = "/data"
  ```
  But this is a persistent volume, not a secrets mechanism.
- For file-based secrets, Fly recommends writing the env var to a tmpfs at
  container startup via an entrypoint script.

**Isolation:** Platform-level isolation only. Inside the container, env vars
are fully exposed. The Fly control plane is a different trust domain from
volumes, which provides meaningful isolation at the infrastructure level.

### PaaS Summary

| Platform | Injection method | Container-level isolation | Platform-level isolation |
|---|---|---|---|
| Railway | Env var only | None | Yes |
| Render | Env var + secret files | Weak (bind-mount) | Yes |
| Fly.io | Env var only | None | Yes |

**Key insight:** PaaS platforms provide **infrastructure-level isolation**
(secret store != volume store) even though the container-level mechanism (env
vars) is weak. For Worthless, the relevant threat is whether a single
compromise vector exposes both shards. On PaaS, a volume compromise does NOT
expose env-var secrets, and vice versa. This is meaningful isolation despite
the weak container-level mechanism.


## 6. Sidecar Architecture in Docker

### Same container (current approach)

Both shards are accessible to the same process. Simpler, but a container
escape exposes both shards.

### Separate container with shared Unix Domain Socket (UDS)

```yaml
services:
  proxy:
    volumes:
      - uds:/tmp/worthless

  reconstruction:
    secrets:
      - shard_b
    volumes:
      - uds:/tmp/worthless

volumes:
  uds:
    driver: local
    driver_opts:
      type: tmpfs
      device: tmpfs
```

The reconstruction service:
1. Reads Shard B from `/run/secrets/shard_b`
2. Listens on `/tmp/worthless/reconstruct.sock`
3. Receives Shard A from the proxy over UDS
4. Reconstructs the key in-memory
5. Makes the upstream API call
6. Zeroes memory
7. Returns the response

### Trust domain implications

| Scenario | Same container | Sidecar via UDS |
|---|---|---|
| `docker exec proxy` | Both shards accessible | Only Shard A accessible |
| `docker exec reconstruction` | N/A | Only Shard B accessible |
| Volume compromise | Both shards if co-located | Only Shard A (Shard B in secret/tmpfs) |
| UDS sniffing | N/A | Attacker needs exec into either container + strace |
| Docker socket access | Everything exposed | Everything exposed |

**Key benefit:** The sidecar creates a process-level trust boundary. Even
within the Docker environment, an attacker who achieves code execution in the
proxy container cannot read Shard B without also compromising the
reconstruction container.

### UDS sharing in Compose

A named volume with tmpfs driver (shown above) is the standard way to share a
Unix socket between containers in Compose. The tmpfs backing means the socket
never hits disk. Both containers mount the same named volume.

**Caveats:**
- The UDS volume itself is a potential attack surface (if writable by the
  proxy container, the proxy could replace the socket with a malicious one).
- In production, the reconstruction container should create the socket and the
  proxy should only connect (not bind).
- File permissions on the socket can restrict access, but all containers
  sharing the volume run as the same UID by default.


## 7. Threat Model Per Mechanism

### Attacker with `docker exec` into proxy container

| Mechanism | Shard B readable? | How? |
|---|---|---|
| Volume file | Yes | `cat /path/to/shard_b` |
| Compose secret (non-Swarm) | Yes | `cat /run/secrets/shard_b` |
| Swarm secret | Yes | `cat /run/secrets/shard_b` |
| Env var | Yes | `cat /proc/1/environ` or `env` |
| K8s secret volume | Yes | `cat /run/secrets/shard_b` |
| Sidecar (B in separate container) | **No** | B is not in this container |

**`docker exec` is root-equivalent inside the container.** Every mechanism
that places Shard B inside the proxy container is defeated by `docker exec`.
The ONLY mechanism that survives is the sidecar architecture where Shard B
lives in a different container.

### Attacker with Docker socket access

| Mechanism | Shard B readable? | How? |
|---|---|---|
| Any container mechanism | Yes | `docker exec`, `docker cp`, inspect |
| Env var | Yes | `docker inspect` without exec |
| Swarm secret | Yes | Can create new service with same secret |
| K8s secret | N/A | Different API server |
| Sidecar | Yes | Exec into reconstruction container |

**Docker socket access is game over for all Docker-based isolation.** The
socket grants full control over all containers, volumes, and secrets. The only
defense is restricting socket access (not exposing it to containers, using
rootless Docker, or using a socket proxy).

### Attacker with volume snapshot only (backup leak, cloud snapshot)

| Mechanism | Shard B exposed? |
|---|---|
| Volume file (co-located) | **Yes** |
| Compose secret (non-Swarm, same disk) | **Likely yes** (depends on snapshot scope) |
| Swarm secret | **No** (tmpfs, not on disk) |
| Env var | **No** (not on disk, but may be in Docker state files) |
| K8s secret volume | **No** (tmpfs) |
| K8s secret in etcd backup | Only with KMS key |
| Sidecar with tmpfs secret | **No** |

This is the most realistic threat for Worthless's target users. Cloud disk
snapshots, volume backups, and container image layers are common leak vectors.
Mechanisms that keep Shard B off persistent disk (tmpfs-backed) survive this
threat.


## 8. Comparison Matrix

| Mechanism | Medium isolation | Survives exec | Survives volume leak | UX complexity | Compose-native |
|---|---|---|---|---|---|
| Volume file | None | No | No | Trivial | Yes |
| Compose secret (non-Swarm) | Path only | No | Maybe | Low | Yes |
| Env var | None | No | Yes* | Trivial | Yes |
| Swarm secret | tmpfs vs disk | No | Yes | Moderate | No (stack deploy) |
| K8s secret + KMS | tmpfs + KMS vs disk | No | Yes | Low (K8s native) | N/A |
| PaaS env var | Platform-level | No | Yes | Trivial | N/A |
| Sidecar + tmpfs secret | Process + tmpfs | **Proxy: Yes** | Yes | Moderate | Yes |

*Env vars may leak through Docker state files on disk, but not through volume
snapshots specifically.


## RECOMMENDATION

### Default docker-compose.yml: Sidecar + Compose Secrets + tmpfs

The recommended architecture for Worthless's default `docker-compose.yml`:

```yaml
version: "3.8"

secrets:
  shard_b:
    file: ${SHARD_B_PATH:-./secrets/shard_b.bin}

services:
  proxy:
    image: worthless/proxy:latest
    ports:
      - "${WORTHLESS_PORT:-8443}:8443"
    volumes:
      - shard_a:/data/shard_a:ro
      - uds:/run/worthless
    depends_on:
      - reconstruction
    # Proxy NEVER has access to shard_b

  reconstruction:
    image: worthless/reconstruction:latest
    secrets:
      - shard_b
    volumes:
      - uds:/run/worthless
    read_only: true
    tmpfs:
      - /tmp:size=1M
    security_opt:
      - no-new-privileges:true
    # Reconstruction NEVER has access to shard_a volume

volumes:
  shard_a:
    driver: local
  uds:
    driver: local
    driver_opts:
      type: tmpfs
      device: tmpfs
      o: size=1M,uid=1000,gid=1000
```

### Why this combination

1. **Shard A** lives on a named volume (`shard_a`), mounted only into the
   proxy container.

2. **Shard B** is delivered via Compose secrets, mounted only into the
   reconstruction container. While non-Swarm secrets are bind-mounts (not
   tmpfs), Shard B is on a different path from any volume and is only
   accessible inside the reconstruction container.

3. **Communication** between proxy and reconstruction happens over a Unix
   domain socket on a tmpfs-backed named volume. No network exposure.

4. **No container has both shards.** This is the critical property. Even
   `docker exec` into the proxy cannot read Shard B, because it physically
   is not mounted there.

5. **UX:** Works with plain `docker compose up`. No Swarm required. Users
   place `shard_b.bin` in a `secrets/` directory (created during enrollment).
   Total setup time: under 5 minutes.

### Defense in depth

- `read_only: true` on reconstruction prevents filesystem writes.
- `no-new-privileges` prevents privilege escalation.
- `tmpfs` for the UDS volume means the socket never hits disk.
- The reconstruction container has no exposed ports.

### What this does NOT defend against

- Docker socket access (game over for all Docker isolation).
- Host root access.
- Memory dumps of the reconstruction container (Shard B is in process memory
  during reconstruction -- mitigated by Rust + explicit zeroing).

### Platform-specific overrides

- **Kubernetes:** Replace Compose secrets with K8s Secrets (KMS-encrypted).
  Replace the sidecar volume with a native K8s Secret volume mount. The
  architecture is identical; only the secret delivery mechanism changes.
- **PaaS (Railway/Render/Fly):** Use platform env vars for Shard B injection.
  The platform's control plane provides infrastructure-level isolation. The
  sidecar architecture may not be available on all PaaS platforms; fall back
  to single-container with env var injection where sidecar is not supported.
- **Swarm:** Upgrade Compose secrets to Swarm secrets for real tmpfs backing.
  Same compose file works with `docker stack deploy` after `docker swarm init`.

### Enrollment flow sketch

```
worthless enroll
  -> generates shard_a, shard_b, commitment, nonce
  -> writes shard_a to ./data/shard_a/  (volume source)
  -> writes shard_b to ./secrets/shard_b.bin  (secret source)
  -> writes docker-compose.yml with above template
  -> user runs: docker compose up -d
```

The enrollment CLI creates the directory structure and compose file. The user
never manually handles shard bytes. The 5-minute target is achievable because
the only Docker-specific step is `docker compose up`.
