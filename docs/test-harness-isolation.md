# Test-harness Docker isolation — findings & plan

**Status:** marker + fail-closed guard IMPLEMENTED (`dev`, 2026-07-04); rootless daemon IMPLEMENTED
(`balance:X-br-3`, 2026-09-05), with 3 of 28 docker-marked tests still skipping on the
published-port defect that step did not close (`balance:N-459`) · **Written:** 2026-07-04 ·
**Owner:** josh

## TL;DR

The deploy-integration tests spin up real `docker` containers on whatever daemon the `docker` CLI
points at. On the homelab box that is the **production** daemon, so a local `./scripts/test.sh` run
floods it with ephemeral `shekel-test-*` containers — which spammed the `wud` update-notifier (now
mitigated) and pollutes `cadvisor`/`alloy` metrics. CI is unaffected (its own throwaway daemon).

**Fix:** run those tests against an **isolated rootless Docker daemon** selected via `DOCKER_HOST`,
and **gate them behind a `docker` pytest marker** that is excluded from routine local runs. No
production-facing test-code logic changes — the suite already drives the plain `docker` CLI, which
honours `DOCKER_HOST`.

---

## Implementation status (2026-07-04)

Items 1 and 2 shipped to `dev`; the churn problem is solved:

- **Marker + default-exclude (commit `56e48204`).** A `docker` pytest marker tags the eight
  daemon-touching classes across `tests/test_deploy/` (28 tests); `scripts/test.sh` defaults to
  `-m "not docker"`, so a routine local run spawns zero containers. CI still runs the full set: it
  called `pytest` directly then, and its shards pass `-m ""` to the wrapper since
  `bank_import:X-gy`. This alone removes the local churn this doc set out to fix.
- **Fail-closed guard (commit `5df43d4f`).** `tests/test_deploy/conftest.py` skips the docker-marked
  tests when `DOCKER_HOST` is the system socket, UNLESS the run is sanctioned: CI (GitHub sets
  `CI=true`), a non-default `DOCKER_HOST`, or `SHEKEL_ALLOW_HOST_DOCKER=1`. The CI exemption
  corrects the snippet under "Fail-closed guard" below, which as written would have skipped these
  tests in CI too and stripped them from the merge gate.

Deferred or corrected:

- **Rootless daemon (Item 4): DONE at `balance:X-br-3` (2026-09-05).** The "One-time host setup"
  block below has been rewritten against what this Arch host actually required; the previous version
  named a package command and a setup tool that do not work here. Measured effect: the 28
  docker-marked tests went from **28 skipped** on the system daemon to **25 passed, 3 skipped** on
  the rootless one. The 3 that still skip are the published-port collision recorded under "Rootless
  port allocation" below -- they skip rather than fail on it, so that defect was quietly thinning a
  green suite.
- **Leftover sweep (Item 3): dropped as a standalone.** The fixtures already use `--rm` plus a
  `finally: docker rm -f`; verified zero leftovers in practice, including after a hard mass-error
  run. Fold a sweep into the rootless work if it happens, where it targets the isolated daemon.
- **Inventory correction:** the "Background" claim of *two* container-spawning files was wrong; the
  corrected list is below.

---

## Background — what the harness is

- Test entrypoint: `scripts/test.sh` → starts a throwaway postgres container for the run on the
  rootless daemon, exports the test DSNs at its unix socket, runs `pytest` as a child and removes
  the container afterwards (`balance:X-br-4`; before it, the wrapper restarted a long-lived shared
  container on request instead). `pytest.ini` sets `addopts = ... --dist=loadgroup -n 12`, so
  **every** local run fans out across 12 pytest-xdist workers.
- Most tests are pure Python / static config assertions. **Eight classes across six files** touch
  the docker daemon (all gated by `_docker_available()`, all now `@pytest.mark.docker`). Of those:
  - **Three actually spawn containers.**
    `test_proxy_trust_and_headers.py::TestSharedNginxRuntimeHeaders` (network + stub + nginx; the
    main source of churn) and `::TestSharedVhostNginxParse` (`nginx -t`), plus
    `test_deploy_configs.py::TestDeployNginxConfigParses` (`nginx -t`). The original draft named
    `test_container_hardening.py` as the second spawner and missed `test_deploy_configs.py`; that
    was wrong.
  - **Five only run `docker compose config`,** which renders merged YAML and creates **no**
    container: `test_container_hardening.py::TestMergedComposeHardeningSurvivesOverride`,
    `test_deploy_configs.py::TestDeployComposeParses`,
    `test_internal_tls_and_access.py::TestMergedComposeCarriesTLS`,
    `test_image_supply_chain.py::TestComposeOverrideRequiresDigest`, and
    `test_docker_secrets_and_env_hygiene.py::TestProdComposeMergedConfig`. They touch the daemon but
    do not pollute `cadvisor`/`alloy`; they carry the marker for a clean "no daemon interaction by
    default" local run.

