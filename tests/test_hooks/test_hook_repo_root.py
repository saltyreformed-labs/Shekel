"""``hook_repo_root`` resolves the SESSION's checkout, not the primary one.

**The first tests any hook helper has had**, and the absence is why this
regressed silently.  ``hook_repo_root`` was introduced to be the single
authority for repo-root resolution (finding HOOK/SH-12, after the question was
spelled three different ways in three scripts) -- and it resolved the wrong
tree for every session working in a git WORKTREE, which under this project's
Multi-session doctrine is every session.

**What that cost, measured 2026-09-04 in ``~/projects/shekel-xauf``.**
``hook_target_relpath`` normalizes the edited file against the resolved root; a
worktree file is not under the primary checkout, so it came back ABSOLUTE, and
every caller's ``case`` pattern missed.  The gates did not fail -- they SKIPPED.
``Decimal(0.1)`` written into ``app/`` through both Write and Edit was not
blocked, though pylint reports W9901 (``shekel-decimal-from-float``) and exits 4
on that file.  ``stop-check.sh`` meanwhile cd'd to the primary checkout and
linted a tree the session was not editing, reporting a peer's uncommitted work
as this lane's failure while passing this lane's real one.

**Why these cases are shaped as they are.**  The bug needs the session's cwd and
``CLAUDE_PROJECT_DIR`` to DISAGREE, which is what the sessions working in
worktrees on 2026-08-29 and 2026-09-04 measured, and what a single-checkout
test would never reproduce -- so the cases that grade the fix build a real git
checkout, point ``CLAUDE_PROJECT_DIR`` somewhere else, and assert against the
cwd.

**Which cases are the CONTROL, measured rather than reasoned.**  Substituting
``origin/dev``'s helper and re-running gives **7 failed, 3 passed** (2026-09-04).
The three that pass either way are kept deliberately and are NOT padding --
they are boundary guards: the fallback arm, the trailing-slash strip finding
HOOK/SH-12 added, and the file genuinely outside the project, which is what
stops the fix turning "resolve the right root" into "match everything".

**Two of those seven failures were weaker than the other five, and saying so
was the point.**  The three toolchain cases of the day failed against dev only
because ``hook_toolchain_root`` did not exist there, so bash errored -- that
guarded the function being DELETED and said nothing about it returning a wrong
value.  The value-level control was a separate injection, also run:
re-pointing the toolchain resolver at the edited tree
(``hook_toolchain_root() { hook_repo_root; ... }``) failed those same three and
only those three, 3 failed / 7 passed.  Both directions were run; neither was
inferred.

**The toolchain cases grade the review's H-1**, which is a regression this fix
introduced before review caught it: ``hook_repo_root`` had consumers in five
files, and a further one inside ``_hooklib.sh`` itself -- the ``_hook_venv_bin``
PATH prefix -- was asking "where does the pinned toolchain live", not "which
tree is being edited".  For that question the MAIN checkout is the right
answer, because worktrees carry no ``.venv`` of their own.  Re-pointing it at
the session's worktree left the venv path naming a directory that does not
exist, so ``pylint`` became unresolvable and the two gates that invoke it (the
Python per-edit gate and the Stop floor) hard-blocked on "command not found"
while blaming a financial-correctness rule.

**That guard answered ``CLAUDE_PROJECT_DIR`` until 2026-10-27, which was the
same defect one step removed** (finding plan#1849, ruling plan#1848).  It held
only while ``CLAUDE_PROJECT_DIR`` named the primary checkout, and a session
LAUNCHED in a worktree gets that worktree (measured 2026-10-26).  So a session
launched in a worktree with no ``.venv`` link ran whatever ``pylint`` its PATH
held: the system's crashed on an untouched file and the gate blocked the edit
(measured 2026-10-27).  :class:`TestTheToolchainRootIsTheMainCheckout` now
asks git from the library's own directory, and holds the cases the old guard
never tried: ``CLAUDE_PROJECT_DIR`` naming the session's own worktree, and a
session standing outside every repository.
"""

import json
import subprocess
from pathlib import Path

import pytest


#: The helper under test, resolved from THIS file rather than from a cwd --
#: these cases deliberately run bash with cwd set elsewhere.
_HOOKLIB = Path(__file__).resolve().parents[2] / "scripts" / "hooks" / "_hooklib.sh"


