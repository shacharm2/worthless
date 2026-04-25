# worthless-c3ox — Redis AUTH + TLS + disable-dangerous-commands

Design for review (architect-reviewer pass complete — verdict: change it,
then ship). Revisions from architect feedback inline below; original
sections kept for traceability with `# ARCHITECT:` annotations on the
changes.

## Goal

Make the Redis hot-path metering deployable to production:
* operator-supplied AUTH so a sibling container on the backend network
  cannot trivially `SET worthless:spend:victim 0`
* TLS so the password isn't sent cleartext on the docker bridge
* dangerous commands (FLUSHALL/FLUSHDB/CONFIG/DEBUG) blocked

Earlier review (penetration-tester, brutus) marked the no-AUTH state as
"bounded by c3ox" — this is closing that bound.

## Scope

**In:**
1. ACL-based AUTH (Redis 7 ACL, not legacy `requirepass`-only).
2. Password loading via the same precedence cascade as the fernet key
   (env override → file at `WORTHLESS_REDIS_PASSWORD_PATH`).
3. TLS support via `rediss://` URL, with CA + optional client cert
   loaded from `/secrets/`-mounted files.
4. Compose rewire: redis service ships with an `aclfile` + a
   `tls-port`-only configuration when env-flagged on; remains
   no-AUTH/no-TLS in dev when the flags are unset.
5. Tests covering: AUTH-on with correct password, AUTH-on with wrong
   password, AUTH-off no-regression, TLS-on with valid CA, TLS-on
   with bad CA, dangerous-commands blocked at ACL level.

**Out (deferred):**
* CA / cert generation tooling (operator brings their own CA).
* mTLS (single-tenant compose; tls-auth-clients no for v1.1).
* Per-tenant ACL users (one `worthless` user only).
* Auto-rotation of the Redis password.
* `rename-command` blocking (deprecated; ACL allow-listing is the
  supported path per Redis docs).

## Architecture decision: AUTH = ACL, not requirepass

Per the research (`docs/planning/redis-auth-tls-reference.md`):
* `requirepass` is a compat shim that sets the password on user
  `default`. Works but unfriendly for least-privilege.
* ACL gives us per-command, per-key-pattern lockdown. For our 5-command
  surface (GET / SET / INCRBY / DEL / PING) this is the right hammer.
* `-@dangerous` does not cover FLUSHALL/FLUSHDB. Allow-list is the only
  way to actually block them.

The minimum ACL ruleset:

```
user default off
user worthless on >${PASSWORD} ~worthless:spend:* &* -@all +get +set +incrby +del +ping +auth +hello +client|setname
```

* `default off` — disables the unauthenticated user entirely.
* `~worthless:spend:*` — key pattern restriction (only our keyspace).
* `&*` — pubsub channel pattern (none used; explicit deny).
* `-@all` — drop everything by default.
* `+get +set +incrby +del +ping` — the 5 commands we use plus PING for
  the boot health check.
* `+auth +hello +client|setname` — needed for the connection handshake
  (per redis-py docs, RESP3 hello + client setname).

This locks down to the smallest viable surface. An attacker who steals
the password still cannot FLUSHALL or CONFIG SET.

## Configuration surface (mirrors fernet pattern)

| Env var | Purpose | Default |
|---|---|---|
| `WORTHLESS_REDIS_URL` | already exists; if set with `rediss://` scheme, TLS auto-on | unset |
| `WORTHLESS_REDIS_PASSWORD` | dev-only env override (visible in /proc) | unset |
| `WORTHLESS_REDIS_PASSWORD_PATH` | path to password file (production) | `/secrets/redis-password` |
| `WORTHLESS_REDIS_CA_PATH` | path to CA cert for TLS | `/secrets/redis-ca.pem` |
| `WORTHLESS_REDIS_CLIENT_CERT_PATH` | optional client cert for mTLS | unset |
| `WORTHLESS_REDIS_CLIENT_KEY_PATH` | optional client key for mTLS | unset |

Precedence: env var → file → unset. If `WORTHLESS_REDIS_URL=rediss://`
but no CA found, fail-startup with a clear error (don't silently fall
back to no-TLS — that would be a downgrade attack against operator
intent).

## ARCHITECT REVISIONS (resolves architect-reviewer notes)

1. **Adopt a `RedisAuth` value-object** — `RedisAuth(password: bytearray,
   ca_path: Path | None, client_cert: Path | None, client_key: Path | None)`.
   Pass as a single arg to `create_redis_client`. One place for `.zero()`.
   Replaces the four-fields-on-ProxySettings approach. Lives in
   `src/worthless/proxy/metering.py` (or a new `auth.py` if it grows).

2. **TDD ordering reversed.** ACL file + compose rewire FIRST (with a
   hardcoded password commit-and-revert), THEN password loading, THEN
   TLS. Discovers compose-side problems while they're cheap to fix.

