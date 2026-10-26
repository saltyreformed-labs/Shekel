"""
Shekel Budget App -- Structured Log Event Definitions

Defines standardized event categories, a registry of every Python-tier
event the application emits, and a helper for emitting structured log
entries with consistent fields.  All log events include the event name
and category as structured ``extra`` fields, making them filterable in
Grafana/Loki.

Three layers of audit coverage cooperate to produce the project's
forensic record (commit C-13, C-14, C-15 of the 2026-04-15 security
remediation plan):

  1. **DB-tier triggers** write to ``system.audit_log`` on every
     INSERT/UPDATE/DELETE against financial/auth tables.  Most
     tamper-resistant -- the runtime app role cannot DROP the trigger.
  2. **Python-tier ``log_event``** (this module) emits structured
     events at every service-layer mutation.  Enables "what happened"
     queries keyed by event name without scanning audit_log row diffs.
  3. **Off-host shipping** flows both layers to S3 / Loki / rsyslog so
     the app container cannot rewrite history.

Event naming convention:  ``<verb>_<noun>`` or ``<noun>_<event>`` in
snake_case.  Every event used in the codebase MUST be registered here
via :func:`_register` so tests can assert completeness and so
operators can introspect the catalogue from the Python REPL.

Audit references: F-080 (Medium), F-085 (Low), F-144 (Low) -- all
remediated by commit C-14.
"""
import logging


# ---------------------------------------------------------------------------
# Event category constants
# ---------------------------------------------------------------------------
#
# Categories partition the event namespace by *intent*.  A single
# category-string travels with every record, so dashboards can filter
# (e.g. "show me every ACCESS event in the last hour") without having
# to maintain a name-to-category lookup table at query time.

#: Authentication and credential lifecycle (login/logout, password
#: change, MFA enrol/verify/disable, lockouts, HIBP rejections).
AUTH = "auth"

#: Business-domain mutations (transfers, transactions, entries, pay
#: periods, recurrence generation, carry-forward).  The dominant
#: category for service-tier events.
BUSINESS = "business"

#: Authorization decisions and IDOR boundary events (cross-user
#: resource access denied, owner-only route blocked, true 404s).
#: Distinguishing ACCESS from AUTH lets SOC tooling alert on probing
#: patterns without false-positives from password failures.
ACCESS = "access"

#: Reserved category for explicit audit-trail markers that do not fit
#: the other categories (e.g. compliance attestations, manual data
#: corrections).  No events emit under AUDIT today; the constant is
#: here so a future feature can add one without bumping the registry's
#: schema version.
AUDIT = "audit"

#: Unhandled-error events emitted alongside an exception trace so the
#: operator can wire alerting on event-name without grepping
#: tracebacks.
ERROR = "error"

#: Per-request lifecycle (slow_request, request_complete) emitted by
#: ``app.utils.logging_config``.  Not a service-tier category.
PERFORMANCE = "performance"


# ---------------------------------------------------------------------------
# Event registry
# ---------------------------------------------------------------------------
#
# Every event the application emits via :func:`log_event` MUST be
# registered here.  The registry serves three purposes:
#
#   1. **Test completeness gate.**  ``tests/test_utils/test_log_events.py``
#      asserts that no two events share a name and that every category
#      string is one of the constants above.  A new event that is
#      emitted but not registered makes the test fail loudly.
#
#   2. **Operator introspection.**  ``python -c "from app.utils.log_events
#      import EVENT_REGISTRY; pprint(EVENT_REGISTRY)"`` lists every
#      Python-tier event the app can emit, the category each falls
#      under, and a one-line description.  Used by the SOC runbook.
#
#   3. **Self-documenting call sites.**  Service modules import the
#      ``EVT_*`` symbol rather than passing a bare string, so a typo
#      surfaces at import time as a NameError rather than at run time
#      as a silent observability gap.

EVENT_REGISTRY: dict[str, dict[str, str]] = {}


