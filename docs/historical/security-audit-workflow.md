> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Security Audit Workflow for Shekel

## Context

Shekel is a solo-maintained personal budget app that manages real financial
data, runs in a Docker container on a bare-metal Arch Linux machine, and is
exposed via Nginx (LAN) and Cloudflare Tunnel (WAN). There is no QA team, no
external code reviewer, and no CI security stage beyond pylint. The developer
wants to use Claude Code to perform an in-depth security audit and asked for a
recommended workflow: local vs. web, production access, three-phase structure,
and the best overall approach.

The existing codebase already has substantial security infrastructure (bcrypt,
TOTP/MFA, Flask-WTF CSRF, Marshmallow validation, `auth_helpers.py` ownership
pattern, `test_access_control.py` IDOR coverage, `test_adversarial/`), so an
audit is not starting from zero -- it needs to verify what exists works, find
gaps, and check areas that are hard to spot by writing new code (dependency
CVEs, config drift, crypto correctness, edge-case auth flows, infra hardening).

## Recommended Approach at a Glance

1. **Run the audit locally with Claude Code CLI** -- not on the web. This
   project's deployment (Docker on the same machine as dev) needs filesystem,
   bash, and Docker access that only the local CLI has.
2. **Use plan mode (read-only) for Phase 1 and Phase 2.** Plan mode is exactly
   the tool for audit phases: Claude explores, reasons, and writes a findings
   document -- but cannot modify code. Enter plan mode with `Shift+Tab` twice.
3. **Three phases, many sessions.** Audit -> Remediation plan -> Implement.
   Each phase is a separate Claude Code session on a dedicated branch.
   Phase 1 is split across up to eight focused sessions (see the Phase 1
   session split) because the comprehensive scope includes SAST, runtime,
   supply chain, attack surface, threat model, business logic, DAST, schema,
   and ASVS -- each one deserves a fresh context so the auditor does not
   anchor on whatever the previous scanner emphasized.
4. **Parallel subagents by domain** -- map the attack surface in parallel, not
   sequentially, to keep the main context window clean.
5. **Dedicated audit branch:** `audit/security-2026-04-14` off of `dev`. Do
   not conduct the audit on `dev` or `main` directly -- `dev` is the active
   development branch per CLAUDE.md style rules. The audit commits
   (findings.md, raw scan outputs, threat model) belong on the audit branch;
   Phase 3 fixes are PR'd to `dev` in the normal workflow.
6. **Production access is read-only and passive.** For this setup (Docker on
   the same machine), "production" is a container on localhost. Read container
   state with `docker inspect`, `docker logs`, and read-only `docker exec`.
   Never give Claude Code the ability to mutate container state, edit mounted
   config, or issue `docker compose` commands against prod without the
   developer driving.
7. **Comprehensive scope:** beyond the standard SAST + manual review, include
   full supply-chain audit, git-history secrets scan, container image
   vulnerability scan, host hardening benchmark, external attack-surface
   mapping, STRIDE threat model, financial-correctness business-logic
   audit, dynamic IDOR probe, migration/schema review, OWASP ASVS L2
   mapping, and a second-opinion red-team pass over the consolidated
   findings. The audit is finished when rigor is satisfied, not on a
   schedule -- no time budget is set.

## Why Local CLI (Not Web)

| Need                                   | Local CLI | Web (claude.ai/code) |
|----------------------------------------|-----------|----------------------|
| Read `app/` source                     | yes       | yes                  |
| Read `nginx/nginx.conf`, `cloudflared/config.yml`, `Dockerfile` | yes | yes |
| Run `pip-audit`, `bandit`, `semgrep`   | yes       | no (no local tools)  |
| Inspect running Docker container       | yes       | no                   |
| Run `docker-bench-security`            | yes       | no                   |
| Read `.env` to check real secrets      | yes       | no                   |
| Spawn parallel Explore subagents       | yes       | limited              |
| 1M context window (Opus 4.6)           | yes       | yes                  |

The web version has a legitimate secondary role (see "Optional: web for second
opinion"), but for a deep audit of an app + its local deployment, the CLI has
strictly more capability.

## Tools and Concepts Glossary

This workflow assumes the developer has not previously run a security audit
and is not familiar with the tooling. Read this section before starting so
the later sections make sense. Every tool named here is an off-the-shelf
scanner, not a Shekel-specific script -- you do not need to understand how
each one works internally, only what kind of thing it catches so you can
judge its output.

### Scanners Used in This Audit

- **bandit.** Python security linter maintained by PyCQA. Reads every
  `.py` file in `app/` and flags known-bad Python patterns: hardcoded
  passwords in source, use of `eval()`/`exec()`, weak random number
  generators used for security, SQL built with string concatenation,
  subprocess calls with `shell=True`. Python-specific; nothing to do
  with other languages or config files. Output is a list of issues
  keyed by file and line with a short explanation and a CWE reference.

- **semgrep.** Pattern-based static analysis. You point it at a rule
  pack (this workflow uses `p/python`, `p/owasp-top-ten`, and `p/flask`)
  and it greps your code for matching patterns. Unlike plain grep, it
  understands Python syntax, so it can match "any call to
  `request.args.get()` whose result is passed to `os.system()`"
  instead of just matching text. Output is a list of matches per rule
  per file:line.

- **pip-audit.** Dependency CVE scanner. Takes `requirements.txt` and
  checks every pinned version against the Python Packaging Advisory
  Database (PyPA). Answers "do any of my pinned Python dependencies
  have known security vulnerabilities?" Output is a list of vulnerable
  packages with their CVE IDs, severity, and the fix version.

- **cyclonedx-py.** SBOM generator. Produces a Software Bill of
  Materials -- a JSON or XML file that lists every package and every
  transitive dependency with exact versions and license info. It is
  not itself a vulnerability scanner; it produces the input that
  `trivy sbom` later scans. The SBOM is also a useful audit artifact
  on its own -- it is the ground-truth list of what is actually
  installed in the app.

- **trivy.** Container and filesystem vulnerability scanner by Aqua
  Security. Used in this workflow three ways:
  - `trivy image <tag>` scans a built Docker image for CVEs in both OS
    packages (Debian base image layers) and Python packages inside
    the image. This catches CVEs that `pip-audit` may miss because it
    sees the fully resolved image, not just the requirements file.
  - `trivy sbom <path>` scans the CycloneDX SBOM -- a second-opinion
    CVE check against a different database than `pip-audit` uses.
    Discrepancies between the two are themselves findings.
  - `trivy config <path>` scans Dockerfile/docker-compose/Kubernetes
    manifests for misconfiguration (running as root, missing
    HEALTHCHECK, privileged containers).

- **gitleaks.** Git history secret scanner. Walks every commit in the
  repo's history (not just the current working tree) looking for
  things that match known secret patterns: AWS keys, private RSA
  keys, Stripe tokens, JWTs, database URLs with embedded passwords,
  and generic high-entropy strings. Crucial because a secret that was
  committed once and later deleted is still in git history until the
  history is rewritten. Any match is a **Critical** finding in this
  workflow even if the secret looks fake or old.

- **detect-secrets.** Second-opinion secret scanner from Yelp. Uses a
  different ruleset than gitleaks (plugin-based, with configurable
  entropy thresholds). Running both catches misses from either one.

- **lynis.** Linux host hardening audit. Run against the host machine
  itself, not against Shekel. Checks kernel sysctl flags
  (`kptr_restrict`, `dmesg_restrict`), file permissions on critical
  paths, SSH daemon config, firewall state, automatic update posture,
  installed package hygiene. Output is a list of hardening suggestions
  with a score at the end. This audits the bare-metal Arch box that
  runs the Docker container.

- **docker-bench-security.** A container that runs the CIS Docker
  Benchmark against your Docker daemon and running containers. Checks
  for containers running as root, privileged mode, missing seccomp
  profiles, mounted Docker socket, exposed Docker daemon TCP port,
  and similar daemon-level issues. Complements `trivy config`, which
  is static, by looking at the actual running configuration.

- **nmap.** Classic network port scanner. Used here only against
  `127.0.0.1` to confirm which ports are actually listening on the
  host -- a cross-check against the firewall config and the
  docker-compose port-binding strategy. Not used against anything
  external.

### Security Concepts You Will See in Findings

- **OWASP Top 10.** Ten broad categories of the most common web app
  vulnerabilities, updated periodically (the current set is the 2021
  revision: A01 Broken Access Control, A02 Cryptographic Failures,
  A03 Injection, A04 Insecure Design, A05 Security Misconfiguration,
  A06 Vulnerable Components, A07 Identification and Authentication
  Failures, A08 Software and Data Integrity Failures, A09 Logging and
  Monitoring Failures, A10 Server-Side Request Forgery). Every finding
  in this audit gets tagged with an OWASP category.

- **CWE (Common Weakness Enumeration).** A more granular catalog of
  software weakness types maintained by MITRE (e.g. CWE-284 =
  Improper Access Control, CWE-89 = SQL Injection, CWE-798 =
  Hardcoded Credentials). Scanners report CWE IDs alongside OWASP
  categories. Useful when you need to look up the exact definition
  of a weakness category.

- **CVE (Common Vulnerabilities and Exposures).** A specific, named
  bug in a specific version of a specific piece of software
  (e.g. `CVE-2024-12345` = remote code execution in Flask 3.0.0).
  `pip-audit` and `trivy` report CVEs; you respond by bumping the
  affected dependency to a fixed version.

- **SAST (Static Application Security Testing).** Reading the source
  code without running it. `bandit`, `semgrep`, `pip-audit`, `trivy`,
  `gitleaks`, and the subagent manual reviews are all SAST.

- **DAST (Dynamic Application Security Testing).** Running the app
  and sending it real HTTP requests to see how it responds. Section
  1M (the IDOR probe) is the only DAST in this workflow.

- **IDOR (Insecure Direct Object Reference).** The bug where User B
  can access User A's data by guessing or observing an ID
  (`/transactions/123` vs `/transactions/124`). Defended against in
  Shekel by `auth_helpers.py`, the ownership pattern that every
  state-changing route uses, and the "404 for both 'not found' and
  'not yours'" rule in CLAUDE.md.

- **CSRF (Cross-Site Request Forgery).** An attacker tricks a victim's
  browser into making a state-changing request to Shekel while the
  victim is logged in. Defended against by Flask-WTF, which Shekel
  already enables in `app/config.py`.

- **XSS (Cross-Site Scripting).** An attacker injects JavaScript into
  a page that is then rendered to other users. Defended against by
  Jinja2's autoescape plus the Content-Security-Policy header.

- **TOCTOU (Time Of Check to Time Of Use) race.** A bug where code
  checks a condition (e.g. "does User A own this transfer?") and
  then acts on it, but the state changes between the check and the
  act. Relevant in Shekel for concurrent POSTs that touch the same
  transfer or anchor balance. Covered in Section 1L.

- **STRIDE.** A structured threat modeling framework with six
  categories: Spoofing (pretending to be someone), Tampering
  (modifying data), Repudiation (denying an action was taken),
  Information disclosure, Denial of service, Elevation of privilege.
  Used in Section 1J to reason about what can go wrong per asset.

- **OWASP ASVS (Application Security Verification Standard).** A
  long, structured checklist of "does your app satisfy this specific
  security requirement?" organized into chapters (V2 Authentication,
  V3 Session Management, V4 Access Control, etc.) and three levels:
  L1 opportunistic, L2 standard for apps with sensitive data, L3
  critical systems. This workflow targets L2 for Shekel in
  Section 1O.