def _bash(
    script: str, cwd: Path, project_dir: Path | str, stdin: str = "",
    library: Path = _HOOKLIB,
) -> str:
    """Source the hook library and run *script*, returning its stdout.

    Args:
        script: Bash to run after the library is sourced.
        cwd: The working directory to run in -- stands in for the session's
            own checkout.
        project_dir: The value of ``CLAUDE_PROJECT_DIR`` -- stands in for the
            directory the session was launched in, which need not be the tree
            it edits.  A ``str`` keeps a trailing slash; a ``Path`` drops it.
        stdin: Payload piped to the script, for the helpers that read it.
        library: The copy of ``_hooklib.sh`` to source.  The toolchain
            resolver asks git from the library's own directory, so its cases
            source a copy inside the fixture repository.

    Returns:
        Stdout, stripped.
    """
    completed = subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{library}"; {script}'],
        cwd=cwd,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        },
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"hook helper exited {completed.returncode}: {completed.stderr}"
    )
    return completed.stdout.strip()


@pytest.fixture(name="checkout")
def _checkout(tmp_path: Path) -> Path:
    """A real git repository standing in for the session's own worktree.

    A real one rather than a stubbed directory: the helper resolves the root
    with ``git rev-parse --show-toplevel``, so a fake would grade the fallback
    branch instead of the fix.
    """
    root = tmp_path / "session-checkout"
    (root / "app").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


@pytest.fixture(name="linked_worktree")
def _linked_worktree(checkout: Path) -> Path:
    """A real ``git worktree`` checkout, whose ``.git`` is a FILE.

    The shape the bug was specific to, and the one ``git init`` cannot produce.
    A worktree needs a commit to branch from, so the fixture makes an empty one
    with identity supplied on the command line rather than from a global config
    the test environment may not have.
    """
    identity = [
        "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
    ]
    subprocess.run(
        ["git", "-C", str(checkout), *identity,
         "commit", "-q", "--allow-empty", "-m", "seed"],
        check=True,
    )
    linked = checkout.parent / "linked-worktree"
    subprocess.run(
        ["git", "-C", str(checkout), "worktree", "add", "-q", str(linked)],
        check=True,
    )
    # The premise, asserted rather than assumed: if this ever became a
    # directory the cases below would silently degrade to the ``git init``
    # shape they exist to be different from.
    assert (linked / ".git").is_file()
    return linked


@pytest.fixture(name="primary")
def _primary(tmp_path: Path) -> Path:
    """A DIFFERENT directory, standing in for the primary checkout.

    This is the decoy: ``CLAUDE_PROJECT_DIR`` can name a directory other than
    the tree the session edits (the primary checkout, as two sessions working
    in worktrees measured it on 2026-08-29), and the pre-fix helper returned it
    unconditionally.
    """
    root = tmp_path / "primary-checkout"
    root.mkdir()
    return root


class TestTheRootResolvesToTheSessionsCheckout:
    """The resolution itself, and the decoy it must not follow."""

    def test_repo_root_prefers_the_sessions_own_checkout(
        self, checkout: Path, primary: Path,
    ):
        """cwd's git toplevel wins over ``CLAUDE_PROJECT_DIR``.

        The whole bug in one assertion.  Pre-fix this returned *primary*.
        """
        assert _bash("hook_repo_root", checkout, primary) == str(checkout)

    def test_a_trailing_slash_is_still_stripped(
        self, checkout: Path, primary: Path,
    ):
        """The behaviour finding HOOK/SH-12 added is not lost to the fix.

        A trailing slash silently defeated the relpath strip once already; the
        fallback path is the one that can still carry one, so it is what this
        exercises.

        **Asserts the VALUE, not just the absence of a slash** (adversarial
        review, 2026-09-04).  ``assert not root.endswith("/")`` admits ``""``,
        which is the catastrophic answer: not because ``cd ""`` succeeds (it
        fails with "null directory" and trips the callers' ``||`` block) but
        because ``hook_target_relpath`` realpaths the root, and
        ``os.path.realpath("")`` is the CWD, so an empty root would silently
        normalize every edited file against whatever tree the hook stood in.
        Comparing against *primary* also proves the FALLBACK arm was the one
        taken: this case only reaches it because ``tmp_path`` sits outside
        any repository, and if ``TMPDIR`` were ever pointed inside a checkout
        ``git rev-parse`` would succeed, git output never carries a trailing
        slash, and the strip would go ungraded while the test still passed.

        **The slash goes in as a string.**  Until 2026-10-27 this passed
        ``Path(f"{primary}/")``, and pathlib drops a trailing slash, so the
        helper never saw one and the strip went ungraded (a mutant deleting the
        strip in the toolchain resolver survived the same way that day).
        """
        root = _bash("hook_repo_root", checkout.parent, f"{primary}/")
        assert root == str(primary)

    def test_it_FALLS_BACK_outside_a_git_checkout(
        self, tmp_path: Path, primary: Path,
    ):
        """Outside a repository the old answer is still the right one.

        ``git rev-parse`` fails there, and failing to the documented
        environment variable is better than failing to a bare ``$PWD`` that
        names no project at all.
        """
        loose = tmp_path / "not-a-repo"
        loose.mkdir()
        assert _bash("hook_repo_root", loose, primary) == str(primary)


