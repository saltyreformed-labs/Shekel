# O7's fence census (balance:R-BAL263) and the record of the fences ruled KEEP (R-BAL268)

A LIVE record. `docs/plans/steps.md` names it as outcome O7's record. No step deletes a fence on its
KEEP list unless its row names the step that may (`balance:R-BAL268`): a step that would delete one
asks the developer first.

## How the census was taken

Two read-only census halves were taken on 2026-10-11 against `dev` at `1e1c7003a`: one over the
tooling and tests (checkers, hooks, CI, test-architecture and source-scanning tests, deploy and
test-infrastructure tests), one over the runtime (`app/`, the migrations, the deploy scripts and
`scripts/`), with the held branch `feat/balance-x-bi-6-4d` at `e5e46fd6e` read wherever a fence's
subject could move. Two neutral verifiers re-read each half adversarially, and one synthesis
reconciled the four, the verifiers winning where they disagreed. Ids are the census's own: `T-` for
tooling and `A-` for runtime; every `file:line` is as committed at `1e1c7003a`, and a group's count
was measured there. Of 240 fences, 83 were classed KEEP, 60 are deleted by existing open steps, 59
needed the steps the census minted, 18 re-ask a constraint PostgreSQL enforces, 10 are obsolete or
duplicate, 7 are kept by rulings and 3 are replaced rather than deleted. The census's working tables
are the coordinator's and are not in the repository; this file is the record.

## The KEEP list (83)

A fence is KEEP when it is a genuine boundary rather than scaffolding around a defect. The kinds and
their counts are the synthesis's, as the developer confirmed them; where the synthesis named no kind
for a fence, the kind is this record's reading.

### Lint, CI and hook wiring, linter configs, checker unit tests (22)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-09 | `.pylintrc:26` and the `--fail-on` lists in CI, pre-commit and the per-edit hook | loads the shekel checkers and hard-fails their messages | wiring; each message leaves with its checker |
| T-11 | `tools/pylint/tests/test_shekel_checkers.py` | the checkers' own behaviour | a checker's tests leave with it |
| T-12 | `scripts/hooks/post-edit-python.sh` | E, F and the shekel messages on every edit | lint wiring |
| T-19 | `scripts/hooks/stop-check.sh`, `scripts/hooks/ENFORCE_PYLINT_FLOOR` | the 10.00 floor and cross-file duplicate code at turn end | lint wiring; the only local catch for cross-file duplicates |
| T-20 | `scripts/hooks/stop-check.sh` (informational greps) | TODO markers, new disables, `except Exception` | informational; it exits 0 |
| T-23 | `.claude/settings.json` (hooks block) | which hook runs when | wiring |
| T-24 | `.pre-commit-config.yaml` (the pylint, duplicate-code, manual-harness and checker-test ids) | the pylint floors and checker tests at commit | lint wiring |
| T-27 | `.pre-commit-config.yaml` (`pylint-quill`, `quill-tests`, `pylint-ci`, `ci-tests`) | the floor and tests of `tools/quill` and `tools/ci` | lint wiring |
| T-31 | `.github/workflows/ci.yml` (lint, six test shards, the verdict, compose `config`) | the merge gate | CI wiring |
| T-32 | `.github/workflows/ci.yml:590` (audit-trigger benchmarks) | nothing; it measures trigger overhead | not a fence |
| T-36 | `tests/test_arch/test_an_inline_style_is_refused_by_a_gate.py:96` | the inline-style lint rule going missing or silent | a test of a linter rule |
| T-50 | `tests/test_arch/test_the_shards_partition_the_suite.py:115` | a shard split that drops or doubles a test | CI infrastructure |
| T-54 | `tools/ci/ci_scope.py:151` (`EXEMPT_READERS`) | a code change classed as registry-only; it fails closed | CI economics, built to outlive the plan gate |
| T-55 | `tools/ci/ci_verdict.py` | a green verdict over a skipped or failed grader | a correctness gate on CI itself |
| T-57 | `tools/ci/test_bottom_layer.py` | a non-stdlib or upward import in `tools/ci` (`balance:R-BAL223`) | layering of the tooling |
| T-58 | `tools/ci/test_ci_workflow.py` | CI wiring that reads a red grader as green | a test of CI's wiring |
| T-102 | `.pre-commit-config.yaml` and CI's polyglot lint (shellcheck, yamllint, biome, gitleaks, typos, rumdl and the rest) | general lint | general linters |
| T-103 | `.pylintrc` (module-line ceiling, `useless-suppression` at :59) | an oversized module, a disable that suppresses nothing | stock configuration that applies design pressure |
| T-217 | `pytest.ini` (`timeout`, the worker and grouping options) | a hung test | a resource guard |
| T-218 | `.github/dependabot.yml` | stale dependencies | supply-chain configuration |
| T-219 | `.gitleaks.toml`, `.djlintrc`, `.hadolint.yaml`, `_typos.toml`, `.rumdl.toml`, `biome.jsonc`, `.yamllint`, `.shellcheckrc` | exemptions from stock rules | general linter configuration |
| T-221 | `scripts/check_template_syntax.py` | a template that does not parse | a general linter |