## The problem (symptoms observed 2026-07-04)

- Hundreds of `shekel-test-nginx-c33-<pid>` / `shekel-test-stub-c33-<pid>` create/die events on the
  production daemon (exit 137 = teardown SIGKILL).
- `wud` logged continuous `Unable to parse Docker event` (NDJSON bursts) and reacted to every event;
  its store went stale/unreliable.
- The churn also lands in `cadvisor` / `alloy` per-container metrics.

This is **not a test bug** — it is a "dev machine == production docker host" problem. The tests are
correct; they just have nowhere isolated to run locally.

## Root-cause mechanism (with citations)

| What | Where |
|---|---|
| Containers driven via the **`docker` CLI** (`subprocess.run(["docker", *args])`) | `test_proxy_trust_and_headers.py:849` (`_docker()` helper) |
| PID-namespaced names → 12 workers × their own stack | `test_proxy_trust_and_headers.py:835` (`suffix = f"c33-{os.getpid()}"`) |
| Creates network + stub + nginx, `class`-scoped fixture | `test_proxy_trust_and_headers.py:820` (`running_stack`) |
| Images pulled onto the daemon | `NGINX_TEST_IMAGE` = the compose's bundled nginx tag, read by `tests/test_deploy/_compose.py`; stub `python:3.14-alpine` (:891) |
| Publishes port, curls host loopback | `-p 0:443` (:909) → `docker port` (:925) → `curl https://localhost:<port>` (:947, :980) |
| Teardown = `docker rm -f` (→ exit 137) | `test_proxy_trust_and_headers.py:966-968` (`finally`) |
| Skips when docker absent (`docker info`) | `_docker_available()` :87 (and `test_container_hardening.py:56`) |

**Two facts drive the whole solution:**

1. Everything goes through the `docker` CLI → the suite **honours `DOCKER_HOST`**. Redirect it and
   every test container/network moves, with zero test-code changes.
2. The tests curl **`https://localhost:<mapped_port>/`** — they depend on the published port being
   reachable on the *pytest process's* loopback.

## Why CI is already fine

`.github/workflows/ci.yml` runs its suite shards on `ubuntu-26.04` (through `scripts/test.sh` since
`bank_import:X-gy`, directly before) on an
**ephemeral runner with its own throwaway docker daemon**. Containers there are created and
discarded on infrastructure that no homelab observability watches. So the collision is
**local-only**.

---

## Recommendation

### Primary: a dedicated **rootless** Docker daemon, selected via `DOCKER_HOST`

Rootless — **not** docker-in-docker — because of driving-fact #2 (`curl localhost`):

- **Rootless** publishes ports into *your user's* network namespace (RootlessKit), so
  `curl localhost:<port>` from pytest keeps working unchanged. ✅
- **dind** publishes into the privileged dind container's namespace — the host's `localhost:<port>`
  wouldn't reach it without extra plumbing, and it needs `--privileged` (against the whole
  `/opt/docker` hardening posture). ❌

Rootless gives a **completely separate daemon + socket + container/network namespace** that the
production `wud`/`cadvisor`/`alloy` (which watch the *system* socket) never see — true isolation,
mirroring the CI model. The C-33 tests only use `network create`, `run`, `port`, `exec`, `logs`,
`rm`, bind-mounts of repo-owned paths, and a random high host port — all fully supported rootless.

**One-time host setup (run as `josh` yourself, via `!`).** Rewritten 2026-09-05 from what this host
actually needed; three lines of the previous version were wrong.

```bash
yay -S docker-rootless-extras             # AUR on this host, NOT pacman -- it is not in extra
systemctl --user start docker.service     # the package ships the user unit; see the note below
loginctl enable-linger josh               # daemon survives logout -- otherwise it dies at logout
# resulting socket: unix:///run/user/1000/docker.sock
docker -H unix:///run/user/1000/docker.sock info | grep -iE 'rootless|Server Version'   # verify
```