- **SBOM (Software Bill of Materials).** A machine-readable list of
  every dependency and transitive dependency in the app, with exact
  versions. Produced by `cyclonedx-py`. Used by `trivy` for
  vulnerability scanning and valuable as a standalone artifact for
  answering "what is actually installed in production."

- **Severity scale.** This audit uses Critical / High / Medium / Low
  / Info. Rough rule of thumb:
  - **Critical** -- exploitable now, leads to data loss, account
    takeover, or RCE. Drop everything and fix.
  - **High** -- exploitable under plausible conditions, or a
    Critical defense is missing. Fix this week.
  - **Medium** -- real weakness but requires additional conditions
    to exploit, or defense in depth is reduced. Fix this month.
  - **Low** -- minor hardening gap or theoretical issue.
  - **Info** -- not a vulnerability, just recorded so a future
    reader knows the question was considered.

### Execution Modes

Two execution models for the scanner-heavy sessions (S1 and S2). Pick
one before starting the session and stick with it.

- **Pre-approved scan outputs (plan mode, recommended for experienced
  auditors).** The developer runs the scanner commands in a regular
  shell before the Claude session starts, commits the raw outputs,
  and Claude only reads the files. Keeps plan mode genuinely read-
  only. Requires the developer to know how to read scanner output
  and recognize a broken run.

- **Claude drives (non-plan mode, recommended for first-time
  auditors).** Claude runs each scanner command one at a time with
  a narrow Bash allowlist and a write allowlist limited to
  `docs/audits/security-2026-04-14/**`. The developer approves each
  permission prompt as it appears and asks Claude to explain any
  output that is unclear. Takes longer but does not assume the
  developer can operate the tools directly.

Session S4 (DAST) always runs in non-plan mode regardless of which
execution model is chosen for S1/S2, because it has to write the
probe script.

## Environment Preparation (before Phase 1)

Run these on the local machine, not in Claude Code, before starting the audit
session:

1. `git checkout dev && git pull && git checkout -b audit/security-2026-04-14`
   (Shekel has no `main` branch in active development per CLAUDE.md; audits
   branch off of `dev`.)
2. `mkdir -p docs/audits/security-2026-04-14/{scans,reports,sbom}`
3. Add `.audit-venv/` to `.gitignore` so the scratch venv is not committed.
4. Install Python scanners into a scratch venv (do NOT pollute
   `requirements.txt`):
   ```
   python -m venv .audit-venv
   source .audit-venv/bin/activate
   pip install pip-audit bandit semgrep cyclonedx-bom detect-secrets
   ```
5. Install host-level scanners (one-time, via pacman on Arch; if any are not
   in repos, use the AUR or run via Docker as noted in each phase):
   - `gitleaks` -- secret scanning over git history
   - `trivy` -- container image and filesystem vulnerability scanner
   - `lynis` -- Linux host hardening audit
   - (`docker-bench-security` is run as a container, no install needed)
6. Verify scanners can read the project:
   - `pip-audit --requirement requirements.txt --format json > /dev/null`
   - `gitleaks detect --no-banner --no-git --source /home/user/Shekel --report-format json --report-path /dev/null`
7. Confirm the prod Docker container is up (`docker ps | grep shekel`) so
   runtime checks work in Section 1D.
8. Confirm Claude Code is set to Opus 4.6 1M context (`/model` should show
   the 1M variant) so a single audit session can hold the entire codebase
   plus scan outputs without compaction.

## Phase 1 -- Static + Runtime + Supply Chain + Threat Model + Business Logic + DAST + Schema + ASVS (read-only, plan mode)

Phase 1 is split across multiple Claude Code sessions to keep each one
focused. Plan mode is on for all of them except where a section explicitly
requires write access (1M DAST probe script, which writes under
`scripts/audit/` and is run in a dedicated non-plan-mode session with a
scoped allowlist). None of the plan-mode sessions write source code -- they
write `findings.md`, raw scan outputs, and the threat model document.

**Terminology note:** "Phase" refers to the three big stages of the whole
audit project (Phase 1 = research, Phase 2 = remediation plan, Phase 3 =
implementation). "Section" refers to the individual work units inside a
phase and is lettered (Sections 1A through 1P inside Phase 1). "Session"
refers to one chat session in Claude Code and is numbered S1 through S8 so
the letters cannot be confused with section letters. One session may cover
several sections; one section always lives inside exactly one session.

**Session S1:** subagent exploration + SAST + manual deep dives (Sections
1A, 1B, 1C). Expect this to be the longest session because 1C was extended
with constant-time, Fernet rotation, password policy, lockout, and PII
logging checks.

**Session S2:** runtime audit + supply chain + git history + container
image + host hardening (Sections 1D, 1E, 1F, 1G, 1H).

**Session S3:** attack-surface map + threat model (Sections 1I, 1J).

**Session S4 (non-plan mode, scoped):** DAST IDOR probe script
development and execution (Section 1M). Write allowlist: `scripts/audit/**`
and `docs/audits/security-2026-04-14/scans/idor-probe.json` only. Must run
against `shekel-dev` compose, never `shekel-prod`.

**Session S5:** financial-correctness / business-logic deep dive
(Section 1L).

**Session S6:** migration + schema audit (Section 1N).

**Session S7:** ASVS L2 mapping (Section 1O).

**Session S8:** consolidated findings + red-team pass (Sections 1K, 1P).
The consolidator starts fresh and is not biased by whichever scanner or
subagent ran last; the red-team subagent then argues with the
consolidator's output.