### Process and approval controls (9)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-04 | `tools/pylint/shekel_checkers/disable_rationale.py:54` (W9903) | a `pylint: disable` with no written reason | a process rule; no app state stands behind it |
| T-18 | `scripts/hooks/post-edit-deps.sh` | a `requirements*.txt` edit nobody approved (it asks) | a developer-approval rule |
| T-21 | `scripts/hooks/session-start.sh` | nothing; it prints the session's context | process |
| T-22 | `scripts/hooks/guard-pr-actions.sh` | a pull request opened or merged outside the coordinator (it asks) | an advisory process control |
| T-56 | `tools/ci/commit_trailers.py`, `tools/ci/trailers.py` | a malformed `Ships:` trailer, a revert git would answer wrongly | git is the truth of what shipped (`balance:R-BAL170`) |
| T-97 | `tests/test_models/test_books_boundary_arms.py:387`, `:499` | a revision that rewrites frozen function bodies | an applied migration is immutable history |
| T-213 | `tests/test_models/test_posting_ref_seed_parity.py:388` | an enum member no migration seeds | migrations are frozen history; `X-dl` may retire its migration leg |
| T-215 | five revision tests asserting `Review:`, e.g. `tests/test_models/test_pay_entries_migration.py:616` | a destructive migration with no recorded review | a developer-approval rule |
| T-220 | `tools/quill/check.py` | a tracker card that breaks the planning rules | the planning process, `X-cx`'s home for the plan gate's owner and cap arms |

### Security boundaries (14)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-15 | `scripts/hooks/post-edit-template.sh:61` | the `safe` filter on user data | cross-site scripting; autoescape is the boundary and this is its review prompt |
| T-17 | `scripts/hooks/post-edit-template.sh:94` | an inline `style=` the content security policy drops | an external constraint no template type spans |
| T-33 | `.github/workflows/docker-publish.yml` (build, cosign signature, digest) | an unsigned or unpinned image | supply chain |
| T-46 | `tests/test_arch/test_the_login_gate_has_one_home.py:94` | a second home of the login decision (`bank_import:R-BI4`) | authentication, over a third-party name Python cannot hide |
| T-62b | `.djlint_rules.yaml` (SHK01) | an inline `style=` attribute | the content security policy |
| T-99 | `tests/test_integration/test_security_headers.py:234` | an inline `on<event>=` handler the policy blocks | the content security policy |
| T-100 | `tests/test_adversarial/test_cache_control.py:104` | a vendored file whose hash differs from `VERSIONS.txt` | supply chain |
| T-101 | `tests/test_routes/test_auth_required.py:224`, `:277` | a route served without login | authentication |
| T-200 | `tests/test_deploy/` (eight files, e.g. `test_container_hardening.py`, `test_proxy_trust_and_headers.py`) | the hardened deploy posture regressing | YAML, nginx, Dockerfile and shell that no type system spans |
| T-205 | `scripts/test.sh:180-216`, `tests/test_scripts/test_test_runner/test_daemon.py:18` | a test container spawned on the production daemon | the production-safety boundary; `X-bt` makes it one producer |
| T-227 | `tests/test_scripts/test_seed_redaction.py:68` | an email address printed by `seed_user.py` | personal data in logs |
| A-57 | `app/login_gate.py:84` (`PUBLIC_ENDPOINTS`) | a route opened without being named | a fail-closed default |
| A-58 | group of 66 ownership calls, e.g. `app/utils/auth_helpers.py:85`, `app/services/transfer_service/_ownership.py:36` | another owner's row, a 404 either way | authorization; `C13-c` and `X-bc` make some cross-owner states unstorable, and the request check stays |
| A-59 | group of 10: `CSRFProtect` (`app/extensions.py`), eight `limiter.limit` sites, the last-activity stamp (`app/__init__.py:743`) | a forged post, brute force, an idle session | the request boundary |

