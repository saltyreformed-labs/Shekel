"""The script itself: its runs inherit no target from the test's environment, it is tracked in the
repository, it finds its running-check and its own parts through production's symlinks, it verifies
only main's build in the new home, and it resolves the :latest tag to its first digest.
"""

from __future__ import annotations

import re
import shutil

import pytest

from tests._repo_root import REPO_ROOT

from ._helpers import (
    _DEPLOY_PARTS, _DEPLOY_SCRIPT, _OLD, _NEW, _said, _NO_MIGRATIONS, _BUILT_BY,
)


class TestTheHarnessInheritsNoTarget:
    """The stack's runs read no ``SHEKEL_*`` variable from the test's environment.

    The script takes every target from one (``SHEKEL_ENV_FILE``,
    ``SHEKEL_DB_CONTAINER``, ...), and ``_Stack.run`` sets only some of them,
    so one exported in the shell that runs the suite would aim a deploy at
    whatever it names: a production pin, written by a test.
    """

    def test_an_exported_env_file_is_not_the_one_deployed(
        self, stack, tmp_path, monkeypatch,
    ):
        """A deploy with ``SHEKEL_ENV_FILE`` exported pins the stack's file, not that one."""
        decoy = tmp_path / "decoy.env"
        decoy.write_text(f"SHEKEL_IMAGE_DIGEST={_OLD}\n", encoding="utf-8")
        monkeypatch.setenv("SHEKEL_ENV_FILE", str(decoy))
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert decoy.read_text(encoding="utf-8") == f"SHEKEL_IMAGE_DIGEST={_OLD}\n", (
            _said(result, calls))


class TestTheScriptIsInTheRepository:
    """Finding F-14: the path that rolls production used to be untracked.

    It lived only on the host, so it sat outside every linter in the polyglot
    gate and nobody could review it -- which is why F-8 survived unnoticed.
    Kept from the retired source-level file; everything else that file
    asserted is now observed behaviourally by this package's other parts.
    """

    def test_the_canonical_copy_is_present_and_executable(self):
        """`deploy/shekel-deploy.sh` is tracked here and runnable."""
        assert _DEPLOY_SCRIPT.is_file(), (
            "deploy/shekel-deploy.sh is missing -- the production deploy path "
            "is untracked again, which is finding F-14."
        )
        assert _DEPLOY_SCRIPT.stat().st_mode & 0o111, (
            "deploy/shekel-deploy.sh is not executable"
        )