def _register(name: str, category: str, description: str) -> str:
    """Register an event in :data:`EVENT_REGISTRY` and return its name.

    Raises a :class:`ValueError` on duplicate registration.  Called at
    module import time for every ``EVT_*`` constant defined below.

    Args:
        name: The structured-log ``event`` field value.  MUST be a
            snake_case string of the form ``<verb>_<noun>`` or
            ``<noun>_<event>``.  No dots, no hyphens.
        category: One of the category constants above.  Validated so
            a typo in the constant name (e.g. ``"buisness"``) cannot
            quietly bypass dashboard filters.
        description: Single-line human-readable summary of WHEN the
            event fires.  Surfaces in operator introspection output.

    Returns:
        The ``name`` argument unchanged so the registration can be
        used as a one-liner module-level constant assignment::

            EVT_FOO = _register("foo", BUSINESS, "...")

    Raises:
        ValueError: If ``name`` is already in the registry, or if
            ``category`` is not a recognised category constant.
    """
    if name in EVENT_REGISTRY:
        # Duplicate registration is always a programming error -- two
        # service modules emitting the same event name would conflate
        # otherwise-distinct actions in dashboards.  Fail loud at
        # import time.
        raise ValueError(
            f"log event {name!r} is already registered; refusing duplicate "
            "registration so dashboards stay unambiguous."
        )
    if category not in (AUTH, BUSINESS, ACCESS, AUDIT, ERROR, PERFORMANCE):
        raise ValueError(
            f"log event {name!r} declares unknown category {category!r}; "
            "use AUTH / BUSINESS / ACCESS / AUDIT / ERROR / PERFORMANCE."
        )
    EVENT_REGISTRY[name] = {
        "category": category,
        "description": description,
    }
    return name


# ── Auth events (existing call sites; constants centralise names) ──

EVT_LOGIN_SUCCESS = _register(
    "login_success", AUTH,
    "User completed primary authentication (no MFA gate or post-MFA).",
)
EVT_LOGIN_FAILED = _register(
    "login_failed", AUTH,
    "Login attempt rejected (bad credentials or active lockout).",
)
EVT_LOGOUT = _register(
    "logout", AUTH,
    "User terminated their session via /logout.",
)
EVT_USER_REGISTERED = _register(
    "user_registered", AUTH,
    "New user account created via /register (F-085 / C-14).",
)
EVT_PASSWORD_CHANGED = _register(
    "password_changed", AUTH,
    "User changed their password via /change-password.",
)
EVT_SESSIONS_INVALIDATED = _register(
    "sessions_invalidated", AUTH,
    "User invoked the global session-invalidation control.",
)
EVT_OTHER_SESSIONS_INVALIDATED = _register(
    "other_sessions_invalidated", AUTH,
    "Background helper invalidated every session except the current one.",
)
EVT_REAUTH_FAILED = _register(
    "reauth_failed", AUTH,
    "Step-up re-authentication failed (bad password or bad TOTP).",
)
EVT_REAUTH_SUCCESS = _register(
    "reauth_success", AUTH,
    "Step-up re-authentication succeeded; fresh-login window restarted.",
)
EVT_MFA_LOGIN_SUCCESS = _register(
    "mfa_login_success", AUTH,
    "User completed login including the MFA factor.",
)
EVT_MFA_ENABLED = _register(
    "mfa_enabled", AUTH,
    "User enrolled an authenticator and confirmed the first TOTP.",
)
EVT_MFA_DISABLED = _register(
    "mfa_disabled", AUTH,
    "User removed their MFA enrolment via /mfa/disable.",
)
EVT_BACKUP_CODES_REGENERATED = _register(
    "backup_codes_regenerated", AUTH,
    "User issued themselves a new set of MFA backup codes.",
)
EVT_TOTP_REPLAY_REJECTED = _register(
    "totp_replay_rejected", AUTH,
    "TOTP code matched a previously-consumed timestep (F-005 / C-09).",
)
EVT_HIBP_CHECK_FAILED = _register(
    "hibp_check_failed", AUTH,
    "HIBP breached-password check failed open (network/HTTP error).",
)
EVT_HIBP_CHECK_REJECTED = _register(
    "hibp_check_rejected", AUTH,
    "HIBP rejected a candidate password as breached.",
)
EVT_ACCOUNT_LOCKED = _register(
    "account_locked", AUTH,
    "Threshold-many consecutive failures triggered an account lockout.",
)