class TestTheShapeTheFixExistsFor:
    """A LINKED worktree, which is the only shape that ever had the bug."""

    def test_a_linked_worktree_resolves_to_ITSELF(
        self, linked_worktree: Path, primary: Path,
    ):
        """``git worktree add``, not ``git init`` -- the production shape.

        Every other case here builds a repository with ``git init``, which
        gives a ``.git`` DIRECTORY.  A linked worktree has a ``.git`` FILE
        holding ``gitdir: /path/to/primary/.git/worktrees/<name>``, and that is
        the only shape this fix exists for: the sessions that lost their gates
        were all ``git worktree`` checkouts.  A suite that grades only the
        ``git init`` shape grades the fix's neighbourhood rather than the fix
        (adversarial review, 2026-09-04).
        """
        assert _bash(
            "hook_repo_root", linked_worktree, primary,
        ) == str(linked_worktree)

    def test_a_SUBDIRECTORY_resolves_to_the_worktree_root(
        self, linked_worktree: Path, primary: Path,
    ):
        """Hooks do not necessarily run at the top of the tree.

        The other discriminating improvement, and it was ungraded: from
        ``<worktree>/app`` the old code returned the primary and the new code
        returns the worktree ROOT, which is what every caller's ``cd`` and
        relpath depend on.
        """
        nested = linked_worktree / "app"
        nested.mkdir(exist_ok=True)
        assert _bash("hook_repo_root", nested, primary) == str(linked_worktree)


@pytest.fixture(name="hooked")
def _hooked(checkout: Path) -> tuple[Path, Path]:
    """Return (main, linked): a repository holding this library, and a worktree.

    The toolchain resolver asks git from the library's OWN directory, so the
    copy under test must sit inside the fixture repository, committed so the
    linked worktree checks out a copy of its own, as every lane does.
    """
    library = checkout / "scripts" / "hooks" / "_hooklib.sh"
    library.parent.mkdir(parents=True)
    library.write_bytes(_HOOKLIB.read_bytes())
    identity = [
        "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
    ]
    subprocess.run(["git", "-C", str(checkout), "add", "scripts"], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), *identity, "commit", "-q", "-m", "hooks"],
        check=True,
    )
    linked = checkout.parent / "hooked-worktree"
    subprocess.run(
        ["git", "-C", str(checkout), "worktree", "add", "-q", str(linked)],
        check=True,
    )
    return checkout, linked


def _library(tree: Path) -> Path:
    """The copy of ``_hooklib.sh`` checked out in *tree*."""
    return tree / "scripts" / "hooks" / "_hooklib.sh"