Three corrections the old block got wrong, all confirmed on 2026-09-05:

- **`docker-rootless-extras` is an AUR package here**, so `pacman -S` fails. `pacman -Qi` reports
  `Installed From: None` and `Packager: Unknown Packager`, i.e. locally built.
- **It does not ship `dockerd-rootless-setuptool.sh`.** The package contains exactly four files:
  `/usr/bin/dockerd-rootless.sh`, a sysctl drop-in, and the `docker.service` / `docker.socket` user
  units. Start the *service*; do not enable `docker.socket` alongside it, because both bind
  `$XDG_RUNTIME_DIR/docker.sock` and would collide.
- **`slirp4netns` is an optional dependency and is not installed**, so rootlesskit falls back to the
  `gvisor-tap-vsock` driver, which announces itself as experimental.
  **Installing it is not required and would not change how the harness behaves**, because the
  per-run containers run `--network=none` (see below) and so use no network driver at all. It sits
  only in the path of `docker pull`, measured at 5 s for `postgres:18-alpine` on this host. Recorded
  because the daemon logs an experimental-driver warning at every start, which otherwise reads as a
  problem waiting to be fixed.

The rest of the prerequisites were already satisfied here and needed no action: `/etc/subuid` and
`/etc/subgid` map `josh:100000:65536`, `kernel.unprivileged_userns_clone` is 1, and cgroup v2
delegation gives the user slice `cpu memory pids`.

### Rootless port allocation: why the harness publishes no ports

**Do not publish a container port on this rootless daemon and expect it to work.** `dockerd`'s
`portallocator` picks a free ephemeral port *inside the container's network namespace*, and
RootlessKit's `builtin` port driver then binds that same number *on the host*, where an entirely
different set of sockets lives. `net.ipv4.ip_local_port_range` is `32768-60999` -- byte-identical to
the band docker publishes into -- and this host routinely holds thousands of sockets in it, so the
two allocators disagree constantly.

Measured 2026-09-05:

| arm | result |
|---|---|
| 12 containers, docker-assigned ports, removed between each | **5/12 failed** |
| 12 containers, docker-assigned ports, none removed (no port ever recycled) | **3/12 failed** |
| 12 containers, host ports chosen by the caller outside the ephemeral band | **0/12 failed** |
| 8 containers, docker-assigned ports, on the ROOT daemon | **0/8 failed** |

The second row is the one that makes this a diagnosis rather than a guess: with nothing recycled
there is no release race left to blame. The fourth is why nobody hit it before the harness moved off
the root daemon.

`scripts/test.sh` therefore gives the per-run cluster **no port at all** -- `--network=none`,
`listen_addresses=''`, and a unix socket in a per-run directory. The socket is mounted at `/sockets`
rather than the default `/var/run/postgresql`, because the postgres entrypoint chowns its default
socket directory to a user that rootless maps into the subuid range and sets the sticky bit, after
which the invoking user cannot unlink the socket: five cleanup attempts of five failed against that
path and five of five succeeded against `/sockets`.

One place still publishes a port: `scripts/build_test_db_image.py`'s bake container, which runs
`initdb`. The entrypoint's own post-`initdb` `psql` hardcodes `/var/run/postgresql` and `PGHOST`
does not override it (measured: exit 2 either way), so that container cannot be moved to a socket
without mounting over the directory whose cleanup fails. It runs once per image build rather than
once per run.

**Wire the suite to it**: IMPLEMENTED in `scripts/test.sh` at `balance:X-br-3`, inside the
wrapper -- unconditionally since `balance:X-br-4`, which deleted the opt-in `TEST_DB_PER_RUN` flag
along with the shared-container path it chose between. When `DOCKER_HOST` is unset and a rootless
socket exists it is selected automatically; then the daemon is asked what it IS and a non-rootless
one is refused. Since step X-cx's K1a-3 (2026-10-20) that rule lives in `scripts/private_cluster.py`
(`daemon()`), the act that starts a cluster, so the release rehearsal kit's bench inherits it too:
the wrapper asks `private_cluster.py daemon` before it prepares the image and exports the endpoint
it names, and the start asks again before it makes anything. The selection happens only when
`DOCKER_HOST` is unset AND that socket actually exists; nothing invents an endpoint, and an absent
rootless daemon is refused rather than fallen back from.

