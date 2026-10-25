> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Python Package Update Plan

**Date:** 2026-04-11
**Python version:** 3.14.3 (Dockerfile)
**Total packages:** 15

---

## Phase 1: Package Inventory

| Package | Current | Latest | Bump | CVEs | Notes |
|---------|---------|--------|------|------|-------|
| Flask | 3.1.3 | 3.1.3 | -- | No | Already current |
| Flask-Limiter | 4.1.1 | 4.1.1 | -- | No | Already current |
| Flask-Login | 0.6.3 | 0.6.3 | -- | No | Already current |
| Flask-SQLAlchemy | 3.1.1 | 3.1.1 | -- | No | Already current |
| Flask-Migrate | 4.1.0 | 4.1.0 | -- | No | Already current |
| Flask-WTF | 1.2.2 | 1.2.2 | -- | No | Already current |
| SQLAlchemy | 2.0.48 | 2.0.49 | Patch | No | Bug fixes only |
| psycopg2 | 2.9.11 | 2.9.11 | -- | No | Already current |
| alembic | 1.18.4 | 1.18.4 | -- | No | Already current |
| bcrypt | 5.0.0 | 5.0.0 | -- | No | Already current |
| pyotp | 2.9.0 | 2.9.0 | -- | No | Already current |
| qrcode | 8.2 | 8.2 | -- | No | Already current |
| cryptography | 46.0.5 | 46.0.7 | Patch | **Yes** | CVE-2026-34073, CVE-2026-39892 |
| marshmallow | 3.26.2 | 4.3.0 | **Major** | No | See breaking changes analysis below |
| python-dotenv | 1.2.2 | 1.2.2 | -- | No | Already current |
| python-json-logger | 4.0.0 | 4.1.0 | Minor | No | Adds Python 3.14 support |

**Summary:** 11 packages already current. 4 packages need updates (1 security-critical, 1 patch,
1 minor, 1 major).

---

## Phase 2: Dependency Analysis

### Dependency Clusters

**Cluster A -- Database ORM:** SQLAlchemy, alembic, Flask-SQLAlchemy, Flask-Migrate

- SQLAlchemy 2.0.49 is a patch release. alembic 1.18.4 already supports it.
- Flask-SQLAlchemy 3.1.1 requires `sqlalchemy>=2.0.1`. No constraint conflict.
- Flask-Migrate 4.1.0 requires `alembic>=1.9.0`. No constraint conflict.
- **These can be updated independently** -- only SQLAlchemy needs a bump and the others are
  already compatible.

**Cluster B -- Authentication:** bcrypt, pyotp, qrcode, cryptography

- cryptography 46.0.7 is a patch release. No upstream or downstream constraints affected.
- bcrypt 5.0.0 does not depend on cryptography directly.
- **cryptography can be updated independently.**

**Cluster C -- Marshmallow (standalone)**

- Shekel uses marshmallow directly -- no flask-marshmallow or marshmallow-sqlalchemy in
  requirements. No ecosystem packages constrain the version.
- **marshmallow can be updated independently.**

### Python 3.14 Compatibility

- python-json-logger 4.1.0 adds official Python 3.14 support (4.0.0 works but is not
  officially tested).
- marshmallow 4.x supports Python 3.10+, tested through 3.14.
- All other packages already work on Python 3.14.3 (confirmed by current production deployment).

---

## Phase 3: Update Sequencing

### Step 1 -- SECURITY: cryptography 46.0.5 -> 46.0.7

**Priority:** Immediate. Two CVEs patched.

**CVE-2026-34073 (fixed in 46.0.6):** Name constraints were not properly applied to peer names
during X.509 certificate verification when leaf certificates contained wildcard DNS Subject
Alternative Names. Does not affect standard Web PKI topologies but is a correctness issue in
certificate validation.

**CVE-2026-39892 (fixed in 46.0.7):** Non-contiguous buffers could be passed to APIs accepting
Python buffers, potentially causing a buffer overflow. OpenSSL updated to 3.5.6 in wheels.

**Change in requirements.txt:**

```
# Line 28: change
cryptography==46.0.5
# to
cryptography==46.0.7
```

**Code changes required:** None. Both are patch-level security fixes with no API changes.

**Verification:**

```bash
pip install -r requirements.txt
pytest tests/test_services/ -v --tb=short
pytest tests/test_routes/ -v --tb=short
pylint app/ --fail-on=E,F
```

**Rollback:** Revert line 28 in requirements.txt to `cryptography==46.0.5`, run
`pip install -r requirements.txt`, re-run tests.

**Commit:** `chore(deps): update cryptography from 46.0.5 to 46.0.7 (CVE-2026-34073, CVE-2026-39892)`

---

### Step 2 -- PATCH: SQLAlchemy 2.0.48 -> 2.0.49 + cryptography (batch)

**Risk:** Low. Patch-level bug fixes only.

Since cryptography was already updated in Step 1, this step covers only SQLAlchemy. However, if
Steps 1 and 2 are done in the same session, they can be combined into a single batch commit.

**Change in requirements.txt:**

```
# Line 19: change
SQLAlchemy==2.0.48
# to
SQLAlchemy==2.0.49
```

**Code changes required:** None.

**Verification:**

```bash
pip install -r requirements.txt
pytest tests/test_services/ -v --tb=short
pytest tests/test_routes/ -v --tb=short
pylint app/ --fail-on=E,F
```

**Rollback:** Revert line 19 in requirements.txt to `SQLAlchemy==2.0.48`, run
`pip install -r requirements.txt`, re-run tests.