class TestTheToolchainRootIsTheMainCheckout:
    """The H-1 regression's guard: two questions, two answers.

    ``_hook_venv_bin`` asks where the PINNED toolchain lives, which is not the
    tree being edited.  Worktrees carry no ``.venv`` of their own -- measured
    across the eight on this machine on 2026-09-04: a real one in the primary,
    a symlink in one worktree, nothing in the other six.  So this answers the
    repository's MAIN worktree, as git names it from the library's own
    directory, whatever directory the session is in and whatever
    ``CLAUDE_PROJECT_DIR`` names (ruling plan#1848).  Otherwise the PATH prefix
    silently vanishes and the two gates that invoke ``pylint`` (the Python
    per-edit gate and the Stop floor) block an edit for a reason that is not in
    it.

    **The control, run 2026-10-27:** against the resolver that stood before
    (``CLAUDE_PROJECT_DIR``), every case here but the last fails; the last, the
    fallback outside any checkout, passes on both, as it should.
    """

    @pytest.mark.parametrize("launched_in", ["its-worktree", "a-decoy"])
    def test_from_a_worktree_it_answers_the_main_checkout(
        self, hooked: tuple[Path, Path], primary: Path, launched_in: str,
    ):
        """Whatever directory the session was launched in.

        ``its-worktree`` is the case measured 2026-10-26: a session launched in
        its worktree gets that worktree as ``CLAUDE_PROJECT_DIR``.  The resolver
        that stood until 2026-10-27 answered it, and the worktree holds no
        venv (finding plan#1849).  ``a-decoy`` is a directory outside the
        repository, which the old cases used in the primary checkout's place.
        """
        main, linked = hooked
        launch = linked if launched_in == "its-worktree" else primary
        assert _bash(
            "hook_toolchain_root", linked, launch, library=_library(linked),
        ) == str(main)

    def test_from_a_directory_outside_any_repository(
        self, hooked: tuple[Path, Path], tmp_path: Path,
    ):
        """The session's working directory does not matter, only the library's.

        A session can stand in a directory outside every repository (a
        scratchpad, ``/tmp``); asking git from there found nothing and fell
        back to the launch directory, the defect itself (review of plan#1823).
        """
        main, linked = hooked
        elsewhere = tmp_path / "scratchpad"
        elsewhere.mkdir()
        assert _bash(
            "hook_toolchain_root", elsewhere, linked, library=_library(linked),
        ) == str(main)

    def test_from_the_main_checkout_it_answers_itself(
        self, hooked: tuple[Path, Path], primary: Path,
    ):
        """The first worktree git names is the main one, wherever it is asked.

        Asked from the main checkout's own copy while a linked worktree exists,
        so an answer read off the wrong entry of ``git worktree list`` names the
        linked one and fails here.
        """
        main, linked = hooked
        assert linked.is_dir()
        assert _bash(
            "hook_toolchain_root", main, primary, library=_library(main),
        ) == str(main)

    def test_the_two_roots_DISAGREE_from_a_worktree(
        self, hooked: tuple[Path, Path],
    ):
        """They must not collapse back into one answer.

        From a session launched in its own worktree, the tree being edited is
        that worktree and the toolchain is the main checkout's.  A future edit
        that re-unified the two resolvers would answer one of them wrong here.
        """
        main, linked = hooked
        both = _bash(
            "hook_repo_root; hook_toolchain_root", linked, linked,
            library=_library(linked),
        ).split("\n")
        assert both == [str(linked), str(main)]

    def test_the_PATH_prefix_is_the_main_checkouts_venv(
        self, hooked: tuple[Path, Path],
    ):
        """The consequence the resolver exists for, read off PATH itself.

        The main checkout holds ``.venv/bin``; the worktree the session was
        launched in holds none.  Measured 2026-10-27 before the fix: no prefix,
        so the gates ran the system's ``pylint``.
        """
        main, linked = hooked
        venv_bin = main / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        path = _bash(
            'printf "%s" "$PATH"', linked, linked, library=_library(linked),
        )
        assert path.split(":")[0] == str(venv_bin)

    def test_a_library_outside_any_checkout_falls_back_and_strips_a_slash(
        self, tmp_path: Path, primary: Path,
    ):
        """The old answer stands where git has none, HOOK/SH-12's strip with it.

        The trailing-slash strip was a property of the single helper; splitting
        it in two is exactly how one half quietly loses it, and git's own
        answer never carries a slash, so only the fallback can grade it.  The
        slash goes in as a string: a ``Path`` would drop it.
        """
        loose = tmp_path / "not-a-repo"
        loose.mkdir()
        library = loose / "_hooklib.sh"
        library.write_bytes(_HOOKLIB.read_bytes())
        assert _bash(
            "hook_toolchain_root", loose, f"{primary}/", library=library,
        ) == str(primary)


class TestTheEditedFileNormalizesAgainstThatRoot:
    """The consequence: an absolute path here is a SKIPPED gate."""

    def test_an_app_file_normalizes_RELATIVE_from_a_worktree(
        self, checkout: Path, primary: Path,
    ):
        """``app/foo.py``, not ``/abs/path/app/foo.py``.

        This is the assertion that maps to the live defect.  Every caller
        matches the result against ``app/*.py``; an absolute path matches
        nothing, so the hook exits 0 and the edit sails through unlinted.
        Pre-fix this returned the absolute path.
        """
        edited = checkout / "app" / "foo.py"
        edited.write_text("x = 1\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(edited)}})

        assert _bash(
            "hook_target_relpath", checkout, primary, stdin=payload,
        ) == "app/foo.py"

    def test_a_file_OUTSIDE_the_checkout_stays_absolute(
        self, checkout: Path, primary: Path, tmp_path: Path,
    ):
        """The skip that IS correct, so the fix does not over-reach.

        A file genuinely outside the project must still fall out of every
        caller's ``case``.  Keeping this arm is what stops the fix from turning
        "resolve the right root" into "match everything".
        """
        outside = tmp_path / "elsewhere.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(outside)}})

        assert _bash(
            "hook_target_relpath", checkout, primary, stdin=payload,
        ) == str(outside)