# ── Access / authorization events (F-144 / C-14) ───────────────────
#
# These three close the "silent IDOR" gap noted in F-144: prior to
# C-14, ``require_owner`` and ``get_or_404`` returned 404 without
# emitting any event, so a probing companion or cross-user attacker
# left no application-tier trace.  Now every ownership-failure path
# emits a structured event the SOC can alert on.

EVT_ACCESS_DENIED_OWNER_ONLY = _register(
    "access_denied_owner_only", ACCESS,
    "Companion (or other non-owner) tried to load an owner-only route.",
)
EVT_ACCESS_DENIED_CROSS_USER = _register(
    "access_denied_cross_user", ACCESS,
    "User tried to load a resource that exists but belongs to another user.",
)
EVT_RESOURCE_NOT_FOUND = _register(
    "resource_not_found", ACCESS,
    "Ownership check ran against a primary key that has no row.",
)
EVT_RATE_LIMIT_EXCEEDED = _register(
    "rate_limit_exceeded", ACCESS,
    "Flask-Limiter rejected a request that exceeded its per-IP quota.",
)
EVT_BASELINE_MISSING = _register(
    "baseline_missing", ERROR,
    "A request asked the balance seam for a figure for a user with no "
    "baseline scenario; the setup-recovery page (or 204 for a fragment) was "
    "returned instead of the balance.  No code path produces this state, so "
    "every occurrence is either data changed outside the app or a caller "
    "resolving the wrong user -- alert on it.",
)

EVT_PAY_CALENDAR_UNDERIVABLE = _register(
    "pay_calendar_underivable", ERROR,
    "A request needed a user's pay calendar and it could not be derived; the "
    "recovery page (or 204 for a fragment) was returned instead of a bare "
    "500.  Two states reach it and the message says which: an owner with no "
    "budget.pay_schedule row -- ordinary, and repaired by generating a "
    "schedule -- or a payday set that cannot define a calendar, which is a "
    "broken invariant no write door produces.  Alert on the second.",
)

EVT_SALARY_STATE_UNSUPPORTED = _register(
    "salary_state_unsupported", ERROR,
    "A request priced a salary profile whose state the tax law does not list; "
    "the fix-it page naming the profile (or 204 for a safe-method fragment) "
    "was returned instead of a figure.  Both profile doors refuse such a state, so every "
    "occurrence is a row older than a release that dropped a state or one "
    "written around the doors -- alert on it.",
)


# ── Business events: existing call sites ───────────────────────────

EVT_RECURRENCE_GENERATED = _register(
    "recurrence_generated", BUSINESS,
    "Recurrence engine created transactions for a template.",
)
EVT_CROSS_USER_BLOCKED = _register(
    "cross_user_blocked", BUSINESS,
    "Defense-in-depth check refused a recurrence operation crossing users.",
)
EVT_CARRY_FORWARD = _register(
    "carry_forward", BUSINESS,
    "User carried forward unpaid items from one period to the next.",
)
EVT_LOAN_RECURRENCE_START_DATE_UPDATED = _register(
    "loan_recurrence_start_date_updated", BUSINESS,
    "Loan recurrence rule start date snapped to the first contractual "
    "installment, so no payment generates before the loan originates.",
)


# ── Business events: transfer service ──────────────────────────────

