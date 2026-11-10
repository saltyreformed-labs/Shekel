"""
Shekel Budget App -- Salary Route Package

Split of the historical monolithic ``app/routes/salary.py`` (1600+ lines)
into a package of per-sub-domain modules, following the
``app/routes/accounts/`` precedent.  One ``salary_bp`` blueprint is shared
across every sub-module; the declaration lives in
:mod:`app.routes.salary._bp` (cycle-break, mirrors ``accounts/_bp.py``).
Each per-domain module imports the blueprint from ``_bp`` and registers
its route decorators against it.  Every URL and endpoint name is preserved
verbatim from the pre-split file, so no ``url_for`` call site, template,
or ``app/__init__.py`` import needed an edit (``app/__init__.py`` continues
to import ``salary_bp`` from this package by the same name, re-exported
below).

Module map:

* :mod:`app.routes.salary._bp` -- ``salary_bp`` blueprint declaration
  (leaf module; cycle-break).
* :mod:`app.routes.salary._helpers` -- shared Marshmallow schema
  singletons, form-field allowlists, unique-constraint name constants,
  and the private helpers used across handlers
  (``_regenerate_salary_transactions``, the
  ``_render_*_partial`` / ``_respond_after_*_change`` HTMX responders,
  ``_get_investment_accounts``).
* :mod:`app.routes.salary.cockpit` -- the ``GET /salary`` cockpit landing
  page (``cockpit``) and its HTMX period-stepping fragment (``anatomy``).
* :mod:`app.routes.salary.profiles` -- salary-profile CRUD
  (``new_profile``, ``create_profile``, ``edit_profile``,
  ``update_profile``, ``delete_profile``, ``reactivate_profile``).
* :mod:`app.routes.salary.pay_list` -- the pay list's doors (plan steps
  salary:X-av-3a and X-av-3b): Record a pay change (``record_pay_entry``)
  and Fix (``fix_pay_entry``, here from ``profiles`` since X-av-3b).
* :mod:`app.routes.salary.items` -- raise and deduction add/edit/delete
  (the two parallel salary line-item families, co-located).
* :mod:`app.routes.salary.views` -- projection view + retired-breakdown
  redirect stubs into the cockpit.
* :mod:`app.routes.salary.checkpoint` -- the analytics Taxes tab's YTD
  tax-checkpoint upsert (update-from-stub).
* :mod:`app.routes.salary.stubs` -- the transcribed pay stub's entry door
  (plan step salary:S11-b): the payday step, record, the stub's page, edit
  and its "Use for pricing" switch.  Since plan step salary:S11-c-2c the
  switched-on stubs price the paychecks' taxes, and the calibration flow
  this map listed (form, preview, confirm, delete) is deleted with the
  pricing path it fed (ruling R-SAL100).
* :mod:`app.routes.salary.tax_config` -- the old tax-configuration URL,
  redirected to the read-only Settings tax section.
"""

# Re-export ``salary_bp`` from the leaf declaration module so consumers
# that ``from app.routes.salary import salary_bp`` (notably
# ``app/__init__.py`` at factory time) resolve without an edit.
from app.routes.salary._bp import salary_bp

# Import sub-modules for the side effect of registering their route
# decorators against ``salary_bp``.  The ``noqa`` markers suppress the
# unused-import / out-of-order-import warnings that would otherwise fire
# on what is, by design, a deferred-import side-effect registration.
from app.routes.salary import cockpit  # noqa: F401, E402
from app.routes.salary import profiles  # noqa: F401, E402
from app.routes.salary import pay_list  # noqa: F401, E402
from app.routes.salary import items  # noqa: F401, E402
from app.routes.salary import views  # noqa: F401, E402
from app.routes.salary import checkpoint  # noqa: F401, E402
from app.routes.salary import stubs  # noqa: F401, E402
from app.routes.salary import tax_config  # noqa: F401, E402


__all__ = ["salary_bp"]
