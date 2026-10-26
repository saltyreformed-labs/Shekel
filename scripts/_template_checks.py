"""The checks a test template built at HEAD is graded by, and the bake's error type.

Split out of :mod:`scripts.build_test_db_image` at plan step
``balance:X-bi-6-4d-2``: that file stood at 999 of pylint's 1000 lines, and the
side-record band rule's trigger family needed two more.  A split by
responsibility, the ground ``N-152`` gave every earlier one: the image script
builds, caches and verifies an image; WHAT a head-built database must carry --
each family's exact count from its producer's own constant -- is this module's,
and ``tests/test_scripts/test_init_database_one_transaction.py`` grades a first
boot by the same list.

The producers raise :class:`TemplateCheckError`; the image script's
``template_checks`` is the door that turns it into the bake's ``BuildError``.
Imported only by a verification, never at the image script's top: its
``--print-tag`` imports nothing beyond the standard library.
"""

from __future__ import annotations

import importlib


class TemplateCheckError(RuntimeError):
    """A check's expected count could not be read off its producer's constant."""


def _expected_account_types() -> int:
    """Return how many reference account types the template must carry.

    Returns:
        ``len(app.ref_seeds.ACCT_TYPE_SEEDS)``.

    Raises:
        TemplateCheckError: When the constant cannot be read.
    """
    return _import_constant("app.ref_seeds", "ACCT_TYPE_SEEDS", length=True)


def _expected_audit_triggers() -> int:
    """Return how many ``audit_*`` triggers the template must carry.

    Returns:
        ``app.audit_infrastructure.EXPECTED_TRIGGER_COUNT``.

    Raises:
        TemplateCheckError: When the constant cannot be read.
    """
    return _import_constant("app.audit_infrastructure", "EXPECTED_TRIGGER_COUNT")


def _expected_append_only_triggers() -> int:
    """Return how many append-only triggers the template must carry.

    The module attaches one trigger per statement kind per protected table,
    so the count is derived from the two lists rather than restated -- the
    arithmetic lives next to the thing it counts.

    Returns:
        ``len(APPEND_ONLY_TABLES) * len(APPEND_ONLY_TRIGGERS)``.

    Raises:
        TemplateCheckError: When the constants cannot be read.
    """
    tables = _import_constant("app.append_only_infrastructure",
                              "APPEND_ONLY_TABLES", length=True)
    kinds = _import_constant("app.append_only_infrastructure",
                             "APPEND_ONLY_TRIGGERS", length=True)
    return tables * kinds


def _trigger_family_check(module: str, attribute: str) -> tuple[str, int]:
    """Return the SQL that counts one trigger family, and the count it must find.

    One attachment per entry in the named ``(trigger name, table)`` constant:
    ``app.level_infrastructure.LEVEL_TRIGGERS`` (plan step ``balance:X-bj-1``),
    ``app.sighting_infrastructure.SIGHTING_TRIGGERS`` (plan step ``bank_import:X-f6b-1``),
    ``app.pay_stub_infrastructure.PAY_STUB_TRIGGERS`` (plan step ``salary:S11-a``) and
    ``app.deleted_row_infrastructure.DELETED_ROW_TRIGGERS`` (plan step ``credit_card:CC-5-4a-4``)
    and ``app.side_band_infrastructure.SIDE_BAND_TRIGGERS`` (plan step ``balance:X-bi-6-4d-2``).
    BOTH halves come from that one constant -- the names the query looks for and the number it must
    find -- so the check cannot count a trigger the module renamed, nor accept a template missing
    one.

    Args:
        module: Dotted module path of the infrastructure module.
        attribute: The constant's name on it.

    Returns:
        ``(sql, expected)``.

    Raises:
        TemplateCheckError: When the constant cannot be read.
    """
    try:
        triggers = getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError) as exc:
        raise TemplateCheckError(
            f"cannot read {module}.{attribute} ({exc}); the verification "
            "would otherwise compare against a number nobody owns"
        ) from exc
    names = ", ".join(f"'{name}'" for name, _table in triggers)
    return (
        "SELECT count(*) FROM pg_trigger "
        f"WHERE tgname IN ({names}) AND NOT tgisinternal",
        len(triggers),
    )


def _import_constant(module: str, name: str, *, length: bool = False) -> int:
    """Read one integer expectation out of the application package.

    Args:
        module: Dotted module path.
        name: Attribute to read.
        length: Take ``len()`` of the attribute rather than the value.

    Returns:
        The integer expectation.

    Raises:
        TemplateCheckError: When the import or the attribute fails, which means the
            producer moved and this verification would otherwise silently
            check the wrong number.
    """
    try:
        imported = importlib.import_module(module)
        value = getattr(imported, name)
    except (ImportError, AttributeError) as exc:
        raise TemplateCheckError(
            f"cannot read {module}.{name} ({exc}); the verification would "
            "otherwise compare against a number nobody owns"
        ) from exc
    return len(value) if length else int(value)


def template_checks() -> tuple[tuple[str, str, int], ...]:
    """Return the ``(label, sql, expected)`` checks both head-build paths are graded by.

    ONE list for both ways a database is built at head: :func:`_verify_image` asks each of the
    baked template, and ``tests/test_scripts/test_init_database_one_transaction.py`` of a first
    boot (``init_fresh_database``, which applies each family itself).  A family added here is
    graded on both paths (review L1 of the ``salary:S11-a`` carry-merge).  Counts are EXACT, from
    each producer's own constant.  Not listed: the posting and opening triggers, whose modules
    export no constant naming their triggers (finding BAL-542).

    Returns:
        One ``(label, sql, expected)`` per check.

    Raises:
        TemplateCheckError: When a producer's constant cannot be read.
    """
    return (
        ("account types", "SELECT count(*) FROM ref.account_types",
         _expected_account_types()),
        ("audit triggers",
         "SELECT count(*) FROM pg_trigger "
         "WHERE tgname LIKE 'audit\\_%' AND NOT tgisinternal",
         _expected_audit_triggers()),
        ("append-only triggers",
         "SELECT count(*) FROM pg_trigger "
         "WHERE tgname LIKE 'ck\\_append\\_only%' AND NOT tgisinternal",
         _expected_append_only_triggers()),
        ("level-within-file triggers",
         *_trigger_family_check("app.level_infrastructure", "LEVEL_TRIGGERS")),
        ("last-sighting triggers",
         *_trigger_family_check("app.sighting_infrastructure", "SIGHTING_TRIGGERS")),
        ("pay-stub refusal triggers",
         *_trigger_family_check("app.pay_stub_infrastructure", "PAY_STUB_TRIGGERS")),
        ("deleted-row triggers",
         *_trigger_family_check("app.deleted_row_infrastructure", "DELETED_ROW_TRIGGERS")),
        ("side-record band triggers",
         *_trigger_family_check("app.side_band_infrastructure", "SIDE_BAND_TRIGGERS")),
    )