EVT_TRANSFER_CREATED = _register(
    "transfer_created", BUSINESS,
    "Transfer service created a transfer (one row; its sides' payments hang "
    "off it).",
)
EVT_TRANSFER_UPDATED = _register(
    "transfer_updated", BUSINESS,
    "Transfer service updated a transfer; each side's record follows its status.",
)
EVT_TRANSFER_SOFT_DELETED = _register(
    "transfer_soft_deleted", BUSINESS,
    "Transfer service took a transfer's sides' records off the books and "
    "flagged it is_deleted=True.",
)
EVT_TRANSFER_HARD_DELETED = _register(
    "transfer_hard_deleted", BUSINESS,
    "Transfer service took a transfer's sides' records off the books and "
    "hard-deleted it.",
)
EVT_TRANSFER_RESTORED = _register(
    "transfer_restored", BUSINESS,
    "Transfer service restored a soft-deleted transfer.",
)
EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT = _register(
    "transfer_restore_refused_archived_account", BUSINESS,
    "restore_transfer refused to reactivate a transfer whose source or "
    "destination account is archived (is_active = False).  Closes F-164.",
)


# ── Business events: credit (legacy per-transaction) workflow ──────

EVT_CREDIT_MARKED = _register(
    "credit_marked", BUSINESS,
    "Transaction marked Credit; payback expense generated in next period.",
)
EVT_CREDIT_UNMARKED = _register(
    "credit_unmarked", BUSINESS,
    "Credit-marked transaction reverted to Projected; payback deleted.",
)
EVT_PAYBACK_DELETED_WITH_SOURCE = _register(
    "payback_deleted_with_source", BUSINESS,
    "Live CC Payback deleted because its source transaction was deleted.",
)


# ── Business events: per-entry credit workflow ─────────────────────

EVT_ENTRY_PAYBACK_CREATED = _register(
    "entry_payback_created", BUSINESS,
    "Aggregated CC Payback created from credit entries on a transaction.",
)
EVT_ENTRY_PAYBACK_UPDATED = _register(
    "entry_payback_updated", BUSINESS,
    "Aggregated CC Payback amount adjusted as credit entries changed.",
)
EVT_ENTRY_PAYBACK_DELETED = _register(
    "entry_payback_deleted", BUSINESS,
    "Aggregated CC Payback removed when its credit entries went to zero.",
)


# ── Business events: transaction entries ───────────────────────────

EVT_ENTRY_CREATED = _register(
    "entry_created", BUSINESS,
    "Individual purchase entry recorded against an envelope transaction.",
)
EVT_ENTRY_UPDATED = _register(
    "entry_updated", BUSINESS,
    "Individual purchase entry modified (amount, description, date, credit).",
)
EVT_ENTRY_DELETED = _register(
    "entry_deleted", BUSINESS,
    "Individual purchase entry hard-deleted from a transaction.",
)
EVT_ENTRIES_SETTLED_DAY_RECORDED = _register(
    "entries_settled_day_recorded", BUSINESS,
    "User confirmed which outstanding purchases their bank statement shows, "
    "stamping each one's settled_on with the day the balance was observed.",
)
EVT_TRANSACTIONS_RECONCILED = _register(
    "transactions_reconciled", BUSINESS,
    "User confirmed which outstanding transactions their bank statement "
    "shows; each settled through the transaction service on the day the "
    "balance was observed, some carrying a corrected amount.  Its "
    "requested_count is every ROW tick of the submission, a field "
    "settlements_reconciled reads too since credit_card:CC-5-4b (ruling "
    "R-CC116), so a settled count below it is not by itself a stale tick.",
)
EVT_TRANSFERS_RECONCILED = _register(
    "transfers_reconciled", BUSINESS,
    "User confirmed which outstanding TRANSFERS their bank statement shows; "
    "each settled through the transfer service on the day the balance was "
    "observed, the transfer and both its sides' records together, some carrying a "
    "corrected amount.  Its own event rather than a count folded into the "
    "transaction one, because settling a transfer touches a SECOND account: "
    "an analyst asking why that account's balance moved has to be able to "
    "find this without knowing to look under transactions.",
)
EVT_SETTLEMENTS_RECONCILED = _register(
    "settlements_reconciled", BUSINESS,
    "User confirmed which reopened payments recorded on this account, for "
    "rows planned on ANOTHER account, their bank statement shows (the "
    "reconcile panel's 'Paid from this account' list, plan step "
    "credit_card:CC-5-4b); each row settled through the transaction service "
    "on the day the balance was observed, its payment dated there (a typed "
    "$0.00 withdraws it), some carrying a corrected amount.  Its own event "
    "because the row that settles is planned on a SECOND account, so these "
    "settles are counted apart from this account's own bills.  Its "
    "requested_count is every ROW tick of the submission, the field it "
    "shares with transactions_reconciled (ruling R-CC116).",
)