3. **Drop the unencrypted-creds warning test** — scope creep. Document
   the recommendation in `docker-compose.env.example` instead.

4. **ACL live-state verification.** Sub-task 4 must include
   `ACL GETUSER worthless` parsing — ACL syntax errors are silent at
   the file level; only live verification catches them.

5. **Footgun #4 (hostname/SAN) addressed.** Compose service name is
   `redis`; cert SAN must include `DNS:redis` OR we default
   `ssl_check_hostname=False` for the compose-network case. Picking
   the latter for v1.1 — pragmatic for self-hosted, documented as a
   compose-network-only caveat.

6. **Single `finally` for zeroing.** Lifespan finally:
   ```python
   finally:
       _zero_buf(settings.fernet_key)
       if settings.redis_auth is not None:
           settings.redis_auth.zero()
   ```
   Sequential, each wrapped in its own try/except so one failure
   doesn't skip the other.

7. **SR-01 redis-py limitation documented.** `redis-py` 5.x copies
   the password into a `str` internally for `AUTH` commands. Once
   the client owns it we can't guarantee zeroing. Document in the
   `RedisAuth` docstring + add a security-doc note alongside the
   existing `api_key.decode()` SR-01 limitation note.

8. **ACL file mount: read-only + immutable.** Document "ACL is
   immutable in our deploy". `ACL SAVE` will fail; that's intentional.

9. **Authenticated healthcheck.** `redis-cli -a "$REDIS_PASSWORD"
   --user worthless PING` so a malformed ACL doesn't silently leave
   the default user enabled while healthcheck still says PONG.

10. **Compose templating via `command:`, not `entrypoint:`.** Don't
    replace the alpine entrypoint's user-switching. Use:
    `command: ["sh", "-c", "sed s|__PASSWORD__|$$REDIS_PASSWORD|g
    /etc/redis/users.acl.tmpl > /tmp/users.acl && exec redis-server
    --aclfile /tmp/users.acl ..."]`.

## Sub-tasks (TDD-ordered, REVISED)

Each sub-task: write failing test → minimal code → green → refactor.

ARCHITECT-REORDERED: 3a → 1 → 2 → 3b → 4.

### Sub-task 1: AUTH password loading

Files: `src/worthless/proxy/config.py`, `src/worthless/proxy/metering.py`,
`tests/test_proxy_keyring.py` (extend) or new `tests/test_redis_auth.py`.

Tests (red first):
* `test_redis_password_from_env` — `WORTHLESS_REDIS_PASSWORD=secret` →
  `ProxySettings.redis_password` is `bytearray(b"secret")`.
* `test_redis_password_from_file` — file at default path → loaded as
  bytearray.
* `test_redis_password_env_overrides_file` — both set → env wins (dev
  convenience).
* `test_redis_password_missing_when_url_demands_it` — `WORTHLESS_REDIS_URL`
  set with credentials, but neither env nor file present → startup
  raises with a message naming the env var.
* `test_redis_password_zeroed_on_lifespan_exit` — bytearray is
  zeroed in lifespan finally (style match with fernet).

Code: small `_read_redis_password()` in `config.py`, mirrors
`_read_fernet_key()` shape. `create_redis_client` plumbs the password
into `Redis.from_url(..., password=...)`.

### Sub-task 2: TLS with cert paths

Files: `src/worthless/proxy/config.py`, `src/worthless/proxy/metering.py`,
new tests.

Tests (red first):
* `test_rediss_url_loads_ca_path` — `WORTHLESS_REDIS_URL=rediss://...`
  + `WORTHLESS_REDIS_CA_PATH=/tmp/ca.pem` → kwargs to `Redis.from_url`
  include `ssl_ca_certs="/tmp/ca.pem"`, `ssl_cert_reqs="required"`.
* `test_rediss_url_missing_ca_fails_startup` — rediss:// + no CA path
  set + no file at default → ImportError-class with actionable message.
* `test_redis_url_no_tls_does_not_pass_ssl_kwargs` — plain `redis://`
  → no `ssl_*` kwargs (avoid breaking existing fakeredis tests).
* `test_create_redis_client_rejects_unencrypted_creds_warning` —
  `redis://:password@host/0` (cleartext over the wire) → log a
  warning suggesting `rediss://` for production. Don't reject
  outright; dev compose uses unencrypted loopback.

Code: `create_redis_client` splits its kwarg construction into a small
helper `_redis_kwargs(url, password, ca_path, ...)`. Test the helper
directly without spinning a client.

### Sub-task 3: ACL file + compose rewire

Files: `deploy/docker-compose.yml`, `deploy/redis-acl.conf` (new),
`deploy/docker-compose.env.example`, `tests/test_deploy_static.py`.

Tests (red first):
* `test_compose_redis_uses_aclfile_when_aclfile_present` — when
  `deploy/redis-acl.conf` exists, the compose `command:` for redis
  references it.