### Import-layering rules over names Python cannot hide (4)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-08 | `tools/pylint/shekel_checkers/package_privacy.py:223` (W9910) | a private module reached across a package boundary | Python has no package privacy, and every one-home design here uses this one |
| T-41 | `tests/test_arch/test_services_no_flask.py:198` | a `flask` import under `app/services/` | a third-party import Python cannot make unwritable |
| T-47 | `tests/test_arch/test_the_migration_runner_has_one_home.py:90` | Alembic's command API used outside `app/migration_runner.py` (`balance:R-BAL114`) | a third-party API Python cannot hide |
| T-71 | `tests/test_services/test_loan_resolver.py:688`, `:1616`, `tests/test_services/test_loan_ledger.py:292` | a database session inside a pure loan producer | a purity rule; its flask half duplicates T-41 and goes at `X-dq` |

### Tests that a structure reached everywhere, or a contract across languages (8)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-35 | `tests/test_arch/test_a_control_rendered_invisible_has_a_rule_that_shows_it.py:119` | a hidden control no stylesheet or script reveals | a Jinja, CSS and JS contract no type spans |
| T-38 | `tests/test_arch/test_a_request_is_a_query_or_a_command.py:138` | a page load that is not read-only, a command at the wrong isolation (`balance:R-EN`) | it grades the mechanism in `app/db_transaction.py` |
| T-44 | `tests/test_arch/test_the_confirm_guard_binds_first.py:79` | a destructive act submitted before its confirm dialog exists | a rendered-order lock on a structural fix |
| T-64 | `tests/test_routes/test_templates.py:2968` (its import half) | a render site re-inlining the shared recurrence macro | a one-spelling lock with no structural form |
| T-98 | `tests/test_routes/test_recurrence_picker.py:415` (its property half) | a script reading a property the server stopped sending | a Python and JS contract |
| T-204 | `tests/test_deploy/test_proxy_trust_and_headers.py:310` and five more sites | trusted-proxy ranges disagreeing across nginx, compose and gunicorn | one network fact in three formats |
| T-222 | `tests/test_routes/test_url_id_converter.py:53` | an integer route rule that escaped the row-id converter | it grades that the converter reached every rule |
| T-223 | `tests/test_routes/test_no_baseline_policy.py:787`, `:944` | a page that fails for an owner with no baseline scenario | a state the rulings keep legal (`recurrence:R-R30`) |

### Regression locks of a removed spelling that stays writable (3)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-82 | `tests/test_routes/test_grid.py:10148` | a hardcoded six-month span returning to the grid template | the spelling stays writable and has no defect class |
| T-84 | `tests/test_routes/test_grid.py:7747`, `:7776` | the reverted panel-swap feature returning | a dead-code lock, not a defect class |
| T-89 | `tests/test_services/test_savings_dashboard_service/test_dti.py:400` (its second guard) | a bare 26-over-12 pay-count literal returning | a removed constant stays writable; the first guard is obsolete and goes at `X-dq` |

### External inputs (4)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-28 | `.github/workflows/ci.yml:338`, `docker-publish.yml:95-135`, `tax-law.yml:63` | a release with next year's tax law not yet transcribed | the IRS, SSA and North Carolina publish it; its absence is not ours to prevent |
| A-60 | group of 44 schema validators in `app/schemas/`, e.g. `validation/transfers.py:110`, `validation/salary.py:679` | bad form input | external input |
| A-61 | group of 8 refusals in `app/services/statement_import/`, e.g. `_integrity.py:68`, `_secu_csv.py:379` | a bank file that contradicts itself or another | external input |
| A-67 | group of 3: `app/__init__.py:263` (`_require_pinned_locale`), `app/__init__.py:940` (`_ALLOWED_SCHEMAS`), `app/config.py` (`_reject_sentinel`) | an unpinned locale, a stray schema, a placeholder secret | the process environment |