# ── Business events: pay periods ───────────────────────────────────

EVT_PAY_PERIODS_GENERATED = _register(
    "pay_periods_generated", BUSINESS,
    # The cadence rides on the event as its kind's phrase -- "every 14
    # days", "monthly on day 15", "twice a month on days 1 and 15" -- since
    # plan step pay_calendar:C17-d-2 (ruling R-PC82); it said "biweekly
    # periods" until C17-d-3 (ledger row PC-514).
    "Pay-period service created one or more new pay periods on the owner's "
    "stated rhythm.",
)


# ── Business events: the salary profile lifecycle ──────────────────

EVT_SALARY_ROWS_FROZEN = _register(
    "salary_rows_frozen", BUSINESS,
    "Archiving a salary profile recorded what each row it priced was last "
    "worth, because the producer behind amount rule 2 is going away "
    "(finding N-261, plan step balance:X-au-d).",
)


# ── Business events: recurrence engines (regenerate / resolve) ─────

EVT_RECURRENCE_REGENERATED = _register(
    "recurrence_regenerated", BUSINESS,
    "Recurrence engine deleted auto-generated rows and re-emitted them.",
)
EVT_RECURRENCE_CONFLICTS_RESOLVED = _register(
    "recurrence_conflicts_resolved", BUSINESS,
    "User resolved override/delete conflicts after a regeneration.",
)
EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED = _register(
    "resolve_conflicts_shadow_refused", BUSINESS,
    "recurrence_engine.resolve_conflicts refused to mutate a transfer "
    "shadow transaction (transfer_id IS NOT NULL).  Closes F-007.",
)
EVT_TRANSFER_RECURRENCE_GENERATED = _register(
    "transfer_recurrence_generated", BUSINESS,
    "Transfer recurrence engine generated transfers from a template.",
)
EVT_TRANSFER_RECURRENCE_REGENERATED = _register(
    "transfer_recurrence_regenerated", BUSINESS,
    "Transfer recurrence engine deleted and regenerated auto transfers.",
)
EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED = _register(
    "transfer_recurrence_conflicts_resolved", BUSINESS,
    "User resolved override/delete conflicts after a transfer regeneration.",
)


# ── Business events: transaction service helpers ───────────────────

EVT_TRANSACTION_SETTLED_FROM_ENTRIES = _register(
    "transaction_settled_from_entries", BUSINESS,
    "Envelope transaction settled at sum(entries) with status -> Done/Received.",
)


# ── Business events: statement import (bank_import:X-f6a) ──────────

EVT_STATEMENT_IMPORTED = _register(
    "statement_imported", BUSINESS,
    "A bank statement was parsed, verified and recorded against an account.",
)

EVT_STATEMENT_IDENTITY_RECORDED = _register(
    "statement_identity_recorded", BUSINESS,
    "A first import recorded what a source calls one of the user's accounts.",
)

EVT_STATEMENT_MATCHED = _register(
    "statement_matched", BUSINESS,
    "An owner accepted that bank lines and app rows are one movement, and the "
    "app's recorded day moved to the bank's.",
)

EVT_STATEMENT_MATCH_RELEASED = _register(
    "statement_match_released", BUSINESS,
    "An owner undid a match; the bank lines it explained are unexplained "
    "again.  It MOVES MONEY where the act had CREATED a row -- a group's "
    "recorded difference states nothing once the grouping is released, so "
    "the release removes it and reverses its postings (plan step "
    "bank_import:X-f6d-4).  ``removed_count`` is how many such rows went.",
)