Two properties worth keeping if this is ever rewritten. It reads `docker info` rather than
pattern-matching the socket path, because "rootless" is a property of a daemon and a path is only a
guess about one -- a `tcp://` endpoint aimed at the `socket-proxy` container would read as isolated
while being the production daemon at another address. And it is fail-closed: an absent rootless
daemon stops the run with instructions rather than falling back to the daemon the whole exercise
exists to get off. `SHEKEL_ALLOW_HOST_DOCKER=1` remains the one spelling for accepting the churn
deliberately, shared with `tests/test_deploy/conftest.py`.

**The POLICY, however, is not yet single-homed, and an earlier draft of this section wrongly said it
was.** Two producers decide "is this daemon isolated" by contradictory methods that agree today:
`scripts/private_cluster.py` asks the daemon (`docker info` -> `rootless`), while
`tests/test_deploy/conftest.py` classifies by a path allowlist (`_SYSTEM_DOCKER_ENDPOINTS`) -- the
exact heuristic the wrapper's own comment argues has a hole, since a `tcp://` endpoint aimed at the
`socket-proxy` container reads as isolated while being the production daemon at another address.
That is CLAUDE.md rule 14's shape and it wants one of the two deleted. Exporting `DOCKER_HOST` lets
those 28 tests run, but only for a caller who also passes `PYTEST_MARKER_EXPR=docker`: this wrapper
defaults to `-m "not docker"`, which deselects them before the conftest is ever consulted.

CI is unaffected: its runners carry no rootless socket, so the wrapper leaves `DOCKER_HOST` unset
and uses the runner's own throwaway daemon, which `CI=true` sanctions.

### Complementary hardening (do regardless of rootless)

**1. Gate the container-spawning tests behind a `docker` marker + default-exclude locally.** This is
the single biggest churn reduction — routine local TDD then spins up **zero** containers.

- Register the marker in `pytest.ini` (extend the existing `markers =` block, :47):

  ```ini
  markers =
      docker: test stands up real containers via the docker CLI; needs an
              isolated daemon (see docs/test-harness-isolation.md)
  ```

- Apply it to the spawning tests (module-level in `test_proxy_trust_and_headers.py`, or class-level
  on the `running_stack` class; and the docker-gated cases in `test_container_hardening.py`):

  ```python
  pytestmark = pytest.mark.docker
  ```

- Default-exclude in **`scripts/test.sh`** (NOT `pytest.ini addopts`, so CI -- which then called
  pytest directly, and since `bank_import:X-gy` passes `-m ""` to the wrapper -- still runs the full
  set):

  ```bash
  # Local default: skip container-spawning tests unless explicitly requested.
  PYTEST_MARKER_EXPR="${PYTEST_MARKER_EXPR:-not docker}"
  # ... pytest -m "$PYTEST_MARKER_EXPR" "$@"
  # Opt in with:  PYTEST_MARKER_EXPR=docker ./scripts/test.sh   (runs against rootless)
  ```

**2. Fail-closed guard (belt-and-suspenders).** A `conftest.py` hook so a bare `pytest` can never
touch the production daemon even if someone bypasses the wrapper: skip `docker`-marked tests unless
`DOCKER_HOST` is set to a non-default endpoint (or an explicit `SHEKEL_ALLOW_HOST_DOCKER=1` override
is present).

```python
# IMPLEMENTED in tests/test_deploy/conftest.py (commit 5df43d4f).  The
# condition MUST exempt CI, or the guard skips these tests on the merge
# gate too (GitHub sets CI=true; its daemon is a throwaway no observer watches):
import os
def pytest_collection_modifyitems(items):
    dh = os.environ.get("DOCKER_HOST", "")
    system_sock = dh in ("", "unix:///var/run/docker.sock", "unix:///run/docker.sock")
    sanctioned = os.environ.get("CI") or os.environ.get("SHEKEL_ALLOW_HOST_DOCKER") == "1"
    if system_sock and not sanctioned:
        skip = pytest.mark.skip(reason="docker tests need an isolated DOCKER_HOST; "
                                       "see docs/test-harness-isolation.md")
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(skip)
```