* `test_compose_acl_file_locks_default_user_off` — ACL file content
  contains `user default off`.
* `test_compose_acl_file_grants_worthless_minimum_commands_only` —
  contains the exact allow-list. No `+@all`, no `+@dangerous`, no
  `+flushall`, etc.
* `test_compose_redis_no_published_ports` — Redis service has no
  `ports:` block (already true; regression guard).

Code:
* New `deploy/redis-acl.conf` — the ACL ruleset above with a
  `${PASSWORD}` placeholder substituted at compose-up time.
* Compose redis service gains `--aclfile /etc/redis/users.acl` +
  `volumes: - ./redis-acl.conf:/etc/redis/users.acl:ro`.
* The proxy service's depends_on stays no-AUTH-friendly (compose
  default to no-AUTH; operator opts in via env flags).

### Sub-task 4: end-to-end integration (docker-gated)

Files: `tests/test_redis_metering_dynamic.py` (extend).

Tests:
* `test_real_redis_with_auth_round_trip` — spin redis:7-alpine with
  `requirepass` set, connect via `Redis.from_url("redis://:pw@host/0")`,
  INCRBY/GET round-trip works. Verifies redis-py's URL-parsing.
* `test_real_redis_wrong_password_raises` — wrong password → connection
  raises `redis.exceptions.AuthenticationError`. Document the exception
  class so we can pattern-match if needed later.

Skip the TLS variant in CI (cert generation is operator-side); cover
via static config tests only.

## Files touched

* `src/worthless/proxy/config.py` — new fields + loaders.
* `src/worthless/proxy/metering.py` — `create_redis_client` plumbs
  password + cert paths.
* `src/worthless/proxy/app.py` — lifespan wires + zeroes the password
  bytearray on shutdown.
* `deploy/docker-compose.yml` — Redis service AUTH+TLS opt-in via env.
* `deploy/redis-acl.conf` — new file, minimum ACL.
* `deploy/docker-compose.env.example` — document the new env vars.
* `tests/test_redis_auth.py` — new test file for AUTH+TLS unit tests.
* `tests/test_redis_metering_dynamic.py` — extend with one docker-gated
  AUTH round-trip test.
* `tests/test_deploy_static.py` — extend with ACL file static checks.

## Open questions for architect

1. **ACL file mounting.** The ACL file needs `${PASSWORD}` substituted
   at boot. Three options:
   (a) `entrypoint.sh`-style template render in the redis container —
       requires custom image or sidecar.
   (b) Operator pre-renders the file before `docker compose up`.
   (c) Use `requirepass` + `rename-command` (legacy path) instead.
   (a) is cleanest for "one-shot self-host" but adds an entrypoint script
   to maintain. (b) is honest but pushes the work onto operators. Lean
   toward (a) — what's your call?

2. **Backward compat.** Current branch's compose has redis on, no AUTH.
   Should the c3ox PR flip the default to AUTH-on (breaks existing
   deploys) or stay opt-in (operators must set
   `WORTHLESS_REDIS_AUTH=true`)? My instinct: stay opt-in for v1.1,
   document the upgrade path.

3. **Password rotation.** Out of scope for c3ox, but the design should
   not foreclose it. Re-reading the password file on SIGHUP would be
   a nice property — affordable, or YAGNI for v1.1?

4. **TLS without CA.** Dev convenience: `--tls-cert-file ... --tls-key-file ...`
   without a CA, client uses `ssl_cert_reqs="none"`. Is shipping that
   path (with a big WARNING log) better than forcing CA setup, or does
   it normalize an insecure config? My lean: don't ship it; force
   operators to bring a CA for TLS to be on at all.

5. **Worth pulling forward worthless-d1au (circuit breaker)?** With
   AUTH+TLS adding latency on every connection, a slow Redis becomes
   more likely. d1au isn't blocking c3ox technically but they touch
   the same code path. Combine into one PR or keep separate?

## Risks

* **ACL syntax errors** are silent — Redis logs them but the server
  still starts with the broken user. Tests must verify the live ACL
  via `ACL WHOAMI` + `ACL GETUSER worthless`, not just the file content.
* **`requirepass` + `aclfile` collision.** The research footgun #6 —
  setting both makes the `default` user ambiguous. Pick one path: ACL
  file only.
* **TLS hostname mismatch** with `ssl_check_hostname=True` (default in
  redis-py 5.x). Compose uses hostname `redis` — the cert CN/SAN must
  include that. Document or default `ssl_check_hostname=False` for
  loopback (security trade-off — argue both ways for the architect).

## Estimated effort

3-4 hours of focused TDD. Sub-task 1 is ~45 min, 2 is ~45 min, 3 is
~1 hour (ACL file + compose dance), 4 is ~30 min. Plus 30 min review
loop margin.