EVT_STATEMENT_MATCH_WITHDRAWN = _register(
    "statement_match_withdrawn", BUSINESS,
    "A movement the accepted matches named left them -- taken off the books "
    "with its row, as a purchase, or as a payment a $0.00 / purchases record "
    "withdrew, or moved onto another account -- so the acts it was the last "
    "app row of were withdrawn and their bank lines are unexplained again; "
    "the message says which (plan steps bank_import:X-gb, "
    "credit_card:CC-5-4a-1, credit_card:CC-5-4a-3).  The SIBLING of "
    "statement_match_released and not the same act: a release is the owner "
    "undoing a decision, and this is the decision losing its subject.  It "
    "moves NO money of its own -- a removal's act reverses the movement's "
    "legs itself, and a moved movement keeps its money -- and rows the "
    "withdrawn acts had CREATED are LEFT standing, which kept_row_count "
    "reports.  transaction_ids names the "
    "rows involved, transfer_ids the transfers whose payments left (since "
    "balance:X-bi-6-4c-4; a transfer's payment was listed under its shadow "
    "row's id in transaction_ids until then), transaction_entry_ids the "
    "movements, freed_line_ids the "
    "lines (transaction_entry_id, singular, until CC-5-4a-3).",
)

EVT_STATEMENT_MATCH_LINELESS = _register(
    "statement_match_lineless", ERROR,
    "An accepted match names NO bank line, which the schema forbids: it "
    "asserts nothing about a bank, so the review screen cannot render it, "
    "while its app rows stay claimed and unmatchable.  Reaching this needs a "
    "code defect -- one writer refuses an empty side, a foreign key refuses to "
    "remove a line a match names, and a migration deleted the acts that "
    "already held none -- so it is logged rather than rendered, and the row "
    "named here is freed by deleting it.",
)

EVT_STATEMENT_LINE_SKIPPED = _register(
    "statement_line_skipped", BUSINESS,
    "An owner decided a bank line is explained by nothing they budget for, so "
    "the Reconcile inbox stops asking about it (ruling bank_import:R-JG, plan "
    "step bank_import:X-gj-4a).  It MOVES NO MONEY and can move none: the "
    "bank's own record still shows the line, the app still records nothing "
    "for it, and the books-versus-bank comparison still reports the "
    "difference.  What it records is a decision about the WORK, which is why "
    "it is a business event rather than a silent filter.",
)

EVT_STATEMENT_LINE_UNSKIPPED = _register(
    "statement_line_unskipped", BUSINESS,
    "An owner undid a skip; that bank line is waiting to be explained again.  "
    "Its own event beside statement_line_skipped for the reason "
    "statement_match_released is one beside statement_matched: undoing a "
    "decision is a different act from making it, and a single event carrying "
    "a direction would make 'how many lines did they skip' unanswerable "
    "without reading the payload.",
)

EVT_STATEMENT_IMPORT_DELETED = _register(
    "statement_import_deleted", BUSINESS,
    "An owner deleted a recorded import: the lines it FIRST recorded are gone, "
    "every match naming one of them was released, and the source-account "
    "pairing went with it if that was the account's last import from that "
    "source.  It MOVES MONEY where the review had CREATED a row from one of "
    "those lines (ruling R-GG): the row goes with the line, and rows_removed "
    "and cash_removed say how many and how much.  A settle day an accepted "
    "match wrote is the app's own record and stays.  It destroys what the "
    "BANK said, which is the fact this whole arc exists to hold; "
    "anchors_released counts the other imports whose placed balance rested "
    "on those lines and was released with them.",
)

EVT_STATEMENT_LINE_RECORDED = _register(
    "statement_line_recorded", BUSINESS,
    "A bank line the app had no row for became a purchase against a budget "
    "line, which MOVES MONEY: the app now records a movement it did not have.",
)

EVT_STATEMENT_INCOME_RECORDED = _register(
    "statement_income_recorded", BUSINESS,
    "A bank line of money COMING IN that no app row explained became an "
    "uncategorized income row, which MOVES MONEY: the app now records a "
    "deposit it did not have (ruling bank_import:R-GW).  Its own event beside "
    "``statement_line_recorded`` because the two doors record opposite "
    "DIRECTIONS of movement into different shapes -- a purchase against a "
    "container that reserves for it, and a top-level row that reserves "
    "nothing -- so one event covering both would name neither.",
)

