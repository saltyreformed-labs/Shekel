"""Run git in ONE repository: how the modules of ``tools/ci`` and ``tools/quill`` start git.

Every call runs ``git -C <root>`` through :func:`run`, with the variables that
bind git to some OTHER repository removed, so ``root`` alone decides which
repository is read or written.  The card-trailer rules (:mod:`tools.ci.trailers`),
the tracker tool's own git calls (``tools/quill/_git.py``) and the tests'
throwaway repositories (:mod:`tools.ci.scratch`) all start git here, but for one
call: ``scratch._prove_bound``'s control, which must reach the binding this module
removes, to prove a test's sentinel is bound.  Moved here from what is now
``tools/quill/_git.py`` by step X-cx's L4.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

#: What no user setting may add to git's output (``log.showSignature`` puts gpg
#: lines into a formatted log, which would split a record).
_PLAIN = ("-c", "log.showSignature=false")

#: A full commit id, SHA-1 or SHA-256.
FULL_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class GitError(RuntimeError):
    """A git command failed; carries its stderr."""


@functools.cache
def _binding_variables() -> frozenset[str]:
    """The environment variables that bind a git process to ONE repository, as git lists them.

    **A hook exports them, and they override ``-C``.**  Git runs a hook from a
    linked worktree with ``GIT_DIR`` and ``GIT_INDEX_FILE`` set to that
    worktree's (measured 2026-10-04), and with ``GIT_DIR`` set, ``git -C
    <root>`` no longer decides which repository a command reads or writes.
    That day the tracker tool's tests, run by the pre-commit hook from a linked
    worktree, moved the shared repository's ``dev`` and wrote
    ``core.bare=true`` into its config.  Git names the set itself (``rev-parse
    --local-env-vars``, the list it clears before entering a submodule), so no
    copy of it is kept here; printing the list reads no repository.
    """
    done = subprocess.run(["git", "rev-parse", "--local-env-vars"],
                          capture_output=True, text=True, check=False)
    if done.returncode or not done.stdout.strip():
        raise GitError(f"git rev-parse --local-env-vars: {done.stderr.strip()}")
    return frozenset(done.stdout.split())


def run(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one git command in ``root`` and in no other repository; the finished process.

    The environment it inherits loses :func:`_binding_variables`, so whatever
    the caller is bound to, ``root`` is the repository touched.  ``stdin`` is
    the text the command reads (``git mktree``, ``git hash-object --stdin``);
    None leaves it the caller's own, as every call was before ``stdin`` existed.
    """
    binding = _binding_variables()
    return subprocess.run(
        ["git", *_PLAIN, "-C", str(root), *args], capture_output=True, text=True, check=False,
        input=stdin,
        env={name: value for name, value in os.environ.items() if name not in binding},
    )


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    """Run one git command in ``root``, reading ``stdin`` (:func:`run`); its standard output.

    Raises:
        GitError: when git exits non-zero, with its stderr.
    """
    done = run(root, *args, stdin=stdin)
    if done.returncode:
        raise GitError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout
