"""
Shekel Budget App -- Seed Reference Tables

Populates every ref-schema lookup table.

Uses upsert pattern to avoid duplicates on re-run; delegates to
``app.ref_seeds.seed_reference_data`` -- the single source of truth
for ref-table seeding across the application factory, the deploy, this
script, the pytest fixture stack, and the test-template builder.  See
audit finding H-002 for the rationale.

The deploy no longer runs this script: its step 3
(``scripts/init_database.py``, which ``scripts/deploy.sh`` runs in the
migrate service) seeds inside its one transaction, before anything reads
``ref_cache`` (plan step balance:X-cv, ruling R-BAL122).
This is the operator's repair tool, and it builds the app WITHOUT the
eager ``ref_cache.init`` (ruling R-BAL123, finding BAL-536): that init
treats a missing row as fatal, so a tool that ran it first died on
exactly the database it exists to repair.

Usage:
    python scripts/seed_ref_tables.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the sys.path bootstrap above must run
# before these imports so ``app`` resolves when invoked as
# ``python scripts/seed_ref_tables.py`` (sys.path[0] is scripts/, not
# the repo root, in that mode).
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.ref_seeds import seed_reference_data
# pylint: enable=wrong-import-position


def seed_ref_tables():
    """Seed every ref table, committing the transaction at the end.

    Thin wrapper around ``seed_reference_data`` that owns the
    transaction boundary and prints one line per inserted row so the
    operator sees what the repair inserted.
    """
    seed_reference_data(db.session, verbose=True)
    db.session.commit()
    print("\nRef table seeding complete.")


if __name__ == "__main__":
    # init_ref_cache=False: the cache is read AFTER a seed, never before one
    # (ruling R-BAL123).  Nothing here reads it.
    app = create_app(init_ref_cache=False)
    with app.app_context():
        seed_ref_tables()