EVT_STATEMENT_RESIDUAL_RECORDED = _register(
    "statement_residual_recorded", BUSINESS,
    "A matched GROUP's difference was recorded as an ordinary uncategorized "
    "row, which MOVES MONEY: the app now records a movement the bank showed "
    "and no row of the owner's accounted for (ruling R-FN).  Its own event "
    "beside the match that produced it, because it is a different act with a "
    "different consequence -- a match re-dates and re-prices rows that already "
    "existed, and this CREATES one, in the per-owner Uncategorized bucket that "
    "nothing else in the app has ever written.",
)

EVT_STATEMENT_BATCH_APPLIED = _register(
    "statement_batch_applied", BUSINESS,
    "An owner applied a whole reviewed statement pass in one request.  It is "
    "its own event beside the per-act ones rather than a replacement for them: "
    "those say what each match and each recorded line did, and this says how "
    "much of what was ticked actually landed -- which is the only place a "
    "REFUSED item is counted at all.",
)

EVT_STATEMENT_RULES_FILED = _register(
    "statement_rules_filed", BUSINESS,
    "An import filed NEW swipe lines as purchases under the owner's own "
    "standing merchant rules, with no press (ruling R-GH, plan step "
    "bank_import:X-ge).  It MOVES MONEY, and it is the only door in the app "
    "that does so without an act in the same request: the consent was given "
    "when the rule was stated.  Its own event beside statement_imported, "
    "because recording what the bank said and filing it into the budget are "
    "different acts with different consequences -- the first moves no figure "
    "and this one does.  ``withheld_count`` is the half a count of what "
    "landed cannot report: lines a rule ANSWERS FOR that this pass refused to "
    "file, because it could not finish searching for a counterpart or because "
    "the destination is one the same statement already explains whole.",
)

EVT_MERCHANT_RULE_STATED = _register(
    "merchant_rule_stated", BUSINESS,
    "An owner said where one merchant's spending goes on one account -- a "
    "recurring envelope's template, a new envelope, never a purchase, or ask "
    "me every time.  It MOVES NO MONEY and can move none: a rule is read to "
    "SUGGEST a destination, and the only thing that records a purchase is an "
    "explicit destination submitted for one specific line.  Logged because "
    "the decision governs where later money is filed, and because the row is "
    "EDITED in place rather than superseded -- so this and the audit trail "
    "are the only record of what was said before.",
)


# ── Performance events (request lifecycle) ─────────────────────────

EVT_REQUEST_COMPLETE = _register(
    "request_complete", PERFORMANCE,
    "Request completed within the slow-request threshold (DEBUG-level).",
)
EVT_SLOW_REQUEST = _register(
    "slow_request", PERFORMANCE,
    "Request exceeded SLOW_REQUEST_THRESHOLD_MS (WARNING-level).",
)


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    category: str,
    message: str,
    **extra,
):
    """Emit a structured log entry with standardized fields.

    Every call site SHOULD pass an ``EVT_*`` constant for *event*
    rather than a bare string so a typo surfaces as a NameError at
    import time.  The helper does not enforce registry membership at
    runtime to keep the per-emit cost at O(1) -- the test suite covers
    completeness instead (see ``tests/test_utils/test_log_events.py``).

    Args:
        logger: The logger instance (typically ``logging.getLogger(__name__)``).
        level: Logging level (e.g., ``logging.INFO``).
        event: Machine-readable event name -- pass an ``EVT_*`` constant.
        category: Event category (one of ``AUTH``, ``BUSINESS``,
            ``ACCESS``, ``AUDIT``, ``ERROR``, ``PERFORMANCE``).
        message: Human-readable description; appears in the log
            record's ``message`` field for analyst readability.
        **extra: Additional key-value pairs included in the JSON
            output's structured fields.  Decimal values MUST be
            serialised by the caller (``str(amount)``) -- the JSON
            formatter does not know how to encode Decimal natively.
    """
    logger.log(
        level,
        message,
        extra={"event": event, "category": category, **extra},
    )
