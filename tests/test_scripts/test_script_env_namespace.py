r"""The test runner shares no operator variable with any other shell script.

``scripts/test.sh`` read the bare ``DB_CONTAINER`` from the environment to
name the test-db container.  ``scripts/backup.sh``, ``scripts/restore.sh``
and ``scripts/verify_backup.sh`` read the SAME name, from the SAME place, to
name the PRODUCTION container -- and ``restore.sh`` DROPs and re-creates the
database it is given.  One exported value therefore pointed both ways at
once: a worktree isolating its suite with ``DB_CONTAINER=<name>-test-db``
silently re-aimed ``restore.sh``, and an operator who exported
``DB_CONTAINER=shekel-prod-db`` for a backup then aimed the test runner's
``docker restart`` at production.  ``deploy/shekel-deploy.sh`` had already
sidestepped it by reading ``SHEKEL_DB_CONTAINER``; the test runner took
``TEST_DB_CONTAINER``.

**That specific collision is now structurally gone, and the general one is
not, which is why this module stayed.**  ``balance:X-br-4`` deleted the
shared container along with the restart, so the runner no longer takes a
container name from anybody: it names its own throwaway
``shekel-testrun-$$``.  What it still does is ``docker rm -fv`` -- so the
shape survives with a different verb, and any future knob naming a container
would re-arm it.  Nothing structural stops the name coming back either --
shell environments are one flat namespace and the two scripts never import
each other -- so the invariant is asserted here rather than left to a
reviewer's memory.

The comparison set is DISCOVERED, not listed
    An earlier draft of this module hand-listed "every script that writes to
    or destroys production data".  It was not that set; it was the residue of
    one ``DB_CONTAINER`` grep, and it silently omitted ``entrypoint.sh``
    (which runs ``alembic upgrade`` against production), ``backup_retention.sh``
    (which deletes backup archives) and two more.  A hand list cannot see a
    script written after it, which is the one case a guard is for.

    So the set is every ``*.sh`` under ``scripts/`` and ``deploy/`` plus the
    repository root, minus the runner itself.  No script inside those roots
    is exempted -- there is no per-file carve-out list to rot.  Be precise
    about what that does and does not buy, because the first draft of this
    paragraph overclaimed it: the ROOTS are still an allowlist, so a shell
    script added under ``tools/`` or ``.github/`` would not be compared, and
    the floor test below pins one member per root so a root that stops
    resolving fails loudly instead of shrinking the sweep in silence.
    Measured 2026-09-05: 18 scripts, zero collisions, so the broad form
    costs nothing today.  (19 on 2026-09-04, before ``balance:X-br-4``
    deleted ``scripts/suite_slot.sh``.)

What the extractor matches, and which way it errs
    It collects UPPER-CASE names three ways: both expansion forms,
    ``${NAME}`` and the bare ``$NAME``, plus assignment targets, ``NAME=``
    and ``export NAME=``.  Each was added because the previous shape let a
    real regression through.  Matching only the braced expansion was this
    module's own first bug: the natural way a future session keeps the old
    spelling alive is

        if [ -n "$DB_CONTAINER" ]; then TEST_DB_CONTAINER="$DB_CONTAINER"; fi

    which is unbraced, and a braces-only extractor passes it silently.  Do
    not narrow the pattern back.  Nothing else catches that shape either:
    shellcheck flags UNQUOTED expansions, not unbraced ones -- SC2250 is
    optional and this repo's ``.shellcheckrc`` does not enable it, and
    ``scripts/test.sh`` itself expands ``"$PYTEST_MARKER_EXPR"`` unbraced
    while shellcheck exits clean.  Count such expansions with
    ``grep -o '\$[A-Z][A-Z0-9_]*' scripts/test.sh | wc -l`` rather than
    trusting a number here (``grep -c`` counts matching LINES, so it
    undercounts any line carrying two expansions -- the first instrument
    written to replace an untrustworthy number was itself the wrong tool).
    This sentence carried a literal count twice and was wrong
    both times -- "seven", then "nine" against an actual twelve, each written
    just after editing the very file being counted; the variable those counts
    were OF has since been deleted, which is a third way for such a figure to
    go wrong.  A figure that must be re-derived on every edit of its own
    subject does not belong in prose, and the argument never needed it: what
    matters is that the form is used at all and that nothing else flags it.

    Case is the filter that keeps this quiet enough to live with.  Operator
    -settable variables in these scripts are ALL-CAPS by convention and
    script-internal ones are lower-case (``local_path``, ``deadline``,
    ``nas_tmp``), so upper-case-only drops the locals without dropping any
    name an export can reach.

    What this does NOT assert is that the runner's variables are unshared in
    general.  ``TEST_DATABASE_URL`` and ``TEST_ADMIN_DATABASE_URL`` are
    deliberately shared -- the runner EXPORTS both, naming its private
    cluster's socket, and ``tests/_worker_database.py`` reads them.  (Until
    ``balance:X-br-4`` two more were shared the other direction,
    ``TEST_DB_PREFIX`` and ``TEST_TEMPLATE_DATABASE``, read from ``.env`` by
    the runner and by ``scripts/build_test_template.py``; a cluster per run
    left them nothing to name apart and they were deleted.)  Those consumers
    are PYTHON and are outside this sweep by construction; the invariant here
    is between the runner and other SHELL scripts, which is where the
    flat-namespace hazard lives.

    It still OVER-connects, deliberately: ``deploy/shekel-deploy.sh``
    assigns its own local ``DB_CONTAINER`` from ``SHEKEL_DB_CONTAINER`` and
    then expands it, so the extractor counts a name no export actually
    reaches.  Over-connecting is the safe direction for a guard -- the worst
    it can do is refuse a coincidence that was harmless, and the remedy is
    the same either way: give the test runner its own name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_TEST_RUNNER = Path("scripts/test.sh")

# ``${NAME}``, ``${NAME:-default}`` and the bare ``$NAME``.  Upper-case only;
# see the module docstring for why both forms and why the case filter.
_ENV_READ = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\b")

# Assignment targets: ``NAME=`` and ``export NAME=`` at the head of a line.
# Reads alone are blind to the WRITE bridge, which is the other natural way
# to keep an old spelling alive:
#
#     export DB_CONTAINER="$TEST_DB_CONTAINER"
#
# That line never EXPANDS ``DB_CONTAINER``, so a read-only extractor stays
# green while the runner injects the production container's name into every
# child process it spawns.  Measured 2026-09-05: unioning assignments adds
# zero names to the runner's set and zero collisions across all 18 scripts,
# so this costs nothing today and closes the shape.
_ENV_WRITE = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Z][A-Z0-9_]*)=", re.M)

# Names the shell or the harness owns, not the operator.  A collision on one
# of these says nothing about the hazard above.
_SHELL_OWNED = frozenset(
    {
        "BASH_SOURCE",
        "FUNCNAME",
        "HOME",
        "IFS",
        "LINENO",
        "PATH",
        "PWD",
        "RANDOM",
        "SHELL",
        "TMPDIR",
        "USER",
    }
)

# The smallest count that proves discovery ran.  Well under the 18 measured
# on 2026-09-05 (19 on 2026-09-04, before ``balance:X-br-4`` deleted
# ``scripts/suite_slot.sh``), so ordinary churn does not trip it, but far
# above what a broken glob returns.
_MIN_DISCOVERED_SCRIPTS = 12


def _other_shell_scripts() -> list[Path]:
    """Return every shell script in the repo except the test runner.

    Returns:
        Repo-relative paths, sorted, from ``scripts/`` and ``deploy/``
        (recursively) plus the repository root.
    """
    found = {
        path.relative_to(_REPO_ROOT)
        for directory in ("scripts", "deploy")
        for path in (_REPO_ROOT / directory).rglob("*.sh")
    }
    found |= {path.relative_to(_REPO_ROOT) for path in _REPO_ROOT.glob("*.sh")}
    return sorted(found - {_TEST_RUNNER})


def _names_in_source(source: str) -> set[str]:
    """Return the upper-case names expanded or assigned in shell source.

    Split out from :func:`_upper_case_names` so the UNION of the two regexes
    is reachable from a test without a file on disk.  It was not, and the
    consequence was measured: substituting a never-matching pattern for
    ``_ENV_WRITE`` left all of this module's tests green, so the capability
    added to catch the write bridge was itself unguarded and could have been
    narrowed or deleted by anyone without breaking a thing.

    Args:
        source: Shell script text.

    Returns:
        Upper-case expanded and assigned names, minus shell-owned ones.
    """
    found = set(_ENV_READ.findall(source)) | set(_ENV_WRITE.findall(source))
    return found - _SHELL_OWNED


def _upper_case_names(relative_path: Path) -> set[str]:
    """Return the upper-case names a shell script expands or assigns.

    The name says what this measures rather than what it is used for: the
    result is every upper-case ``$NAME`` / ``${NAME}`` expansion plus every
    upper-case ``NAME=`` assignment target.  That is a SUPERSET of the
    environment variables the script reads -- it includes upper-case locals
    the script assigned itself and names appearing only inside comments or
    error strings, and it is a superset only of reads written in those two
    expansion forms.  The superset is the point; see the module docstring on
    which way the extractor errs.

    Args:
        relative_path: Script path relative to the repository root.

    Returns:
        Upper-case expanded and assigned names, minus shell-owned ones.
        No underscore filter: ``_ENV_READ`` and ``_ENV_WRITE`` both begin
        their capture with ``[A-Z]``, so an underscore-prefixed name such as
        ``_REPO_ROOT`` is never produced in the first place.  An earlier
        draft filtered for it and documented the filter as live, which was
        dead code describing itself as load-bearing.
    """
    return _names_in_source(
        (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    )


class TestScriptEnvNamespace:
    """No operator variable reaches both the suite and another script."""

    def test_discovery_finds_the_scripts_and_the_known_hazards(self) -> None:
        """Discovery ran, and it reaches the scripts that motivated it.

        An empty or truncated discovery makes every disjointness assertion
        below vacuously true, and a parametrized sweep over an empty list
        reports nothing at all rather than failing.  Pin both the floor and
        the three scripts whose production writes are the reason this
        module exists.
        """
        discovered = _other_shell_scripts()

        assert len(discovered) >= _MIN_DISCOVERED_SCRIPTS, (
            f"discovery returned only {len(discovered)} shell scripts "
            f"({[p.as_posix() for p in discovered]}); the glob is broken and "
            "every check below would pass while measuring nothing"
        )
        # One pin per DISCOVERY ROOT, not merely three important scripts.
        # With ``scripts/`` and the repo root pinned but ``deploy/`` not, a
        # deploy root that stopped resolving would return 18 scripts, clear
        # the floor of 12, satisfy every pin, and drop
        # ``deploy/shekel-deploy.sh`` out of the comparison in silence.
        for required in (
            Path("scripts/restore.sh"),  # scripts/: DROPs the prod database
            Path("scripts/backup_retention.sh"),  # scripts/: deletes archives
            Path("deploy/shekel-deploy.sh"),  # deploy/: deploys and migrates
            Path("entrypoint.sh"),  # repo root: alembic upgrade against prod
        ):
            assert required in discovered, (
                f"{required} is not in the discovered set; the guard would "
                "not compare the test runner against it"
            )

    def test_extractor_finds_the_names_it_is_asked_about(self) -> None:
        """The regex matches, so a disjointness pass is not vacuous.

        Two empty sets are disjoint.  If ``_ENV_READ`` ever stops matching
        (a quoting-style change, a rewrite), the real assertion below would
        pass while measuring nothing -- so pin one known member on each side
        first.
        """
        runner = _upper_case_names(_TEST_RUNNER)
        restore = _upper_case_names(Path("scripts/restore.sh"))

        assert "TEST_DB_IMAGE" in runner, (
            f"extractor found {sorted(runner)} in {_TEST_RUNNER}, which does "
            "not include an operator variable it demonstrably reads.  The pin "
            "was TEST_DB_CONTAINER until balance:X-br-4 deleted the shared "
            "container; if TEST_DB_IMAGE goes the same way, repin it on "
            "another name the runner reads rather than dropping this "
            "assertion -- two empty sets are disjoint"
        )
        assert "DB_CONTAINER" in restore, (
            f"extractor found {sorted(restore)} in scripts/restore.sh, which "
            "does not include the container name it demonstrably reads"
        )

    def test_extractor_sees_unbraced_expansions(self) -> None:
        """Both ``$NAME`` and ``${NAME}`` are matched.

        A braces-only pattern passes the likeliest regression -- a
        back-compat shim assigning ``TEST_DB_CONTAINER="$DB_CONTAINER"`` --
        and nothing else in the toolchain catches it.  Pin both forms so
        narrowing the regex breaks a test instead of quietly disarming the
        guard.
        """
        found = set(_ENV_READ.findall('X="${BRACED:-a}"; Y="$UNBRACED"'))

        assert found == {"BRACED", "UNBRACED"}, (
            f"extractor matched {sorted(found)}; it must see both expansion "
            "forms, because a back-compat shim is written unbraced"
        )

    def test_extractor_sees_assignment_targets(self) -> None:
        """The WRITE half of the union is live, and is exercised here.

        ``_ENV_WRITE`` exists to catch the bridge a read-only extractor
        cannot see::

            export DB_CONTAINER="$TEST_DB_CONTAINER"

        A review measured that deleting it changed nothing any test asserted
        -- the runner's name set was byte-identical and all tests stayed
        green -- so the capability was present but unguarded.  These
        assertions fail if it is removed, narrowed to exports only, or
        allowed to match lower-case locals.
        """
        assert set(_ENV_WRITE.findall("export FOO=1\n  BAR=2\nbaz=3\n")) == {
            "FOO",
            "BAR",
        }, "the assignment regex must see plain and exported ALL-CAPS targets"

        # The union, not just the regex: a name that is only ever ASSIGNED
        # must still reach the disjointness comparison.  The left-hand side
        # here is deliberately lower-case -- an upper-case one would itself
        # be an assignment target and muddy which regex contributed what,
        # which is exactly how this assertion was wrong on first writing.
        assert _names_in_source(
            'local_x="$READ_ONLY"\nexport WRITE_ONLY=1\n'
        ) == {
            "READ_ONLY",
            "WRITE_ONLY",
        }, "_names_in_source must union expansions with assignment targets"

    @pytest.mark.parametrize(
        "other", _other_shell_scripts(), ids=lambda p: p.as_posix()
    )
    def test_runner_shares_no_operator_variable(self, other: Path) -> None:
        """No single exported name reaches the runner and another script.

        Args:
            other: Any other shell script in the repository.
        """
        shared = _upper_case_names(_TEST_RUNNER) & _upper_case_names(
            other
        )

        assert not shared, (
            f"{sorted(shared)} is used by BOTH {_TEST_RUNNER} and {other}.  "
            "If it is an operator variable, one exported value reaches both "
            "-- which is how the test runner once aimed a docker restart at "
            "the production container.  Three remedies, in the order to "
            "consider them: give whichever side is newer its own prefixed "
            "name (TEST_ for the runner, SHEKEL_ for deploy, as "
            "TEST_DB_IMAGE and SHEKEL_DB_CONTAINER already do); or, if "
            "it is a script-internal local, lower-case it, since this check "
            "reads ALL-CAPS as operator-settable by convention; or, if the "
            "sharing is deliberate and safe, narrow this test and say why "
            "-- do not delete it."
        )

    def test_runner_does_not_read_the_bare_db_container(self) -> None:
        """The specific regression: ``DB_CONTAINER`` is production's alone.

        The runner no longer takes a container name from anyone -- it names
        its own ``shekel-testrun-$$`` -- so this is a guard against the knob
        coming back, not against one that is there.  It is worth keeping
        because the runner still runs ``docker rm -fv`` on whatever it is
        pointed at: the verb changed from ``docker restart`` and the
        consequence did not.
        """
        assert "DB_CONTAINER" not in _upper_case_names(_TEST_RUNNER), (
            "scripts/test.sh reads the bare DB_CONTAINER again.  That name "
            "means the PRODUCTION container to scripts/restore.sh, which "
            "DROPs the database it is given -- and this runner force-removes "
            "the container it is given.  Prefix any such name with TEST_."
        )
