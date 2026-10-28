"""
Shekel Budget App -- User and Authentication Models (auth schema)

Includes the User model (Flask-Login compatible), user settings,
and MFA/TOTP configuration.
"""

from decimal import Decimal

from flask_login import UserMixin

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.mixins import TimestampMixin


class User(UserMixin, TimestampMixin, db.Model):
    """Application user.  Flask-Login's UserMixin provides is_authenticated, etc.

    Account-lockout columns (see audit finding F-033 / commit C-11):

      * ``failed_login_count`` -- count of consecutive bad-password
        attempts.  Incremented inside ``auth_service.authenticate`` on
        every failed verify, reset to ``0`` on a successful login or
        when the threshold trips and ``locked_until`` is stamped.
      * ``locked_until`` -- exclusive upper bound on the lockout window;
        ``NULL`` outside lockout.  ``authenticate`` short-circuits and
        raises ``AuthError`` without running ``verify_password`` while
        ``locked_until > now`` so a brute-force attacker observes
        constant-time rejections during the lockout window (no timing
        oracle on whether the password was correct on the locked
        account).

    Together these columns implement per-account brute-force throttling
    that does not depend on Flask-Limiter's IP-keyed storage.  IP
    rotation (residential proxy, RFC 1918 spoofing) cannot bypass the
    per-account counter -- only knowing the password before the
    lockout trips can.  Threshold and duration are env-configurable
    via ``LOCKOUT_THRESHOLD`` and ``LOCKOUT_DURATION_MINUTES``; see
    ``BaseConfig``.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Defensive lower bound on the lockout counter.  The service
        # only ever increments from a non-negative starting value, but
        # a future raw-SQL backfill or a buggy migration that wrote a
        # negative value would otherwise quietly invert the lockout
        # logic (the "<= 0 means no lockout yet" branch would never
        # trip).  The CHECK is the database-side belt to the
        # service-side suspenders.
        db.CheckConstraint(
            "failed_login_count >= 0",
            name="ck_users_failed_login_count_non_negative",
        ),
        # ``last_security_event_kind`` is a free-text VARCHAR at the
        # SQL level so the column type stays interchangeable with any
        # future kind without an Alembic ``ALTER TYPE`` dance, but
        # the application only ever writes one of four enum values
        # (see ``app.utils.security_events.SecurityEventKind``).  The
        # CHECK pins the column to that whitelist at the database
        # tier so a future caller that bypasses the helper -- a
        # raw-SQL UPDATE, a forgotten branch in an admin script -- is
        # rejected at COMMIT instead of silently writing a kind the
        # banner template cannot render.  Audit finding F-091 / C-16
        # of the 2026-04-15 security remediation plan.
        #
        # The constraint is conditional on the column being non-NULL
        # so that historical rows where no security event has yet
        # been recorded continue to satisfy it.  Adding a kind to
        # ``SecurityEventKind`` requires a follow-up migration that
        # extends this whitelist; the migration must be deployed
        # before any code path that emits the new kind.
        db.CheckConstraint(
            "last_security_event_kind IS NULL OR "
            "last_security_event_kind IN ("
            "'password_changed', 'mfa_enabled', "
            "'mfa_disabled', 'backup_codes_regenerated')",
            name="ck_users_security_event_kind",
        ),
        # Pair invariant: ``kind`` and ``at`` are stamped together by
        # ``record_security_event`` and remain non-NULL together for
        # the row's lifetime.  A row carrying one without the other
        # would either fail the visibility check (kind missing -> banner
        # cannot render) or leak an indeterminate banner kind (at
        # missing -> banner shows "at None" copy).  Either case is a
        # programming error in a future caller; the CHECK promotes it
        # from a silent UI bug to a visible IntegrityError at COMMIT.
        db.CheckConstraint(
            "(last_security_event_at IS NULL) = "
            "(last_security_event_kind IS NULL)",
            name="ck_users_security_event_at_kind_paired",
        ),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    is_active = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )
    # Timestamp of most recent "log out all sessions" or password change event.
    # The user loader compares this against the session creation time to reject stale sessions.
    session_invalidated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Number of consecutive failed login attempts since the last
    # successful authenticate() or lockout-trip.  NOT NULL with a
    # server default of 0 so existing rows backfill cleanly and any
    # raw-SQL INSERT that omits the column still produces a usable
    # row.  See audit finding F-033 / commit C-11.
    failed_login_count = db.Column(
        db.Integer, nullable=False, server_default="0",
    )
    # Exclusive upper bound on the active lockout window, or NULL when
    # the account is not locked.  Comparison is strict greater-than:
    # at the instant the column equals ``now`` the lockout is over.
    # Nullable because most rows are NOT in lockout at any given moment
    # -- the column carries the "no active lockout" state as NULL
    # rather than as a sentinel datetime in the past, which keeps the
    # service-side check (``locked_until is not None and locked_until
    # > now``) explicit about both conditions.  See audit finding
    # F-033 / commit C-11.
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    role_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.user_roles.id", ondelete="RESTRICT"),
        nullable=False,
        server_default="1",  # 1 = owner
    )
    linked_owner_id = db.Column(
        db.Integer,
        # Named as migration b961beb0edf6 named it, which production holds
        # (plan#1858).
        db.ForeignKey(
            "auth.users.id", name="fk_users_linked_owner", ondelete="SET NULL",
        ),
    )
    # Security-event "was this you?" notification state (audit
    # finding F-091 / commit C-16 of the 2026-04-15 security
    # remediation plan).  See ``app/utils/security_events.py`` for
    # the recording / acknowledgement helpers and the kind enum;
    # see ``app/templates/_security_event_banner.html`` for the
    # rendering site.
    #
    # ``last_security_event_at`` -- timezone-aware moment of the
    # most recent password change, MFA enrol/disable, or backup-
    # code regeneration.  Nullable: rows that have never had a
    # security event are the common case for fresh accounts and
    # for accounts that have never rotated credentials, so the
    # NULL state is meaningful (no banner ever rendered).
    last_security_event_at = db.Column(
        db.DateTime(timezone=True), nullable=True,
    )
    # ``last_security_event_kind`` -- short machine code from
    # ``SecurityEventKind`` naming the change.  Database CHECK
    # constraint (``ck_users_security_event_kind``) pins the value
    # to the enum whitelist; pair CHECK
    # (``ck_users_security_event_at_kind_paired``) keeps this
    # NULL iff ``last_security_event_at`` is also NULL.  VARCHAR(50)
    # is generously sized so adding a longer kind in the future
    # does not require an ALTER TYPE.
    last_security_event_kind = db.Column(
        db.String(50), nullable=True,
    )
    # ``last_security_event_acknowledged_at`` -- timezone-aware
    # moment the user dismissed the banner.  Nullable: NULL means
    # "never dismissed" (the banner remains visible whenever an
    # event is present).  Stored on the row rather than in the
    # session cookie so dismissal persists across browsers /
    # devices and so an attacker who triggers a change cannot
    # suppress the legitimate user's banner just by dismissing it
    # on their own session first.
    last_security_event_acknowledged_at = db.Column(
        db.DateTime(timezone=True), nullable=True,
    )

    # Relationships
    settings = db.relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    role = db.relationship("UserRole", lazy="joined")

    @property
    def data_owner_id(self) -> int | None:
        """Return the id of the user whose budget data this user acts on.

        **The one statement of that rule.**  An owner acts on their own data; a
        companion acts on the data of the owner they are linked to
        (``linked_owner_id``), which is ``None`` when that owner was deleted
        (the FK is ``ON DELETE SET NULL``) -- or when the companion was
        written without one, which no constraint forbids and which
        :func:`app.services.entry_service.resolve_owner_id` refuses as a
        data-integrity fault.  Read off columns already
        loaded, so it issues no statement on a loaded row -- which is why it is
        a property of the row rather than a lookup by id: plan step
        ``balance:X-bn`` needs the owner inside a logging hook that must not
        open a transaction (:func:`app.db_transaction.bind_request_actor`).
        :func:`app.services.entry_service.resolve_owner_id` answers the same
        question by id and delegates here.

        Returns:
            The owning user's id, or ``None`` for a companion with no linked
            owner.
        """
        if self.role_id == ref_cache.role_id(RoleEnum.COMPANION):
            return self.linked_owner_id
        return self.id

    def __repr__(self):
        return f"<User {self.email}>"


class UserSettings(TimestampMixin, db.Model):
    """Per-user application preferences (grid defaults, inflation rate, etc.)."""

    __tablename__ = "user_settings"
    __table_args__ = (
        db.CheckConstraint(
            "default_inflation_rate >= 0 AND default_inflation_rate <= 1",
            name="ck_user_settings_valid_inflation",
        ),
        db.CheckConstraint("grid_default_periods > 0", name="ck_user_settings_positive_periods"),
        db.CheckConstraint(
            "low_balance_threshold >= 0",
            name="ck_user_settings_positive_threshold",
        ),
        db.CheckConstraint(
            "large_transaction_threshold >= 0",
            name="ck_user_settings_large_txn_threshold",
        ),
        # F-077 / C-24: SWR is the percentage of portfolio drawn
        # each year in retirement (4% rule -> ``Decimal("0.0400")``).
        # The retirement-settings route divides percent input by
        # 100 before persistence; CHECK pins storage to ``[0, 1]``.
        # The column carries a Python-side default but is nominally
        # nullable, so the CHECK admits NULL.
        db.CheckConstraint(
            "safe_withdrawal_rate IS NULL OR "
            "(safe_withdrawal_rate >= 0 AND safe_withdrawal_rate <= 1)",
            name="ck_user_settings_valid_safe_withdrawal",
        ),
        # F-077 / C-24: Estimated effective tax rate during
        # retirement.  NULL = no retirement-tax adjustment applied
        # -- ``retirement_gap_calculator.calculate_gap`` skips the
        # after-tax block entirely when ``estimated_tax_rate`` is
        # None (`:76`, `:110`), so the projection reports gross
        # pension income; no bracket-based fallback exists.  A-26 /
        # LOW-05: the prior comment promised a fallback that the
        # code does not implement; the documented contract now
        # matches the code (whether a bracket fallback SHOULD be
        # built is a carried product question, not a defect).  Same
        # percent-to-decimal convention as ``safe_withdrawal_rate``.
        db.CheckConstraint(
            "estimated_retirement_tax_rate IS NULL OR "
            "(estimated_retirement_tax_rate >= 0 AND "
            "estimated_retirement_tax_rate <= 1)",
            name="ck_user_settings_valid_estimated_tax_rate",
        ),
        db.CheckConstraint(
            "anchor_staleness_days > 0",
            name="ck_user_settings_positive_staleness_days",
        ),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    # E-11 / HIGH-06 (Commit 24): Python-side ``default`` constructed
    # from a string ``Decimal`` rather than a float literal.  See
    # :class:`~app.models.investment_params.InvestmentParams.assumed_annual_return`
    # for the full rationale; the same convention applies to every
    # ``Numeric`` column with a Python-side default.
    default_inflation_rate = db.Column(
        db.Numeric(5, 4), default=Decimal("0.0300"),
    )
    grid_default_periods = db.Column(db.Integer, default=6)
    # NOT NULL with a whole-dollar server default of 500 so the grid and
    # the dashboard chart threshold line always have a concrete value to
    # read -- the nullable column previously forced divergent literal-500
    # fallbacks at every read site (the grid routes, the settings form,
    # the dashboard pulse producer's None branch).  The Python-side
    # default keeps fresh ORM inserts at 500 without a round-trip.
    low_balance_threshold = db.Column(
        db.Integer, nullable=False, default=500, server_default="500",
    )
    safe_withdrawal_rate = db.Column(
        db.Numeric(5, 4), default=Decimal("0.0400"),
        server_default=db.text("0.0400"),
    )
    planned_retirement_date = db.Column(db.Date, nullable=True)
    estimated_retirement_tax_rate = db.Column(db.Numeric(5, 4), nullable=True)
    large_transaction_threshold = db.Column(
        db.Integer, nullable=False, server_default="500",
    )
    anchor_staleness_days = db.Column(
        db.Integer, nullable=False, server_default="14",
    )
    default_grid_account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Recurring surface (Loop B): which unit the Monthly / Per-paycheck
    # toggle shows first on the unified /templates page.  FALSE = monthly
    # (the default lens, standing in for the retired /obligations monthly
    # view); TRUE = per-paycheck (the grid-comparison lens the developer
    # reaches for).  The recurring-view producer renders BOTH units for
    # every figure regardless; this preference only picks the initially
    # visible one.  NOT NULL with a server default of FALSE so every
    # existing row lands on the monthly default -- the static default fits
    # all rows, so the column is added in one step.
    recurring_show_per_paycheck = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )

    # Back-reference to User
    user = db.relationship("User", back_populates="settings")
    default_grid_account = db.relationship(
        "Account", lazy="joined",
        foreign_keys=[default_grid_account_id],
    )

    def __repr__(self):
        return f"<UserSettings user_id={self.user_id}>"


class MfaConfig(TimestampMixin, db.Model):
    """MFA/TOTP configuration for a user.

    Stores the encrypted TOTP secret, enabled state, hashed backup
    codes, and confirmation timestamp.  One-to-one with auth.users
    (user_id is unique).

    The TOTP secret is encrypted at rest using Fernet symmetric
    encryption (key from TOTP_ENCRYPTION_KEY env var).  Backup codes
    are stored as a JSON list of bcrypt hashes.

    During an in-progress /mfa/setup flow the unconfirmed secret is
    held in ``pending_secret_encrypted`` (server-side, encrypted under
    the same MultiFernet as ``totp_secret_encrypted``) rather than in
    the user's session cookie, which Flask only signs and does not
    encrypt.  ``pending_secret_expires_at`` bounds the window in which
    the pending secret can be promoted to ``totp_secret_encrypted`` --
    abandoned setups become unusable on their own.  See audit finding
    F-031 / commit C-05.

    Replay prevention: ``last_totp_timestep`` records the highest 30-
    second time-step ever accepted from this row.  Subsequent verifies
    must produce a strictly greater step or are rejected as replays.
    See audit findings F-005, F-142 / commit C-09.

    Related service: app/services/mfa_service.py
    Related routes: /mfa/setup, /mfa/confirm, /mfa/verify, /mfa/disable
    """

    __tablename__ = "mfa_configs"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    totp_secret_encrypted = db.Column(db.LargeBinary)
    # Pending TOTP secret captured during /mfa/setup but not yet
    # confirmed by a valid TOTP code on /mfa/confirm.  Encrypted under
    # the same Fernet/MultiFernet key as ``totp_secret_encrypted``.
    # Nullable because most rows are NOT mid-setup at any given moment
    # (no setup ever started, or the most recent setup was confirmed,
    # expired, or abandoned and cleared).
    pending_secret_encrypted = db.Column(db.LargeBinary, nullable=True)
    # Wall-clock expiry of ``pending_secret_encrypted``.  /mfa/confirm
    # rejects pending state once this timestamp has passed so an
    # abandoned setup cannot be completed weeks later by an attacker
    # who briefly gains access to the account.  Nullable for the same
    # reason as ``pending_secret_encrypted``: most rows have no pending
    # setup in progress.
    pending_secret_expires_at = db.Column(
        db.DateTime(timezone=True), nullable=True,
    )
    is_enabled = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    backup_codes = db.Column(db.JSON)
    confirmed_at = db.Column(db.DateTime(timezone=True))
    # Highest TOTP time-step (Unix-seconds // 30) that the user has
    # successfully presented.  Replay prevention rejects any code that
    # decodes to a step less than or equal to this value -- without it,
    # a 30-second TOTP code with the standard +-1 drift window remains
    # replayable for ~90 seconds after observation.  See ASVS V2.8.4
    # and audit findings F-005, F-142 / commit C-09 of the 2026-04-15
    # security remediation plan.
    #
    # Nullable for two complementary reasons: rows that pre-date this
    # column have no recorded step (and the first successful verify
    # populates it), and rows where MFA has been disabled clear it
    # back to NULL so a re-enrollment under a new secret does not
    # inherit a stale step boundary.
    last_totp_timestep = db.Column(db.BigInteger, nullable=True)