**Commit:** `chore(deps): update SQLAlchemy from 2.0.48 to 2.0.49`

---

### Step 3 -- MINOR: python-json-logger 4.0.0 -> 4.1.0

**Risk:** Very low. No API changes. Only adds official Python 3.14 and PyPy 3.11 support, drops
Python 3.8-3.9 support (irrelevant -- project uses 3.14).

**Change in requirements.txt:**

```
# Line 37: change
python-json-logger==4.0.0
# to
python-json-logger==4.1.0
```

**Code changes required:** None. Import paths, class names, and API are identical.

**Verification:**

```bash
pip install -r requirements.txt
pytest tests/test_services/ -v --tb=short
pytest tests/test_routes/ -v --tb=short
pylint app/ --fail-on=E,F
```

**Rollback:** Revert line 37 in requirements.txt to `python-json-logger==4.0.0`, run
`pip install -r requirements.txt`, re-run tests.

**Commit:** `chore(deps): update python-json-logger from 4.0.0 to 4.1.0`

---

### Step 4 -- MAJOR: marshmallow 3.26.2 -> 4.3.0

**Risk:** Medium on paper (major version bump), but **low in practice for this codebase** based on a
full audit of all 45 schemas, 253+ field definitions, 8 custom validators, and 42 ValidationError
raises.

#### Breaking Changes in marshmallow 4.0 -- Shekel Impact Assessment

| Removed/Changed Feature | Used in Shekel? | Impact |
|------------------------|-----------------|--------|
| `default`/`missing` field params | No -- uses `load_default` | None |
| `fields`/`additional` in Meta | No -- only `unknown = EXCLUDE` | None |
| `ordered` in Meta | No | None |
| `Schema.context` attribute | No | None |
| `Field.fail()` method | No -- uses `raise ValidationError()` | None |
| `"self"` in `Nested()` | No -- no Nested fields used | None |
| `json_module` in Meta | No | None |
| `marshmallow.base` module | No | None |
| `marshmallow.utils` functions | No | None |
| Validators returning `False` | No -- all raise `ValidationError` | None |
| Field metadata via kwargs | No | None |
| `_bind_to_schema` param rename | No custom field subclasses | None |
| `pass_many` rename | Not used | None |
| `Schema.loads` param rename | Not used -- all use `.load()` | None |
| `unknown` kwarg added to hooks | All hooks use `**kwargs` | None |
| `@validates` receives `data_key` | Not affected | None |

**Full audit result: zero deprecated features in use.** The codebase already follows marshmallow 4.x
patterns. No code changes are required.

**Change in requirements.txt:**

```
# Line 30: change
marshmallow==3.26.2
# to
marshmallow==4.3.0
```

**Code changes required:** None. See audit table above.

**Verification -- extra thorough for a major bump:**

```bash
pip install -r requirements.txt
# Run schema-heavy route tests first (these exercise all 45 schemas)
pytest tests/test_routes/ -v --tb=short
# Then service tests (exercise validators and load paths)
pytest tests/test_services/ -v --tb=short
# Lint
pylint app/ --fail-on=E,F
```

**Rollback:** Revert line 30 in requirements.txt to `marshmallow==3.26.2`, run
`pip install -r requirements.txt`, re-run tests.

**Commit:** `chore(deps): update marshmallow from 3.26.2 to 4.3.0`

---

## Phase 4: Risk Assessment

### Summary

| Bump Type | Count | Packages |
|-----------|-------|----------|
| Already current | 11 | Flask, Flask-Limiter, Flask-Login, Flask-SQLAlchemy, Flask-Migrate, Flask-WTF, psycopg2, alembic, bcrypt, pyotp, qrcode, python-dotenv |
| Patch | 2 | cryptography, SQLAlchemy |
| Minor | 1 | python-json-logger |
| Major | 1 | marshmallow |
| **Total update-test-commit cycles** | **4** | |

### Risk Rankings (highest to lowest)

1. **marshmallow 3.26.2 -> 4.3.0 (Medium-Low):** Major version bump, but the full codebase
   audit found zero uses of any deprecated or removed features. The main residual risk is
   undocumented behavioral changes in edge cases -- the test suite (3200+ tests) is the
   backstop. If any schema behaves differently, tests will catch it.

2. **cryptography 46.0.5 -> 46.0.7 (Low, but required):** Two CVEs make this non-optional.
   Patch-level with no API changes. Risk is near zero, but it is a compiled C extension, so
   verify the wheel installs cleanly in the Docker build.

3. **SQLAlchemy 2.0.48 -> 2.0.49 (Very Low):** Patch-level ORM bug fixes. The project uses
   standard ORM patterns. No risk.

4. **python-json-logger 4.0.0 -> 4.1.0 (Negligible):** Pure Python version support change.
   Zero API changes.

### Packages Recommended to Stay on Current Version

**None.** All 4 updates are safe to proceed with. The cryptography update is actively recommended
due to CVEs.

### Docker Build Consideration

cryptography and psycopg2 are compiled extensions. After updating cryptography, rebuild the Docker
image to verify the wheel installs cleanly with the `libpq-dev gcc libc-dev` build dependencies in
the builder stage:

```bash
docker build --no-cache -t shekel-test .
```

### Recommended Execution Order

Steps 1-3 (cryptography, SQLAlchemy, python-json-logger) can realistically be combined into a
single commit since they are all zero-risk changes. However, for maximum bisectability per the plan
above, do them separately. The developer can decide.

Step 4 (marshmallow) should always be its own commit, even though the audit shows no code changes
needed. A major version bump deserves a clean revert point.