**3. Leftover sweep.** Crashed runs leak containers today (seen in wud's event log). Add a
`docker`-scoped `pytest_sessionfinish` (or extend the existing DB one at `tests/conftest.py:2711`)
that force-removes any stray `shekel-test-*` on the active daemon:

```bash
docker ps -aq --filter 'name=shekel-test-' | xargs -r docker rm -f
docker network ls -q --filter 'name=shekel-test-net-' | xargs -r docker network rm
```

---

## Implementation checklist (for a fresh session)

Work happens in **this repo** (`/home/josh/projects/Shekel`), branch `dev`.

- [x] **Quick win** (pure repo change, kills routine churn) -- commit `56e48204`:
  - [x] Register `docker` marker in `pytest.ini`.
  - [x] Apply `@pytest.mark.docker` at class level to all eight daemon-touching classes across
        `tests/test_deploy/` (spawners AND `docker compose config` cases, including the
        `test_deploy_configs.py` one the draft missed).
  - [x] `scripts/test.sh`: default `-m "not docker"`, with a `PYTEST_MARKER_EXPR` opt-in. Verified:
        default run deselects the 28 marked tests and spawns **no** containers; CI's direct `pytest`
        still runs them.
- [x] **Fail-closed guard** -- commit `5df43d4f`:
  - [x] `tests/test_deploy/conftest.py` skips docker tests on the system socket, exempting CI and
        the `SHEKEL_ALLOW_HOST_DOCKER=1` override. Verified: system-daemon skips, `CI=1` runs,
        override runs, default path unchanged.
- [x] Point `docs/testing-standards.md` at this doc.
- [x] **Isolated rootless daemon (Item 4): DONE at `balance:X-br-3`, 2026-09-05.** The commands this
      checklist used to carry were refuted; see "One-time host setup" above, which is the one home
      for them. Do not restore them here: they named `pacman` for an AUR package, a setup tool that
      package does not ship, and a `slirp4netns` prerequisite that is not installed and not needed.
  - [x] `scripts/test.sh` selects the daemon itself and refuses a non-rootless one; no manual
        `export` is required.
  - [x] Verified with `SHEKEL_ALLOW_HOST_DOCKER` unset:
        `DOCKER_HOST=unix:///run/user/1000/docker.sock PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy`
        gave **25 passed, 3 skipped** against **28 skipped** on the system daemon, and zero
        `shekel-testrun-*` containers appeared on the system daemon.
  - [ ] The leftover sweep was NOT folded in and is not needed: the wrapper removes its own
        container and socket directory on every exit path, measured zero leftovers after both gating
        full suites.

## Open decisions for you

**Resolved 2026-07-04:** (1) marker + default-exclude adopted, rootless deferred as optional; (2)
the default lives in the wrapper, so CI keeps running the full set; (3) class-level marker on each
daemon-touching class. Original framing kept below for context.

1. **Rootless daemon vs CI-only.** If you'd rather not maintain a rootless daemon, the marker +
   default-exclude alone removes all local churn and you rely on CI (already isolated) for that
   coverage — those are stable deploy-config tests that change rarely. Rootless is only worth it if
   you want to iterate on the C-33 tests *locally*.
2. **Where the `-m "not docker"` default lives** — wrapper only (recommended, keeps CI running the
   full set) vs `pytest.ini` (then CI must opt back in explicitly).
3. **Marker granularity** — module-level on the whole C-33 file vs class-level on just the
   `running_stack` class (the static-assertion tests in that file don't need docker and could keep
   running locally).

## Already done (context)

On 2026-07-04 the production `wud` was flipped to **opt-in watching** (`WATCHBYDEFAULT=false`,
`WATCHEVENTS=false`, `WATCHATSTART=true`; `wud.watch=true` on real `/opt/docker` services). So even
a stray prod-daemon test run no longer spams wud — but that only shields wud. Rootless is what
actually keeps test containers off the production daemon (and out of cadvisor/alloy). See
`/opt/docker/monitoring/docker-compose.yml` (wud service comments).

## References

- Spawning fixture: `tests/test_deploy/test_proxy_trust_and_headers.py:820-968`
- Docker helper / gate: `…:849` (`_docker`), `…:87` (`_docker_available`)
- Test config: `pytest.ini` (`addopts … -n 12`, `markers =` :47)
- Entrypoint: `scripts/test.sh`
- CI (already isolated): `.github/workflows/ci.yml`, the `test` job (`runs-on: ubuntu-26.04`) and
  its step 7 (`scripts/test.sh`)
- Rootless Docker: <https://docs.docker.com/engine/security/rootless/>
