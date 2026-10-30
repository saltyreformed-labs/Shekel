"""
Shekel Budget App -- Scenario Model (budget schema)

A scenario is a named version of the budget.  Phase 1 uses only the
baseline scenario; Phase 3 adds clone, compare, and diff features.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin, UserScopedMixin


class Scenario(UserScopedMixin, TimestampMixin, db.Model):
    """A named budget scenario (baseline or what-if variant)."""

    __tablename__ = "scenarios"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_scenarios_user_name"),
        # H-1 of shekel-audits/security-2026-04-15/model-migration-drift.md:
        # at most one baseline scenario per user.  Production carries this
        # partial unique index from migration
        # c5d6e7f8a901_add_positive_amount_check_constraints.py;
        # the model-side declaration keeps db.create_all() (the test path)
        # in sync so the suite exercises the same constraint production
        # enforces.  Baseline scenarios are the load-bearing reference for
        # every balance projection -- two baselines for the same user is
        # a logic-corruption bug (the calculator picks an arbitrary one
        # and projections drift).
        db.Index(
            "uq_scenarios_one_baseline",
            "user_id",
            unique=True,
            postgresql_where=db.text("is_baseline = true"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    is_baseline = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    cloned_from_id = db.Column(
        db.Integer,
        # F-137 / C-42: explicit FK name follows the project's ``fk_*``
        # convention documented in ``docs/coding-standards.md``.  Earlier
        # the self-referential FK carried the Alembic-default
        # ``scenarios_cloned_from_id_fkey`` name; this declaration keeps
        # ``db.create_all()`` aligned with the post-C-42 migrated state.
        # SET NULL semantics preserved.
        db.ForeignKey(
            "budget.scenarios.id",
            name="fk_scenarios_cloned_from",
            ondelete="SET NULL",
        ),
    )

    def __repr__(self):
        return f"<Scenario '{self.name}' baseline={self.is_baseline}>"