class TestTheLatestTagResolvesToItsFirstDigest:
    """The ``:latest`` path, where no digest is given: the first RepoDigest.

    Plan step balance:X-dm.  It read ``docker image inspect ... | grep -oE
    'sha256:...' | head -1`` under pipefail, and no test drove it, since
    every other test passes a digest.  Once ``head`` had its line, ``grep``
    could die writing the rest, and ``set -e`` then ended the run with no
    message of the script's own; a failed inspect, or an answer holding no
    digest, ended it the same way, so the refusal written for the second
    case never ran.
    """

    def test_the_latest_tag_deploys_its_digest(self, stack):
        """Pulled, resolved to its digest, and pinned."""
        result = stack.run(pull_latest=True, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert "pull -q ghcr.io/saltyreformed-labs/shekel:latest" in calls, (
            _said(result, calls)
        )

    def test_a_digest_list_larger_than_a_pipe_still_resolves(self, stack):
        """The FIRST digest is taken, however long the list after it.

        Every later line holds a digest too, so ``grep -oE`` had far more to
        write than a pipe holds once ``head -1`` had left: the replaced
        spelling failed this every run.
        """
        mirrors = "".join(
            f"registry.example/mirror{i}@sha256:{i:064x}\n"
            for i in range(20000)
        )
        result = stack.run(
            pull_latest=True, health="healthy",
            repo_digests=f"ghcr.io/saltyreformed-labs/shekel@{_NEW}\n" + mirrors,
            **_NO_MIGRATIONS,
        )
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)

    def test_a_failed_inspect_is_refused_in_the_script_s_own_words(self, stack):
        """Before the dump, the pin and any container."""
        result = stack.run(
            pull_latest=True, image_inspect_rc="1", **_NO_MIGRATIONS,
        )
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert (
            "docker image inspect ghcr.io/saltyreformed-labs/shekel:latest failed"
            in result.stdout + result.stderr
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)

    def test_an_answer_with_no_digest_is_refused_in_the_script_s_own_words(
        self, stack,
    ):
        """The refusal that ``set -e`` used to pre-empt now runs."""
        result = stack.run(pull_latest=True, repo_digests="", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert (
            "could not resolve digest for ghcr.io/saltyreformed-labs/shekel:latest"
            in result.stdout + result.stderr
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)


class TestOnlyMainsBuildInTheNewHomeVerifies:
    """The one signing identity ``cosign verify`` is handed (card L5).

    Every other test here passes ``--no-verify`` or stops before the verify,
    so no test drove the verify call, and the identity regexp it hands cosign
    was a literal nothing read.  cosign matches that regexp ANYWHERE in the
    certificate's SAN, so the unanchored form it replaced accepted a build of
    any ref.  A stub ``cosign`` records what it was handed, and the regexp is
    graded with ``re.search``, the search cosign makes; Go's engine and
    Python's agree on every SAN below (none ends in a newline, the one place
    their ``$`` differ).
    """

    def test_the_regexp_admits_main_s_build_and_refuses_every_other(
        self, stack,
    ):
        """One verify, of the target, accepting only the new home's main."""
        cosign_log = stack.root / "cosign.log"
        cosign = stack.bin_dir / "cosign"
        cosign.write_text(
            "#!/usr/bin/env bash\n"
            f"(IFS=$'\\t'; printf '%s\\n' \"$*\") >>'{cosign_log}'\n",
            encoding="utf-8",
        )
        cosign.chmod(0o755)
        result = stack.run(args=["--dry-run", _NEW], **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        verifies = [
            line.split("\t")
            for line in cosign_log.read_text(encoding="utf-8").splitlines()
        ]
        assert len(verifies) == 1, (verifies, _said(result, calls))
        verify = verifies[0]
        assert verify[0] == "verify" and verify[-1] == (
            f"ghcr.io/saltyreformed-labs/shekel@{_NEW}"
        ), verify
        regexp = verify[verify.index("--certificate-identity-regexp") + 1]
        cases = {
            f"{_BUILT_BY}@refs/heads/main": True,
            # Signed before the move: no second identity is accepted.
            "https://github.com/SaltyReformed/Shekel/.github/workflows/"
            "docker-publish.yml@refs/heads/main": False,
            f"{_BUILT_BY}@refs/heads/dev": False,
            f"{_BUILT_BY}@refs/tags/v1": False,
            # Case exact.
            f"{_BUILT_BY}@refs/heads/main".replace("/Shekel/", "/shekel/"): False,
            # Anchored at the start, then at the end.
            f"https://example.com/?{_BUILT_BY}@refs/heads/main": False,
            f"{_BUILT_BY}@refs/heads/main-x": False,
        }
        assert {
            san: re.search(regexp, san) is not None for san in cases
        } == cases, regexp


class TestTheRunningCheckIsReadFromTheScriptsCheckout:
    """Ruling R-BAL254: one running-check, found beside the script's checkout.

    ``scripts/_container_lib.sh`` holds it for this script and the backup
    scripts alike.  Production runs ``/usr/local/bin/shekel-deploy``, a
    chain of symlinks into the repository, so the script finds the helper
    through ``readlink -f`` of itself.  Every other test here runs the
    repository's copy in place, which is that path's end; these pin the
    chain that starts it, and what a copy without the helper does.

    Ruling plan#3544 split the script itself under 1,000 lines a file: its
    entry sources its parts, ``deploy/_*.sh``, from beside itself the same
    way, and a copy without one refuses the same way.
    """

    def test_the_parts_the_tests_carry_are_the_ones_the_entry_sources(self):
        """``_DEPLOY_PARTS`` is the set of files the entry's own directives name.

        Each part is sourced under a ``# shellcheck source=`` directive, the
        entry's statement of what it loads beside itself.  An empty glob would
        give the parametrized refusal below no case and the control a copy
        missing its parts, so the set is held to the entry's.
        """
        sourced = set(re.findall(
            r"^# shellcheck source=(_[a-z_]+\.sh)$",
            _DEPLOY_SCRIPT.read_text(encoding="utf-8"), re.MULTILINE,
        ))
        assert sourced == {part.name for part in _DEPLOY_PARTS}, sourced
        assert sourced, "the entry sources no part beside itself"

    def test_production_s_two_symlinks_find_the_helper(self, stack, tmp_path):
        """Run through two symlinks, as production does, it deploys.

        ``/usr/local/bin/shekel-deploy`` links to
        ``/opt/docker/scripts/shekel-deploy.sh``, which links into the
        repository: a script resolving only one hop would look for the
        helper beside ``/opt/docker/scripts`` and stop.
        """
        hop = tmp_path / "opt-docker-scripts" / "shekel-deploy.sh"
        hop.parent.mkdir()
        hop.symlink_to(_DEPLOY_SCRIPT)
        link = tmp_path / "usr-local-bin" / "shekel-deploy"
        link.parent.mkdir()
        link.symlink_to(hop)
        result = stack.run(script=link, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert f"running: {_DEPLOY_SCRIPT}\n" in result.stdout, (
            f"the startup report did not name the file the chain resolves "
            f"to.  {_said(result, calls)}"
        )

    def test_a_copy_without_the_helper_stops_before_touching_anything(
        self, stack, tmp_path,
    ):
        """No helper beside it: it refuses to start, naming itself and the file.

        The ruling's words: it "refuses to start, before it touches anything".
        Nothing runs first -- not the startup report, not docker -- so the
        refusal itself names the copy that is running.
        """
        copy = tmp_path / "hand-copy" / "deploy" / "shekel-deploy.sh"
        copy.parent.mkdir(parents=True)
        shutil.copy2(_DEPLOY_SCRIPT, copy)
        result = stack.run(script=copy, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert result.stdout == "", _said(result, calls)
        assert (
            f"ERROR: {copy} cannot load "
            f"{copy.parent}/../scripts/_container_lib.sh" in result.stderr
        ), _said(result, calls)
        assert calls == [], (
            f"the script ran docker before stopping.  {_said(result, calls)}"
        )
        assert stack.pin == _OLD, _said(result, calls)

    @pytest.mark.parametrize("missing", [part.name for part in _DEPLOY_PARTS])
    def test_a_copy_without_one_of_its_parts_stops_before_touching_anything(
        self, stack, tmp_path, missing,
    ):
        """The helper is beside it but one part is not: it refuses, naming both files.

        The part's refusal is the helper's: nothing runs first, not the
        startup report and not docker, so the line names the copy running.
        """
        copy = tmp_path / "partial" / "deploy" / "shekel-deploy.sh"
        copy.parent.mkdir(parents=True)
        shutil.copy2(_DEPLOY_SCRIPT, copy)
        for part in _DEPLOY_PARTS:
            if part.name != missing:
                shutil.copy2(part, copy.parent / part.name)
        helper = copy.parents[1] / "scripts" / "_container_lib.sh"
        helper.parent.mkdir()
        shutil.copy2(REPO_ROOT / "scripts" / "_container_lib.sh", helper)
        result = stack.run(script=copy, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert result.stdout == "", _said(result, calls)
        assert f"ERROR: {copy} cannot load {copy.parent}/{missing}," in result.stderr, (
            _said(result, calls))
        assert calls == [], (
            f"the script ran docker before stopping.  {_said(result, calls)}"
        )
        assert stack.pin == _OLD, _said(result, calls)

    def test_a_copy_that_carries_the_helper_deploys(self, stack, tmp_path):
        """The control: the same copy, with its parts and the helper laid out beside it."""
        copy = tmp_path / "carried" / "deploy" / "shekel-deploy.sh"
        copy.parent.mkdir(parents=True)
        shutil.copy2(_DEPLOY_SCRIPT, copy)
        for part in _DEPLOY_PARTS:
            shutil.copy2(part, copy.parent / part.name)
        helper = copy.parents[1] / "scripts" / "_container_lib.sh"
        helper.parent.mkdir()
        shutil.copy2(REPO_ROOT / "scripts" / "_container_lib.sh", helper)
        result = stack.run(script=copy, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