### Database constraints, triggers and ORM guards that are the rule, and the seam they protect (6)

| id | where | what it guards | why it stays |
|---|---|---|---|
| A-02 | `app/services/transfer_service/_validation.py:397` (`assert_restorable`, check 3) | restoring a transfer onto an archived account | a domain rule; its three shadow checks go at `X-bi-6-4d` |
| A-05 | `app/services/transfer_service/_side_days.py:131` (`repair_fallback`) | a transfer side with no day of its own borrows one | a derivation, not a fence; only its name still says repair (`BAL-640`) |
| A-64 | group: 164 of the 171 `ck_*` constraints in `app/models`, e.g. `ck_transfers_positive_amount`, `ck_transactions_cleared_needs_settle_day` | the domain rule itself | the constraint is the rule |
| A-65 | group of 8 trigger families, e.g. `app/append_only_infrastructure.py:210`, `app/posting_infrastructure.py:97` | audit, append-only, deleted rows, levels, openings, pay stubs, sightings, a balanced journal | the trigger is the rule |
| A-68 | group of 9: `app/models/mixins.py:532`, `app/models/_derived_flag.py:14` and seven append-only ORM guards (e.g. `app/models/journal_entry.py:413`) | a wrong type, a write to an immutable record | the ORM guard is the rule |
| A-69 | `app/services/status_seam/_seam.py:327` (`apply_status_change`) | the one status writer | it is the structural target; the W9907 fence around it goes at `X-aj2` |

### Domain refusals with words, and stale-page refusals (2 groups)

| id | where | what it guards | why it stays |
|---|---|---|---|
| A-62 | group of about 120 `reject_*` and `_refuse_*` in `app/`, e.g. `app/services/entry_service/_refusals.py`, `app/services/status_seam/_refusals.py` | an act the domain forbids, refused with words | a domain refusal; `books_hold`'s two spellings inside it are a finding, not a KEEP |
| A-63 | group: 25 `version_id_col` lines in `app/models` and the stale-page responses, e.g. `app/routes/transactions/_helpers.py:419`, `app/services/transaction_service/_delete.py:290` | a write made from an out-of-date page | concurrency |

### Fail-closed deploy guards (2)

| id | where | what it guards | why it stays |
|---|---|---|---|
| A-56 | `app/audit_infrastructure.py:171` (`require_audit_triggers`) | a deploy where an audited table lacks its trigger | fail closed; it counts, and `X-ej` makes it compare the set (`BAL-635`) |
| A-66 | group of 9 guards, e.g. `deploy/shekel-deploy.sh:336` (`preflight_migrations`), `:556` (`refuse_to_repin`) | an unsafe deploy | fail closed |

### Test-infrastructure contracts (4)

| id | where | what it guards | why it stays |
|---|---|---|---|
| T-39 | `tests/test_arch/test_a_seeded_start_state_is_built_once.py:141` | a shared seed leaking writes between tests | a test-infrastructure contract |
| T-96 | `tests/test_integration/test_posting_ledger_loan_reconciliation.py:1957` | the parallel-run oracle reading the code it grades | oracle independence; `G1` rewrites it when the allowlist it parses goes |
| T-209 | `scripts/build_test_db_image.py:248` (`cache_key`), `:130`; `tests/test_scripts/test_build_test_db_image.py:102` | a stale baked test template | the key is derived and the image verified |
| T-226 | `scripts/_template_checks.py` (on the held branch `e5e46fd6e` only) | a template missing a trigger family or a count | each count is read from its producer's constant |

### Integrity hygiene reports with no invariant behind them (5)

| id | where | what it guards | why it stays |
|---|---|---|---|
| A-215 | `scripts/_integrity_orphan.py:39` (OR-01) | a definition with no rule and no rows | hygiene; an empty definition is legal |
| A-217 | `scripts/_integrity_orphan.py:73` (OR-04) | a pay period holding no rows | hygiene, and noisy: an empty period is ordinary |
| A-218 | `scripts/_integrity_orphan.py:81` (OR-05) | an active transfer definition with no rows | hygiene |
| A-221 | `scripts/_integrity_balance.py:202` (BA-05) | an anchor jump over half the balance | a heuristic |
| A-234 | `scripts/_integrity_core.py:91` (`TRANSFER_LEG_RECORDS_JOIN`) | the raw-SQL join DC-11 and BA-06 read | the sweep's own SQL; a view both sides read is the alternative |