It is acceptable to merge adjacent sessions when context stays under ~60%,
but do NOT merge Session S8 (consolidation + red-team) with any earlier
session -- anchoring the consolidator on one of the upstream sections is
the exact bias this workflow is trying to avoid.

### 1A. Parallel OWASP-domain exploration (3 Explore subagents)

Run all three in parallel in one message:

- **Subagent A -- Identity & Access (OWASP A01, A07):** `app/routes/auth.py`,
  `app/services/auth_service.py`, `app/services/mfa_service.py`,
  `app/models/user.py`, `app/utils/auth_helpers.py`,
  `tests/test_integration/test_access_control.py`. Check: login/register flows,
  session invalidation, MFA bypass paths, backup-code exhaustion, "remember
  me" lifetime, `require_owner` fallback (note: `getattr(current_user,
  "role_id", owner_id)` defaults to OWNER when `role_id` is missing -- verify
  this cannot trigger in prod with a real user row).
- **Subagent B -- Data Layer & Injection (OWASP A03, A04):** every blueprint
  in `app/routes/`, `auth_helpers.py` callers, balance calculator, transfer
  service, recurrence engine. Check: every query filters by `user_id`; every
  soft-deletable query filters `is_deleted`; no raw SQL; joinedload usage on
  high-traffic paths; Marshmallow coverage on POST/PATCH/DELETE; Decimal
  construction from strings; transfer invariants hold under edge cases.
- **Subagent C -- Config, Secrets, Deploy (OWASP A02, A05, A08):** `config.py`,
  `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`,
  `nginx/nginx.conf`, `cloudflared/config.yml`, `gunicorn.conf.py`,
  `entrypoint.sh`, `.env.example`, `.env.dev`, `.gitignore`. Check: secret
  defaults, TOTP Fernet key handling, cookie flags, security headers (CSP,
  HSTS, X-Frame-Options -- currently the comment in nginx.conf says "Flask
  owns all other headers", verify Flask actually sets them), Nginx trusted
  proxy ranges, Cloudflare Tunnel ingress rules, Gunicorn
  `FORWARDED_ALLOW_IPS`, Docker non-root user.

Each subagent writes to its own file under `docs/audits/security-2026-04-14/reports/`
(e.g. `01-identity.md`, `02-data-layer.md`, `03-config-deploy.md`). Use
severity scale: **Critical / High / Medium / Low / Info**, mapped to OWASP ID
and the CWE if applicable.

### 1B. Automated SAST

**Execution model (important):** Plan mode restricts *edits*, but each Bash
invocation still goes through a permission prompt, and tools that write
scan output files (`> docs/audits/...`) are technically file-creating
actions that may or may not be allowed by the current permission policy.
Rather than fighting the prompt loop or running the audit outside plan
mode, use the **pre-approved scan outputs** pattern:

1. Before starting the Section 1B session, the developer runs every
   scanner command in this section in a regular shell (outside Claude
   Code) and commits the raw outputs to the audit branch under
   `docs/audits/security-2026-04-14/scans/`.
2. Section 1B Claude then runs in plan mode and only *reads* the pre-
   committed scan outputs. Claude does not execute the scanners itself;
   it reads, correlates, and normalizes.
3. The scanner commands are recorded verbatim in this section so the
   developer runs exactly what the audit workflow specifies -- no
   improvisation.

This makes Section 1B fully reproducible: the scan outputs are files in
the branch, anyone can re-read them, and a later session can diff old
outputs against new ones without re-running the scanners. It also keeps
plan mode genuinely read-only.

The alternative (run Section 1B outside plan mode with an explicit Bash
allowlist of `bandit|semgrep|pip-audit|trivy|gitleaks|lynis|detect-secrets|
cyclonedx-py|docker` and a write allowlist of `docs/audits/**`) is also
acceptable when the developer wants Claude to drive the scanners directly.
If that path is chosen, create the audit directory first, pre-approve the
exact command set, and confirm before every command that the target write
path is under `docs/audits/`.

Scanner commands (run by the developer outside Claude, or by Claude in
non-plan mode with the allowlist above):

```
# Python SAST
bandit -r app/ -f json -o docs/audits/security-2026-04-14/scans/bandit.json
bandit -r app/ -f txt  -o docs/audits/security-2026-04-14/scans/bandit.txt

# Semgrep with OWASP rulepack
semgrep --config p/python --config p/owasp-top-ten --config p/flask \
    --json --output docs/audits/security-2026-04-14/scans/semgrep.json app/
semgrep --config p/python --config p/owasp-top-ten --config p/flask \
    app/ > docs/audits/security-2026-04-14/scans/semgrep.txt

# Dependency CVEs (quick first pass; deeper supply-chain audit in 1E)
pip-audit --requirement requirements.txt \
    --format json --output docs/audits/security-2026-04-14/scans/pip-audit.json
pip-audit --requirement requirements.txt \
    > docs/audits/security-2026-04-14/scans/pip-audit.txt
```

Claude reads the outputs and normalizes every finding into the same severity
schema as the subagent reports. Duplicate findings across tools are merged.
Low-confidence findings are flagged for human review.

### 1C. Manual deep dives (main session)

The subagents give breadth; these reads give depth on the highest-risk areas
that tools miss:

1. **Crypto correctness** -- read `auth_service.py` and `mfa_service.py`
   end-to-end. The specific checks:
   - **Password hash verify** must use `bcrypt.checkpw` (or equivalent
     library call), never `hash1 == hash2` on string comparison.
   - **TOTP verify** must use `pyotp.TOTP.verify` (constant-time internally)
     and must constrain the time window (`valid_window`) to the smallest
     usable value -- typically 1 (90 seconds total drift). A window of 2+
     is a finding because it widens brute-force by 2x/3x.
   - **Backup code compare** must use `hmac.compare_digest`. A direct
     `==` on the stored hash is a Medium finding (timing side-channel).
   - **Backup code entropy** must be derived from `secrets.token_hex`,
     `secrets.token_urlsafe`, or `secrets.choice` -- never `random.*`.
     Record the number of bits of entropy per code (e.g. 8 hex chars =
     32 bits, which is too low).
   - **Session token / CSRF token compare** -- Flask-WTF handles this
     correctly out of the box, but grep the whole app for any hand-
     written `token == expected` against a stored token; any hit is a
     finding.
   - **TOTP secret at rest** -- confirm that `users.totp_secret` is stored
     encrypted (Fernet with `TOTP_ENCRYPTION_KEY`), not as plaintext. The
     audit must also answer: what is the key rotation story? If the
     Fernet key is rotated, do existing tokens still decrypt? Is there a
     versioned token format or a dual-key read path? If rotation forces
     every user to re-enroll MFA, that is a finding because the only
     remediation for a suspected key compromise becomes a destructive
     user-visible event. Record the answer in `findings.md` even if the
     answer is "no rotation story" (that itself is the finding).
   - **Fernet key handling** -- confirm `TOTP_ENCRYPTION_KEY` is loaded
     only from the environment, never hardcoded, never defaulted, and
     never logged. Grep for the env var name in `app/` and `scripts/`.
2. **Transfer invariants** -- read `app/services/transfer_service.py` and
   trace every code path that can mutate a transfer or shadow. CLAUDE.md
   lists five invariants -- verify each is enforced by a code path, not by
   convention. For each invariant, paste the enforcing source lines into
   `findings.md` as evidence. "Enforced by convention" (i.e. no code path
   actively blocks the violation) is itself a High finding for a money
   app.
3. **Balance calculator** -- read `app/services/balance_calculator.py`.
   Confirm it queries only `budget.transactions`, never `budget.transfers`.
   This must be a grep over the whole file (and over any helpers it
   imports) that produces zero matches for `transfers`. A trust-based
   "looks right" is insufficient.
4. **Open-redirect helper** -- read `_is_safe_redirect()` in
   `app/routes/auth.py:29`. Common misses to check explicitly:
   newline/tab injection (`\r\n` in the Location header), IDN homograph
   attacks, protocol-relative URLs (`//evil.example/`), backslash-
   separated authority (`\\evil.example`), and fragment-only redirects
   bypassing the scheme check. Write out the set of inputs the helper
   rejects vs. accepts.
5. **Rate limiting** -- confirm Flask-Limiter decorators on `/login`,
   `/register`, password-reset (N/A; no email reset), `/mfa/verify`, and
   any other credential-consuming endpoint. The storage backend is
   **already known to default to `memory://`** (see Preliminary Finding
   #4) -- the Section 1C task is to read `gunicorn.conf.py` and determine
   the worker count, and then quantify the effective rate-limit drift
   (e.g. "4 workers means the documented 5/15min login limit is really
   20/15min worst case").
6. **Audit log completeness** -- verify every state-changing route emits
   a `log_event()` call. Grep for every `@*.route(..., methods=[...])`
   containing a mutating verb (`POST`/`PATCH`/`PUT`/`DELETE`) and, for
   each, confirm the handler function calls `log_event`. Any miss is a
   Medium finding (audit trail gap).
7. **Password policy** -- find where password length/complexity is
   enforced (schema? service?). Record: minimum length, complexity
   rules, rejection of known-breached passwords, reuse prevention,
   maximum length (a maximum below bcrypt's 72-byte limit prevents
   silent truncation). Any of these missing is a Low-to-Medium finding
   depending on context.
8. **Account lockout** -- after N failed logins, is the account locked
   or throttled beyond the Flask-Limiter IP limit? If the only defense
   is IP rate-limiting, an attacker rotating IPs is not slowed. Record
   the answer; "no lockout, only rate-limit" is a finding for a public
   app (accepted for single-user private until the public release).
9. **PII / secrets in logs** -- grep `app/` for `logger.*current_user`,
   `logger.*password`, `logger.*totp`, `print(` against any object that
   may contain sensitive data. Read `app/utils/logging_config.py` and
   verify a redaction filter exists. An unredacted logger that handles
   auth objects is a Medium finding.

### 1D. Runtime audit of the production container

Still read-only. From the main Claude session. **Prod container names are
`shekel-prod-app`, `shekel-prod-nginx`, `shekel-prod-db`** (compose project
`shekel-prod`). Dev variants (`shekel-dev-app`, `shekel-dev-db`) exist on the
same host -- DO NOT substitute a dev container here; dev does not run Nginx,
does not use the same env, and findings against dev are not production
findings. Double-check the container by running `docker inspect
shekel-prod-app --format '{{.Config.Image}}'` and confirming the image tag
matches `requirements.txt` of the audited commit.

```
docker inspect shekel-prod-app --format '{{json .Config}}' > docs/audits/security-2026-04-14/scans/container-config.json
docker inspect shekel-prod-app --format '{{json .HostConfig}}' > docs/audits/security-2026-04-14/scans/container-hostconfig.json
docker exec shekel-prod-app id                               # confirm non-root (UID != 0)
docker exec shekel-prod-app env                              # confirm env drift
docker exec shekel-prod-app ls -la /home/shekel/app
docker logs shekel-prod-app --tail 500 > docs/audits/security-2026-04-14/scans/container-logs.txt
# Networks are literally named "backend" and "frontend" on the shekel-prod project
docker network inspect shekel-prod_backend shekel-prod_frontend > docs/audits/security-2026-04-14/scans/networks.json
```

Note: Docker Compose prefixes network names with the project name, so the
actual network names as seen by `docker network ls` are
`shekel-prod_backend` and `shekel-prod_frontend`. Within `docker-compose.yml`
they are declared as `backend` and `frontend`. Verify the exact names with
`docker network ls | grep shekel` before running the inspect command.

Then do a **drift check**: compare `nginx/nginx.conf` in the repo to what the
Nginx container is actually serving (`docker exec shekel-prod-nginx cat /etc/nginx/nginx.conf`).
If they differ, that is itself a finding. Repeat the comparison for
`gunicorn.conf.py` and any other mounted config.

### 1E. Supply chain audit

Beyond the quick `pip-audit` from 1B, do a deeper supply-chain pass:

```
# Generate a CycloneDX SBOM for the prod requirements
cyclonedx-py requirements --of JSON \
    -o docs/audits/security-2026-04-14/sbom/sbom.json requirements.txt
cyclonedx-py requirements --of XML  \
    -o docs/audits/security-2026-04-14/sbom/sbom.xml  requirements.txt

# Resolved transitive tree (catches indirect deps that pip-audit may underweight)
pip install --dry-run --report \
    docs/audits/security-2026-04-14/sbom/resolved-tree.json -r requirements.txt

# Re-scan the SBOM with trivy for a second opinion (different DB than pip-audit)
trivy sbom docs/audits/security-2026-04-14/sbom/sbom.json \
    --format json --output docs/audits/security-2026-04-14/scans/trivy-sbom.json
trivy sbom docs/audits/security-2026-04-14/sbom/sbom.json \
    > docs/audits/security-2026-04-14/scans/trivy-sbom.txt
```

Then Claude reads:
1. Every direct dependency in `requirements.txt` -- last release date, GitHub
   stars, maintenance signal, security advisory history. Flag any unmaintained
   or single-maintainer packages.
2. Pin discipline -- every direct dep must be pinned to an exact version
   (`==`, not `>=` or `~=`). A loose pin in `requirements.txt` is itself a
   finding because it makes builds non-reproducible and dependency confusion
   attacks easier.
3. License audit -- flag any GPL/AGPL-licensed deps if the project does not
   intend to be GPL.
4. Cross-check `pip-audit` and `trivy` outputs -- discrepancies are findings
   in their own right.

### 1F. Git history secrets scan

The repo currently has `.env` gitignored, but a secret may have been committed
historically and never rotated. Scan the entire git history:

```
gitleaks detect --no-banner --source /home/user/Shekel \
    --report-format json \
    --report-path docs/audits/security-2026-04-14/scans/gitleaks.json
gitleaks detect --no-banner --source /home/user/Shekel \
    --report-format sarif \
    --report-path docs/audits/security-2026-04-14/scans/gitleaks.sarif

# Second-opinion scan with detect-secrets (different ruleset)
detect-secrets scan --all-files --baseline \
    docs/audits/security-2026-04-14/scans/detect-secrets-baseline.json
```

Any detected secret is a **Critical finding** even if the secret looks fake or
old -- the remediation is to rotate AND remove from history (`git filter-repo`
or BFG). Note clearly that this requires force-pushing rewritten history,
which is destructive and must be done by the developer.

### 1G. Container image vulnerability scan

Scan the actual built Docker image, not just `requirements.txt`. The image
includes the Python runtime, Debian/Alpine packages, and any layers added by
the multi-stage build:

```
# Find the running image tag
IMAGE=$(docker inspect shekel-prod-app --format '{{.Config.Image}}')

# OS + language vuln scan
trivy image "$IMAGE" \
    --format json \
    --output docs/audits/security-2026-04-14/scans/trivy-image.json
trivy image "$IMAGE" \
    --severity CRITICAL,HIGH,MEDIUM \
    > docs/audits/security-2026-04-14/scans/trivy-image.txt

# Misconfiguration scan of the Dockerfile + compose files
trivy config /home/user/Shekel \
    --format json \
    --output docs/audits/security-2026-04-14/scans/trivy-config.json
trivy config /home/user/Shekel \
    > docs/audits/security-2026-04-14/scans/trivy-config.txt
```

Findings include OS-level CVEs in the base image (Python 3.14 slim ->
Debian), Python wheels with CVEs that pip-audit missed, and Dockerfile
anti-patterns (root user, no HEALTHCHECK, etc. -- this project already
addresses these but the scan confirms).

### 1H. Host hardening + Docker daemon benchmark

The container runs on a bare-metal Arch Linux host that is also the dev
machine. Hardening here matters because if the host is compromised, the
Cloudflare Tunnel becomes a pivot.

```
# Linux host hardening audit
sudo lynis audit system --quick \
    --logfile docs/audits/security-2026-04-14/scans/lynis.log \
    --report-file docs/audits/security-2026-04-14/scans/lynis-report.dat

# Docker daemon CIS benchmark
docker run --rm --net host --pid host --userns host --cap-add audit_control \
    -v /var/lib:/var/lib:ro -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v /usr/lib/systemd:/usr/lib/systemd:ro -v /etc:/etc:ro \
    docker/docker-bench-security \
    > docs/audits/security-2026-04-14/scans/docker-bench.txt
```

Lynis findings to look at first:
- Kernel hardening flags (`kernel.kptr_restrict`, `kernel.dmesg_restrict`,
  `kernel.unprivileged_userns_clone`)
- File permissions on `/home/user/Shekel/.env` (must be `600`)
- SSH config if SSH is exposed to LAN
- Firewall state (nftables/iptables rules)
- Automatic security updates -- Arch is rolling, so the hardening question is
  whether the host is updated regularly

Docker-bench findings to look at first: containers running as root, missing
`--security-opt no-new-privileges`, missing seccomp profile, mounted Docker
socket, exposed Docker daemon TCP socket.

### 1I. External attack-surface mapping

Document everything that is reachable from the public internet, the LAN, or
inside the Docker network. This is read-only and Claude does most of it from
config files plus a few `nmap`/`ss` calls on the host.

```
# What ports are listening on the host?
ss -tulpn > docs/audits/security-2026-04-14/scans/host-listening-ports.txt

# What does the local Docker network look like?
docker network ls > docs/audits/security-2026-04-14/scans/docker-networks.txt
docker network inspect $(docker network ls -q) \
    > docs/audits/security-2026-04-14/scans/docker-networks-detail.json

# Local-host scan of the running app (LAN side)
nmap -sV -p 80,443,5432,8000 127.0.0.1 \
    > docs/audits/security-2026-04-14/scans/nmap-localhost.txt

# Cloudflare Tunnel ingress -- which hostnames map to which services
cat /home/user/Shekel/cloudflared/config.yml \
    > docs/audits/security-2026-04-14/scans/cloudflared-ingress.txt
```

Claude then writes `docs/audits/security-2026-04-14/reports/04-attack-surface.md`
with sections:

- **Public (WAN via Cloudflare Tunnel):** every hostname, the service it maps
  to, the auth gate at the edge (Cloudflare Access? none?), and the OWASP
  attack categories that apply.
- **LAN (Nginx on the host):** every port reachable from the LAN, the auth
  gate at Nginx, and what an attacker on the LAN could touch.
- **Container internal (Docker backend network):** services that are *not*
  externally reachable but are reachable from a compromised app container
  (the database, mainly).
- **Host (loopback only):** services bound to 127.0.0.1 that should not be
  reachable from outside the host.

For each entry, list a) the auth gate, b) the rate limit (or "none"), c) the
data exposed if the auth gate fails, d) the blast radius.

### 1J. Threat model (STRIDE)

Write `docs/audits/security-2026-04-14/reports/05-threat-model.md`. STRIDE
covers six categories per asset: Spoofing, Tampering, Repudiation, Information
disclosure, Denial of service, Elevation of privilege.

The assets to model:

1. **User account** -- email, password hash, TOTP secret, backup codes,
   session cookie
2. **Financial data** -- transactions, balances, transfers, paychecks, debt
   accounts
3. **Anchor balance** -- the single source of truth for projections; if
   tampered the entire app is wrong
4. **Audit log** -- if tampered, repudiation becomes possible
5. **Cloudflare Tunnel credentials** -- pivot to taking over the public
   endpoint
6. **Docker socket / host shell** -- pivot to entire host

For each asset, the model answers: what can an external attacker do? what
can a logged-in attacker (companion role) do? what can a compromised
dependency do? what can a compromised host do?

The threat model is the deliverable that tells the developer what they
should be most worried about and where the remaining audit effort should
focus -- it is also what stays useful long after this audit is done.

### 1K. Consolidate into `findings.md`

The final deliverable of Phase 1 is one file:
`docs/audits/security-2026-04-14/findings.md`. Done in a fresh Claude Code
session (Session S8, per the session split at the top of Phase 1) so the
consolidator is not anchored on whichever scanner or subagent ran last.
The red-team pass in Section 1P runs inside the same Session S8, after
the consolidator finishes its initial draft.

The session loads:
- All eight subagent / domain reports from `reports/`: `01-identity.md`,
  `02-data-layer.md`, `03-config-deploy.md`, `04-attack-surface.md`,
  `05-threat-model.md`, `06-asvs-l2.md`, `07-business-logic.md`,
  `08-migrations-schema.md`
- All scanner outputs from `scans/` (bandit, semgrep, pip-audit, trivy
  image, trivy SBOM, trivy config, gitleaks, detect-secrets, lynis,
  docker-bench, container inspect, network inspect, nmap, idor-probe,
  per-table schema dumps)
- The SBOM and resolved-tree from `sbom/`

Then it normalizes everything into one report with this structure:

```
# Shekel Security Audit -- 2026-04-14

## Summary
- Scope: Shekel commit <sha>, audit branch audit/security-2026-04-14
- Tool versions: bandit X.Y, semgrep X.Y, pip-audit X.Y, trivy X.Y,
  gitleaks X.Y, lynis X.Y, docker-bench (date)
- Duration: total wall-clock, per-phase breakdown
- Counts: N critical, N high, N medium, N low, N info
- Top three risks (one paragraph each)

## Threat Model Summary
- Brief asset/attacker matrix from 1J, with pointers into Findings

## Findings
### F-001: <short title>
- Severity: High
- OWASP: A01:2021 -- Broken Access Control
- CWE: CWE-284
- Phase / source: 1A subagent A | 1B bandit | 1F gitleaks | etc.
- Location: app/routes/transactions.py:142
- Description: ...
- Evidence: <code snippet or tool output>
- Impact: ...
- Recommendation: ...
- Status: Open

### F-002: ...

## Accepted Risks
Findings the developer is intentionally NOT fixing, with rationale. (Empty
at the end of Phase 1 -- populated during Phase 2 triage.)

## Scan Inventory
A table mapping every scan output file to which findings it generated,
proving nothing was discarded.
```

Every finding carries file + line. Evidence is quoted. Every scan output
file is referenced in at least one finding or in the Scan Inventory -- if a
scanner produced output but no findings, the inventory says so explicitly,
which proves the scan was actually read.

No vague "consider reviewing X" entries -- each finding is either a concrete
defect with a fix or is demoted to Info.

### 1L. Financial-correctness and business-logic audit (new session, plan mode)

Static scanners and OWASP checklists will not catch a `float` leaking into
a `Decimal` chain, a TOCTOU race between an ownership check and a mutation,
a transfer invariant that can be violated by two concurrent POSTs, or a
rounding bug in the paycheck calculator. For a money app, these are the
bugs that matter most. Run this phase in its own fresh session so the
auditor is not distracted by CVE noise.

Scope and method:

1. **Type-purity grep.** Grep `app/services/` and `app/routes/` for every
   occurrence of `float(`, bare `Decimal(` whose argument is not a string
   literal (e.g. `Decimal(x)` where `x` may be a float), `** 0.5`,
   `/ 100` without Decimal context, and `round(` with no
   second argument on a Decimal. Any hit is a potential finding. Record
   location + classification (safe / needs-review / finding).
2. **Concurrency / TOCTOU.** Enumerate every endpoint in `app/routes/`
   that performs a read-check-write pattern:
   - `get_or_404(...)` + `user_id` check, then `commit()` -- confirm the
     object row is locked (`with_for_update()`) or the update is a CAS
     (`UPDATE ... WHERE id=X AND user_id=Y AND version=Z`).
   - Transfer status transitions (`projected -> done`, `done -> settled`)
     -- confirm two concurrent POSTs from different tabs cannot both
     succeed.
   - Anchor balance updates -- the single source of truth; concurrent
     anchor updates must be serialized.
   - Delete-then-recreate paths (if any) must be transactional.
   For each endpoint, write out the pseudocode of the expected race and
   the actual code's defense. "Unlikely under single user" is an
   **Accept-with-note**, not an Info, because the app intends to go
   public.
3. **Transfer invariants one-by-one.** Re-read
   `app/services/transfer_service.py` against the five invariants from
   CLAUDE.md:
   1. Every transfer has exactly two linked shadow transactions.
   2. Shadow transactions are never orphaned and never created without
      their sibling.
   3. Shadow amounts, statuses, and periods always equal the parent.
   4. No code path directly mutates a shadow.
   5. `balance_calculator` queries only `budget.transactions`, never
      `budget.transfers`.
   For each invariant, quote the enforcing code into `findings.md`.
   "No enforcing code path exists; we rely on callers to do the right
   thing" is a **High** finding even if no current caller violates it.
4. **Rounding and decimal places.** Read the paycheck calculator and the
   tax-bracket service. Confirm every intermediate step stores a
   `Decimal` quantized to the expected precision (`Decimal.quantize(
   Decimal("0.01"), rounding=ROUND_HALF_EVEN)` or equivalent). A bare
   chain of Decimals with no explicit quantize at output is a Low-to-
   Medium finding because the accumulated drift can be real money over
   26 pay periods a year.
5. **Negative-amount and zero-amount handling.** For every route that
   accepts an amount, confirm the Marshmallow schema rejects negative
   amounts where appropriate AND the database CHECK constraint backs it
   up. A route accepting negative amounts with no validator is
   potentially exploitable for balance manipulation.
6. **Idempotency of mutations.** For every POST that can plausibly be
   double-clicked (create transfer, record paycheck, mark settled), is
   there an idempotency key, a unique-constraint-based deduper, or at
   least a rate-limit that prevents the duplicate? If no protection
   exists, record as Low (UX) or Medium (financial) depending on the
   blast radius.

Output: `reports/07-business-logic.md` with a one-paragraph summary per
check and a list of findings keyed into the Section 1K consolidator.

### 1M. Dynamic authorization testing -- live IDOR probe (non-plan-mode session)

Static IDOR review by reading `auth_helpers.py` callers proves the code
*looks* right. It does not prove that every endpoint in practice returns
404 when User B asks for User A's objects. A lightweight DAST probe
executes the actual HTTP requests and records what comes back.

**CRITICAL:** this phase runs against `shekel-dev` compose ONLY. Never
run the probe against `shekel-prod-app`. Confirm the target URL is
`localhost:<dev-port>` before executing.

Procedure:

1. Spin up dev compose: `docker compose -f docker-compose.dev.yml up -d`.
2. Seed two owner users A and B and one companion user C via
   `scripts/seed_user.py` (parameterize with a `--user-name` flag if
   the script does not already support it; if a new flag is needed, add
   it on a separate branch and keep this phase read-only against the
   seed script).
3. Log in as A, walk the entire app (transactions, accounts, transfers,
   paychecks, templates, scenarios, debts, anchors), and record the
   integer IDs of every object A created. A simple approach: run a
   read query in `docker exec shekel-dev-db psql ...` against every
   user-owned table filtered by `user_id = <A>` and dump the IDs as
   JSON.
4. Log in as B (fresh client, fresh session) and attempt every
   `GET`, `POST`, `PATCH`, `PUT`, `DELETE` verb listed in each route
   blueprint against A's IDs. Expected for all: **HTTP 404** (per
   CLAUDE.md "404 for both 'not found' and 'not yours'"). Any other
   response code is a finding:
   - 200 = Critical (direct IDOR).
   - 403 = Medium (information leak: 403 vs 404 distinguishes existence).
   - 500 = High (unhandled exception reveals stack or state).
   - 302 to a user-facing page rendering A's data = Critical.
5. Repeat the entire probe with an unauthenticated client. Expected:
   302 to login or 401. Any 200 is Critical.
6. Repeat the entire probe with companion user C. The owner/companion
   boundary must be enforced. Any cross-role access that is not
   documented as permitted is a finding.
7. For HTMX routes, set the `HX-Request: true` header so the response is
   the partial template and the probe captures what the partial would
   render. HTMX partials that leak data are as bad as full pages.
8. Record every request/response pair (method, path, status, first 200
   bytes of body) as JSON to
   `docs/audits/security-2026-04-14/scans/idor-probe.json`. Summary of
   failures goes into `findings.md`.
9. Commit the probe script itself to `scripts/audit/idor_probe.py` so
   Phase 3 can re-run it as a regression after every access-control fix
   and the final audit closes by re-running it with zero failures.

This phase is the only phase where Claude may write new code (the probe
script). It is executed in a dedicated non-plan-mode session with the
write allowlist `scripts/audit/**` and the Bash allowlist `docker|
docker-compose|psql|curl|python`. Do not expand the allowlist.

### 1N. Migration and database schema audit (new session, plan mode)

Alembic migrations can introduce integrity bugs that are invisible to
SAST and to unit tests that only exercise the post-migration state. For
a money app, a downgrade that drops a column without backup is a
data-loss vector.

Procedure:

1. Enumerate every file in `migrations/versions/` (or wherever Alembic
   migrations live in this repo).
2. For each migration, verify:
   - The `downgrade()` function actually reverses `upgrade()`. Not
     `pass`. Not `raise NotImplementedError`. Not partial.
     NotImplementedError is acceptable only with a comment explaining
     why reversal is impossible AND a developer-level sign-off.
   - Destructive operations (`drop_table`, `drop_column`,
     `alter_column` with type narrowing, `rename_*`) have been reviewed
     and are not silently lossy.
   - Every new NOT NULL column on a populated table has a
     `server_default` or a data-migration step that fills existing rows
     before the constraint is added.
   - Every new CHECK / UNIQUE / FK constraint is named (pattern
     `ck_<table>_<description>` per coding standards).
3. Live schema drift check:
   ```
   for t in budget.transactions budget.transfers budget.accounts ...
   do
       docker exec shekel-prod-db psql -U postgres -d shekel \
           -c "\\d+ $t" > docs/audits/security-2026-04-14/scans/schema-$t.txt
   done
   ```
   Then diff the `\d+` output against the SQLAlchemy model definitions
   in `app/models/`. Any drift is a finding. Drift commonly happens
   when a migration was applied by hand or when a model was changed
   without a migration.
4. Confirm every financial `Numeric(12,2)` column has a matching
   database-level `CHECK(column >= 0)` (or whatever range the
   Marshmallow schema enforces). Schema-level validation without
   database-level enforcement is a finding because a raw SQL path
   (script, admin, future endpoint) bypasses the schema.
5. Confirm every `is_deleted` column has an index, and confirm every
   query path respects it (this was already checked in Subagent B --
   here we confirm at the DB level that the index exists).
6. For every FK, confirm the `ON DELETE` behavior matches the coding
   standards (CASCADE for user_id, RESTRICT for ref, etc.) by reading
   the actual `\d+` output. The model's `ondelete=` parameter and the
   live constraint can diverge if a migration was edited post-hoc.

Output: `reports/08-migrations-schema.md` with one entry per migration
and one entry per schema drift.

### 1O. OWASP ASVS Level 2 mapping (new session, plan mode)

OWASP Application Security Verification Standard is the gold-standard
checklist for "is this app actually secure" vs. "does it fail the top
10." ASVS L2 is the right bar for an app that intends to go public and
handles financial data.

Procedure:

1. Load the ASVS v4.0.3 (or current) L2 requirements for these
   chapters. Do not fabricate from memory -- reference the actual ASVS
   document:
   - V2  Authentication
   - V3  Session Management
   - V4  Access Control
   - V5  Validation, Sanitization, Encoding
   - V6  Stored Cryptography
   - V7  Error Handling and Logging
   - V8  Data Protection
   - V9  Communications
   - V10 Malicious Code
   - V14 Configuration
2. For each L2 requirement, write one row in the output table:
   `| ASVS ID | Requirement | Pass/Fail/N-A | Code location or reason |`
3. Pass requires a specific code location (file:line) that satisfies
   the requirement. "We think we do this" without a file:line is Fail.
4. N/A is allowed only with a written reason tied to the "Not in scope
   because the feature does not exist" list in the preliminary
   findings (e.g. V2.2.1 about password-reset tokens is N/A because
   there is no password-reset flow).
5. Every Fail becomes a finding in `findings.md` at the severity level
   defined by ASVS itself (L2 requirements are Medium-or-higher by
   definition).

Output: `reports/06-asvs-l2.md`. This is the single biggest rigor win
in the whole audit -- it forces a structured answer to "is this actually
secure" rather than "did any scanner complain."

### 1P. Red-team pass over `findings.md` (fresh subagent)

After Session S8 consolidates `findings.md`, spawn **one** fresh
Explore subagent whose sole job is to argue with the findings. The
consolidator in 1K has its own biases: it may under-rate a finding
because the scanner said Low, or over-rate a finding because the
subagent said Critical, or mark a verification "done" when it was a
code-read not a test. The red-team pass catches this.

The red-team subagent prompt explicitly:

- Receives only `findings.md` and the source files referenced.
- Is told to look for three failure modes:
  1. **Severity inflation** -- a High that is really a Medium.
  2. **Severity deflation** -- a Low/Info that is really a
     High/Critical in context (e.g. an access-control Info that turns
     Critical because the affected data is financial).
  3. **Verification-by-assertion** -- any finding that says
     "verified" where the verification is a code read, not a test, a
     DAST probe, or a reproducible scan output.
- Produces an appendix `findings.md -- Red Team Appendix` with one
  entry per challenged finding:
  `F-007: challenged severity (High -> Medium). Reason: ...`
- Does NOT overwrite the consolidator's findings. Every challenge must
  be resolved by the developer in Phase 2 triage: accept the red team
  (re-rate the finding) or defend the original rating in writing.

This is the only place in the whole workflow where a subagent is
pointed at a document written by Claude, not at source code. That
framing is intentional: the red-team pass is auditing the audit itself.

## Phase 2 -- Remediation Plan (fresh session, plan mode)

**Session 2: brand new Claude Code session.** Load only `findings.md` plus the
critical files it references -- do NOT reuse Phase 1 context. The fresh
context prevents the planner from anchoring on whatever the auditor was
focused on last.

Produce `docs/audits/security-2026-04-14/remediation-plan.md`:

1. Triage every finding: **Fix now / Fix this week / Fix this month / Accept**.
2. Group fixes by domain so a single PR doesn't touch ten unrelated files.
3. For each fix: the change sketch, tests to add, regression risk, rollback
   plan, and a complexity label (Small / Medium / Large) that describes
   scope, not schedule.
4. Order by severity + blast radius. Criticals jump to the front even if the
   fix is harder.
5. Dependencies between fixes are called out -- e.g. "F-007 depends on F-003
   because both touch `auth_service.login`".

The remediation plan also notes which findings are intentionally deferred and
why, so the deferred list is not forgotten.

## Phase 3 -- Implementation (normal workflow)

**Session 3 (and onward): normal Claude Code sessions, NOT plan mode.** Work
through the remediation plan in order. For each group:

1. Create a feature branch off `dev`: `fix/security-F003-F005-rate-limit`.
2. Implement the fix.
3. For every Critical or High finding, add a **regression test** that
   asserts the vulnerable path now returns the correct secure response
   (404 for cross-user access, 400 for validation, 403 where
   appropriate, etc.). The regression test lives in
   `tests/test_adversarial/`, not `tests/test_routes/`, because it is
   a hostile-path test by construction. If the finding is an
   access-control issue, the regression test must be authored in the
   form "User B requests User A's object and the response is 404" so
   that a future refactor that re-introduces the IDOR fails the test.
4. Re-run the Phase 1 scanners on the touched area. The finding must
   disappear. Examples:
   - `bandit -r app/routes/auth.py` for a bandit finding.
   - `semgrep --config p/python --config p/owasp-top-ten app/routes/
     auth.py` for a semgrep finding.
   - `pip-audit --requirement requirements.txt` for a dependency
     bump.
   Paste the scanner output (or its absence) into the PR description.
5. Run targeted test suites for the modified files first -- single
   file or single test for fast feedback per
   `docs/testing-standards.md`.
6. **Re-run the full test suite once per PR, not per batch.** Per
   `docs/testing-standards.md`, split by directory (`pytest
   tests/test_services/`, `pytest tests/test_routes/`, etc.) with
   `timeout 720` per group. Paste the pass/fail counts for each group
   into the PR description.
7. **Re-run the IDOR probe from 1M** after every access-control fix.
   The expected result is that every request in `idor-probe.json`
   returns 404 (or the documented expected status). A regression in
   the probe blocks the PR.
8. **Re-run pylint on every touched file:** `pylint app/routes/
   auth.py --fail-on=E,F`. No new warnings. Paste the output into
   the PR description.
9. Update `findings.md` to mark the finding as Fixed with the commit
   SHA and PR number. Do not mark Fixed until all of steps 3-8 have
   been completed and their outputs recorded.
10. Merge into `dev` on the normal cadence.

Never batch unrelated criticals into one PR -- if one fix has to be
reverted, the unrelated fixes revert with it. Within one PR, always
prefer smaller fixes that can be reverted independently.

**Phase 3 closeout (before declaring the audit done):**

1. Re-run the full SAST sweep on `dev`: `bandit -r app/`, `semgrep`,
   `pip-audit`. No previously-open findings may match. Any new finding
   is a new audit cycle, not a shortcut.
2. Re-run the DAST IDOR probe against a fresh dev compose with the
   final code. Zero failures.
3. Re-run the full test suite split by directory. Zero failures.
4. Confirm `findings.md` has every non-accepted finding marked Fixed
   with a commit SHA and PR number.
5. Re-run the schema drift check from 1N -- live DB schema must match
   the models after every migration has been applied.

## Optional: web (claude.ai/code) for Second Opinion

The web version cannot replace the local CLI for this project, but it can
usefully provide an independent second pass on `findings.md`. After Phase 1:

1. Push `audit/security-2026-04-14` to GitHub (private repo).
2. Open a web Claude session pointed at the branch.
3. Ask it to *only review* `findings.md` against the source files the findings
   reference, and flag (a) false positives, (b) missed high-severity items in
   the same files, (c) over-severe ratings.
4. Its comments feed back into Phase 2 triage.

Do not use the web version for the primary audit. Do not give it write access.

## Production Access -- Guardrails

For this deployment (Docker on the same Arch machine):

- **Allowed in Phase 1:** `docker inspect`, `docker logs`, `docker exec <ctr>
  <read-only command>`, reading mounted configs, reading the running Nginx
  config.
- **Allowed in Phase 3:** deployment via the existing `scripts/deploy.sh`
  *only when the developer runs it*. Claude Code should not be the one
  executing the deploy script -- it should write the fix, run tests, and hand
  off.
- **Forbidden always:** `docker exec` with write side effects, `docker compose
  down/up` on prod, editing mounted config files, touching `.env`, restarting
  the container, modifying Cloudflare Tunnel config, running `flask db
  upgrade` against the prod database. These require the developer to drive.

If the developer wants a remote pair of hands for the production machine,
SSH-only access via a scoped user (no sudo, no docker group, no write on
`/home/user/Shekel`) is the safer pattern -- but given the container
is local, there is no need to set that up for this audit.

## Preliminary Findings (Pre-Verified Against the Codebase)

These were surfaced during planning exploration and re-verified against the
actual repository on 2026-04-14. Items marked **CONFIRMED** are already real
findings and should be copied straight into `findings.md` at the start of
Phase 1. Items marked **RESOLVED** are already safe and are listed only so
Phase 1 does not re-investigate them. Phase 1 still runs its full pass --
these notes just prevent anchoring on guesses and prevent duplicate work.

1. **CONFIRMED -- `.env.dev` is tracked and stale.** `.env` is correctly
   gitignored, but `.env.dev` is committed. Its current contents are safe
   placeholder values (`dev_password_change_me`,
   `dev-secret-key-not-for-production`) so this is not a live leak -- but
   line 1 references `FLASK_APP=src/flask_app/app.py`, a path that does not
   exist in this project (the actual entry is `run.py`). The file is either
   dead code or out of sync. Remediation: delete or bring in sync. Severity
   Low (confusing, not exploitable).

2. **RESOLVED -- `require_owner` role_id fallback.**
   `app/utils/auth_helpers.py:52` contains
   `if getattr(current_user, "role_id", owner_id) != owner_id:`, which would
   default to the owner role if `role_id` were missing on the user object.
   Verified against `app/models/user.py`: the `users.role_id` column is
   `nullable=False` with `server_default="1"`, so a real production user row
   cannot have `role_id IS NULL`. The `getattr` is defense-in-depth for test
   fixtures only. Severity: Info. Section 1A subagent A must still confirm
   no code path (raw INSERT, migration, seed script) inserts a user with
   `role_id=NULL` and no ORM path clears it.

3. **CONFIRMED -- Incomplete security headers.** `nginx/nginx.conf:~157`
   sets only `X-Content-Type-Options: nosniff` on static files and comments
   "Flask owns all other headers." Verified: Flask
   (`app/__init__.py:409-428`) sets `X-Content-Type-Options`,
   `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
   `Permissions-Policy`, and a `Content-Security-Policy`. Two real gaps:
   - **HSTS is NOT set** anywhere in Flask or Nginx. For an app reachable
     over HTTPS via Cloudflare Tunnel, this is a real hardening gap.
     Severity Medium. Remediation: add `Strict-Transport-Security:
     max-age=31536000; includeSubDomains; preload` either in Flask or at
     the Nginx layer (with care -- preload is a one-way commitment).
   - **CSP uses `'unsafe-inline'` in `style-src`** and permits external
     CDN hosts (`cdn.jsdelivr.net`, `unpkg.com`, `fonts.googleapis.com`,
     `fonts.gstatic.com`) in `script-src`/`style-src`/`font-src`. CDN
     dependencies are a supply-chain surface. Severity Medium. Remediation
     options: self-host the CDN assets (preferred) OR pin Subresource
     Integrity (SRI) hashes on every `<link>`/`<script>` referencing those
     CDNs AND add `require-sri-for script style` to the CSP.

4. **CONFIRMED -- Flask-Limiter in-memory storage backend.**
   `app/extensions.py:31` hardcodes
   `Limiter(..., storage_uri="memory://")`. Under multi-worker Gunicorn
   each worker keeps its own counter (so the effective rate limit is
   `limit * worker_count`); on container restart the counters reset.
   Severity Medium. Remediation: add a Redis (or at minimum a shared
   filesystem-backed) `storage_uri` for production, OR document and
   enforce a single-worker Gunicorn configuration. Section 1A subagent A
   must trace how `gunicorn.conf.py` configures workers and record the
   blast radius.

5. **UNVERIFIED -- Dependency freshness.** `requirements.txt` looks current
   but has not been audited against CVE feeds in this session. `pip-audit`
   and `trivy sbom` in Sections 1B/1E will provide authoritative answers.
   Flagged here as a likely source of findings, not a pre-confirmed one.

6. **UNVERIFIED -- TOTP encryption key rotation story.** CLAUDE.md lists
   `TOTP_ENCRYPTION_KEY` as a required env var. Section 1C.1 must determine
   whether the app supports rotating the Fernet key without losing access
   to existing enrolled TOTP secrets (versioned token format, re-wrap
   migration, or dual-key read path). If there is no rotation story, that
   is a finding because the only remediation for key compromise would be
   forcing every user to re-enroll MFA.

### Not in Scope Because the Feature Does Not Exist

Verified against the repo on 2026-04-14. Phase 1 subagents must NOT fabricate
coverage for these -- if a subagent claims to have audited one of these, that
is itself a finding (hallucination in the audit itself):

- **File uploads:** no upload handling, no `request.files` usage, no
  `ALLOWED_EXTENSIONS` configuration in the app code.
- **Password-reset email flow:** no email sending, no `itsdangerous` reset
  token generation, no `smtplib`/Flask-Mail. Password resets are a manual
  out-of-band operation.
- **WebSockets / SSE:** no `flask-sock`, no `socket.io`, no server-sent
  events.
- **Background-job daemons:** no Celery, no RQ, no APScheduler daemon. Only
  standalone maintenance scripts under `scripts/`.
- **Separate admin UI:** there is no `/admin` blueprint; the owner/
  companion role distinction is enforced inline. The app IS the admin
  interface for the owner role.

If any of the above is later added to the codebase, this audit is stale for
that feature and must be re-run for the new surface.

## Critical Files Any Auditor Must Read

- `app/config.py` -- secret and cookie handling
- `app/__init__.py` -- session/login-manager wiring, security headers if any
- `app/routes/auth.py` -- auth flows including open-redirect helper
- `app/services/auth_service.py` and `app/services/mfa_service.py` -- crypto
- `app/utils/auth_helpers.py` -- ownership pattern (every route's safety net)
- `app/services/transfer_service.py` -- financial invariants
- `app/services/balance_calculator.py` -- query scoping
- `nginx/nginx.conf`, `gunicorn.conf.py`, `Dockerfile`, `docker-compose.yml`,
  `cloudflared/config.yml`, `entrypoint.sh` -- deploy surface
- `.env.example`, `.env.dev`, `.gitignore` -- secret hygiene
- `requirements.txt` -- dependency CVEs
- `tests/test_integration/test_access_control.py`,
  `tests/test_adversarial/test_hostile_qa.py` -- what's already protected

## Verification the Audit Was Thorough

Phase 1 is complete when ALL of these hold:

- [ ] `findings.md` has entries (or an explicit "nothing found") for every
      OWASP A01-A10 category.
- [ ] All eight domain reports exist under `reports/`:
      `01-identity.md`, `02-data-layer.md`, `03-config-deploy.md`,
      `04-attack-surface.md`, `05-threat-model.md`, `06-asvs-l2.md`,
      `07-business-logic.md`, `08-migrations-schema.md`.
- [ ] All scanner outputs exist under `scans/`: `bandit.json`,
      `bandit.txt`, `semgrep.json`, `semgrep.txt`, `pip-audit.json`,
      `pip-audit.txt`, `trivy-image.json`, `trivy-image.txt`,
      `trivy-sbom.json`, `trivy-sbom.txt`, `trivy-config.json`,
      `trivy-config.txt`, `gitleaks.json`, `gitleaks.sarif`,
      `detect-secrets-baseline.json`, `lynis-report.dat`, `lynis.log`,
      `docker-bench.txt`, `container-config.json`,
      `container-hostconfig.json`, `container-logs.txt`, `networks.json`,
      `host-listening-ports.txt`, `docker-networks.txt`,
      `docker-networks-detail.json`, `nmap-localhost.txt`,
      `cloudflared-ingress.txt`, `idor-probe.json`, and one
      `schema-<table>.txt` per user-data table audited in 1N.
- [ ] SBOM exists under `sbom/sbom.json` (and `sbom.xml`) and the
      resolved-tree exists under `sbom/resolved-tree.json`, and both
      have been read by Claude (not just generated and ignored).
- [ ] Every medium-or-higher finding has a file:line reference and
      quoted evidence.
- [ ] All six preliminary findings listed in this document are each
      either confirmed (as findings) or explicitly resolved (as Info
      with a reason).
- [ ] Runtime drift check (repo configs vs container configs) has been
      run for `nginx.conf`, `gunicorn.conf.py`, and any other mounted
      config file. Each comparison is recorded under `scans/`.
- [ ] STRIDE threat model in `reports/05-threat-model.md` covers all six
      assets (account, financial data, anchor balance, audit log,
      Cloudflare Tunnel creds, host shell), with one matrix row per
      (asset, STRIDE category, attacker type).
- [ ] ASVS L2 mapping in `reports/06-asvs-l2.md` has a Pass / Fail /
      N-A status for every L2 requirement in chapters V2, V3, V4, V5,
      V6, V7, V8, V9, V10, V14. Every Pass cites a file:line. Every
      N-A cites a scope-exclusion reason. Every Fail is also a
      finding in `findings.md`.
- [ ] Business-logic report in `reports/07-business-logic.md`
      documents type-purity (Decimal vs float), concurrency/TOCTOU,
      transfer invariants (each with enforcing source lines quoted),
      rounding, negative-amount handling, and idempotency. "Enforced
      by convention" appears nowhere without being flagged as a
      High finding.
- [ ] Migration report in `reports/08-migrations-schema.md` lists
      every Alembic migration with Pass/Fail for downgrade reversal
      and destructive-op review. Live schema drift has been diffed
      against models and any drift is recorded.
- [ ] IDOR probe `scans/idor-probe.json` exists, was executed against
      the dev compose only (not prod), and every cross-user request
      returned the expected 404 (or the finding is recorded). A
      rerunnable probe script exists at `scripts/audit/idor_probe.py`.
- [ ] `findings.md` Scan Inventory section accounts for every file in
      `scans/` and every report under `reports/`, proving each scan
      and each report was actually consumed.
- [ ] Discrepancies between `pip-audit` and `trivy sbom` are
      explicitly reconciled (either both are right and the finding is
      real, or one is a false positive with reasoning).
- [ ] Red-team appendix exists in `findings.md` (per Section 1P).
      Every challenged finding is either re-rated or defended in
      writing by the developer.
- [ ] `findings.md` has an "Accepted Risks" section that is either
      non-empty OR explicitly states "no findings accepted -- all
      triaged."
- [ ] Constant-time comparison checks from 1C.1 have been completed
      and every auth-related comparison (password, TOTP, backup code)
      is accounted for.
- [ ] Fernet TOTP-key rotation story is documented in `findings.md`
      (either "rotation supported via X, evidence: Y" or "no rotation
      story -- finding F-NNN").

Phase 2 is complete when `remediation-plan.md` exists with a prioritized,
triaged entry for every finding -- including a rationale for any finding
moved to "Accept," and an explicit dependency graph between fixes that
touch the same files.

Phase 3 is complete when ALL of these hold:

- [ ] `findings.md` shows every non-accepted finding as Fixed with a
      commit SHA and PR number.
- [ ] The latest SAST scans on `dev` no longer match the previously-
      open finding patterns (bandit/semgrep/pip-audit/trivy rerun
      outputs are committed under `docs/audits/.../scans-final/`).
- [ ] The DAST IDOR probe has been re-run against the final dev
      compose with zero failures.
- [ ] The full `pytest` suite has been re-run split by directory with
      zero failures, and the pass counts are recorded in the audit
      branch.
- [ ] `pylint app/ --fail-on=E,F` passes with no new warnings
      compared to the pre-audit baseline.
- [ ] A regression test exists in `tests/test_adversarial/` for every
      Critical or High finding.
- [ ] Schema drift check (1N) has been re-run and live DB matches
      models.