## The fences kept by a ruling (7)

Each stands because a developer ruling put it there, and a question step puts the reopening to the
developer with worked numbers (`balance:R-BAL267`); the step that builds on a reopened ruling waits
for the answer. On a yes the deletion is filed as its own step; on a no the fence moves to the KEEP
list above with that ruling as its reason. The posted-ledger family is ruled by `R-DM`, `R-DU`,
`R-DV`, `R-BAL98`, `R-BAL102` and `R-BAL122`, against rule 14's own (`R-JA`, `R-IY`).

| id | where | what it guards | ruled by | question |
|---|---|---|---|---|
| A-18 | `scripts/init_database.py:313` (`resync_all_cash_postings_after_migration`) | re-books every settled source's journal entries at each deploy | the posted-ledger family | `balance:X-ek` |
| A-19 | `scripts/init_database.py:378` (`backfill_loan_payment_postings_after_migration`) | re-reconciles every loan's corrections at each deploy | the posted-ledger family | `balance:X-ek` |
| A-22 | `app/services/loan_posting_service/_sync.py:300` (`_assert_checked_projection`) | the posted loan ledger equals the loan's fold, date by date | the posted-ledger family | `balance:X-ek` |
| A-23 | `scripts/_integrity_consistency.py:232` (DC-10) | an undated movement holding a live journal leg | the posted-ledger family | `balance:X-ek` |
| A-25 | `app/services/pay_period_gates.py:424` (`reject_moved_ledger`) | removing earlier paychecks when a booked total would move | `pay_calendar:R-PC53`, `R-PC114` | `pay_calendar:C25` |
| A-38 | `app/exceptions.py:129` (`ForeignAccountError`) | a balance read handed another owner's account | `R-GV` | `balance:X-el` |
| A-41 | `app/services/loan_recurrence_sync.py:580` (`sync_loan_payment_start`) | a loan rule's start date re-copied from the loan | `recurrence:R-R29` | `recurrence:R31` |

DC-09 (A-46, `scripts/_integrity_consistency.py:196`) is not kept by a ruling: `pay_calendar:C13-c`
deletes it (`pay_calendar:R-PC48`), and `pay_calendar:C26` first asks whether that migration also
keys `salary.paycheck_lines`, the table DC-09 reads, or DC-09 stays.

## The fences a planned step replaces rather than deletes

- T-40, the per-render read-pass count, by `pay_calendar:C11`'s layer predicate; deleting it instead
  takes a route-only `BalanceContext` factory.
- T-51's owner and cap arms, by `X-cx`'s `tools/quill/check.py` and the tracker Action: the rule set
  moves home.
- T-53, the shipped-against-git arm, by `tools/ci/trailers.py` and `plan sync` (`balance:R-BAL184`),
  a stored card state kept by a reconciler.
- T-06, W9908's list, possibly by W9910 package privacy, as `G1`'s trace decides.
- T-01, T-02, T-03, T-13 and T-74, static checkers, by run-time refusals (`FloatOperation`, `Money`,
  a raising `DisplayLabel.__eq__`), so their coverage becomes the code that runs.
- T-29, T-30 and T-80, three detectors, by `pay_calendar:C10`'s process-clock checker extended to
  `tests/` (`pay_calendar:C24`, `X-dx`); that checker is itself KEEP-class.
- A-21's remainder and A-22, the per-door posting field filters and the E1a assert, by `X-ai-b`'s
  commit-boundary grader and `X-d`'s per-visible-date assert; `X-ek` decides.
- T-91's second list, graded by an arm at `X-dh` rather than deleted; `X-dz` deletes it.
- T-216, the code-reviewer agent's mirror of the rules, rewritten and kept by `X-cx`'s last card and
  `X-bi-6-4d-2`.
- A-39 and A-16's `$0.00` arm, the reconcile panel's and the loan walk's, by `X-db`'s one refusal at
  the status seam.
- T-60's two producers of whether a docker daemon is safe, by `X-bt`'s one predicate.
