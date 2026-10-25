"""What production runs at a commit (:mod:`tools.quill._production`): Docker's own reading of an
ignore file, and the files outside the image that build or run production.

**Every expectation of the reader below was measured on a REAL BuildKit build** (Docker
29.9.2, buildx 0.38.1, 2026-10-24, X-cx L8's S3b part 5): each case's ignore file and files
made a context, ``FROM scratch`` + ``COPY . /`` built it with ``--output type=local``, and
the files it copied are the ones each case keeps; each refused case's build failed.  The
documented cases are Docker's own examples (its documentation, "Build context",
``.dockerignore files``); the others pin what ``moby/patternmatcher``'s source does where the
documentation says nothing, the walk's parent results above all, where its other matcher
(``MatchesOrParentMatches``) would answer otherwise.  Git histories are throwaway
repositories under pytest's ``tmp_path``.
"""
from __future__ import annotations

import pytest

from tools.ci import scratch
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import commit_files as _files
from tools.ci.scratch import run as _run
from tools.quill import _production
from tools.quill._production import IgnoreFileError, changed, files, ignore_file, kept, rules


def _kept(text, names):
    """The names of ``names`` an ignore file of ``text`` leaves in the context."""
    return kept(rules(text), names)


#: Docker's documented examples: the ignore file, every file of the context, and the files a
#: build copies of it.
_DOCUMENTED = {
    # "*/temp*": names starting temp in any immediate subdirectory, file or directory;
    # "*/*/temp*": two levels below the root; "temp?": a one-character extension of temp in
    # the root; "# comment": ignored.
    "matching": ("# comment\n*/temp*\n*/*/temp*\ntemp?\n",
                 ["somedir/temporary.txt", "somedir/temp/inner.txt",
                  "somedir/subdir/temporary.txt", "tempa", "tempb", "temp", "tempab",
                  "temporary.txt", "a/b/c/temp.txt", "# comment", "somedir/keep.txt"],
                 {"# comment", "a/b/c/temp.txt", "somedir/keep.txt", "temp", "tempab",
                  "temporary.txt"}),
    # Leading and trailing slashes are disregarded: each of the four excludes foo/bar under
    # the root, and nothing else.
    "/foo/bar/": ("/foo/bar/\n", ["foo/bar/inner.txt", "foo/baz", "bar", "x/foo/bar"],
                  {"bar", "foo/baz", "x/foo/bar"}),
    "/foo/bar": ("/foo/bar\n", ["foo/bar/inner.txt", "foo/baz", "bar", "x/foo/bar"],
                 {"bar", "foo/baz", "x/foo/bar"}),
    "foo/bar/": ("foo/bar/\n", ["foo/bar/inner.txt", "foo/baz", "bar", "x/foo/bar"],
                 {"bar", "foo/baz", "x/foo/bar"}),
    "foo/bar": ("foo/bar\n", ["foo/bar/inner.txt", "foo/baz", "bar", "x/foo/bar"],
                {"bar", "foo/baz", "x/foo/bar"}),
    "foo/bar/, a file": ("foo/bar/\n", ["foo/bar", "foo/baz"], {"foo/baz"}),
    # "**/*.go" excludes every file ending .go anywhere.
    "**": ("**/*.go\n", ["a.go", "x/y/z.go", "x/a.go.txt", "x/go"], {"x/a.go.txt", "x/go"}),
    # "*.md" then "!README.md": the markdown right under the root, but README.md, is
    # excluded; markdown under subdirectories is still included.
    "!": ("*.md\n!README.md\n", ["a.md", "README.md", "docs/a.md", "docs/README.md"],
          {"README.md", "docs/README.md", "docs/a.md"}),
    # The last line that matches a file decides: README files but README-secret.md ...
    "last wins": ("*.md\n!README*.md\nREADME-secret.md\n",
                  ["a.md", "README.md", "README-x.md", "README-secret.md"],
                  {"README-x.md", "README.md"}),
    # ... and every README file when the exception comes last.
    "last wins, reordered": ("*.md\nREADME-secret.md\n!README*.md\n",
                             ["a.md", "README.md", "README-x.md", "README-secret.md"],
                             {"README-secret.md", "README-x.md", "README.md"}),
    # "For historical reasons, the pattern . is ignored."
    ".": (".\n", ["a", "b/c"], {"a", "b/c"}),
    # filepath.Clean trims whitespace and removes . and ..
    "cleaned": ("  ./foo/../bar  \n", ["bar/x", "foo/x", "foobar"], {"foo/x", "foobar"}),
}

#: What patternmatcher's source does where the documentation is silent, each as measured.
_SOURCE = {
    # The walk judges a path with its directory's results: "a" excludes a/, "!a/b" takes
    # a/b back, and the second "a", which named a/ only after the exception, does not
    # exclude a/b again (MatchesOrParentMatches would).
    "parent results": ("a\n!a/b\na\n", ["a/b", "a/c", "b"], {"a/b", "b"}),
    # A trailing ** after no wildcard: everything under foo/, and not foobar.
    "prefix": ("foo/**\n", ["foo/x", "foo/y/z", "foobar"], {"foobar"}),
    # A leading **/ before no other wildcard: temp at any depth, the root's included.
    "suffix": ("**/temp\n", ["temp", "a/temp", "a/b/temp", "a/xtemp", "b/temp/inner"],
               {"a/xtemp"}),
    # A byte order mark before the first line, and lines ending CR LF.
    "bom and crlf": ("﻿a\r\nb\r\n", ["a", "b", "c"], {"c"}),
    # A backslash escapes the next character.
    "escape": ("a\\*b\n", ["a*b", "axb"], {"axb"}),
    # Only a # in the first column makes a comment.
    "indented #": ("  #x\n", ["#x", "y"], {"y"}),
    # An exception with a wildcard, under an excluded directory.
    "wildcard exception": ("docs\n!docs/*.md\n", ["docs/a.md", "docs/b.txt", "docs/x/c.md"],
                           {"docs/a.md"}),
    # ? is one character other than /.
    "?": ("a?c\n", ["abc", "a/c", "ac"], {"a/c", "ac"}),
    # A class; a ! inside one is a character, not a negation.
    "class": ("[ab]x\n[!c]y\n", ["ax", "bx", "cx", "!y", "cy", "dy"], {"cx", "dy"}),
    # Leading slashes clean to one, which is then dropped (POSIX's normpath keeps two).
    "//": ("//foo\n", ["foo", "bar", "x/foo"], {"bar", "x/foo"}),
}


@pytest.mark.parametrize(("text", "names", "want"), list(_DOCUMENTED.values()),
                         ids=list(_DOCUMENTED))
def test_an_ignore_file_keeps_what_dockers_documentation_says(text, names, want):
    """Each of Docker's documented examples keeps exactly the files its build copied."""
    assert _kept(text, names) == want


@pytest.mark.parametrize(("text", "names", "want"), list(_SOURCE.values()), ids=list(_SOURCE))
def test_an_ignore_file_keeps_what_dockers_build_copied(text, names, want):
    """Each case the documentation is silent on keeps exactly the files its build copied."""
    assert _kept(text, names) == want


@pytest.mark.parametrize(("text", "why"), [
    ("!\n", 'a line "!" names no pattern'),
    ("! \n", 'a line "!" names no pattern'),
    ("foo\\\n", "ends in a lone backslash"),
    ("a[\n", "is not one Docker reads"),
], ids=["!", "! and a space", "trailing backslash", "unclosed class"])
def test_an_ignore_file_docker_refuses_is_refused(text, why):
    """A pattern Docker refuses makes no image (its build fails, measured for the lone ``!``,
    the trailing backslash and the unclosed class), so the reader refuses it, saying why."""
    with pytest.raises(IgnoreFileError, match=why):
        rules(text)


def test_no_ignore_file_keeps_every_file():
    """With no ignore file the context is the whole tree."""
    assert _kept("", ["a", "b/c", ".git-like"]) == {"a", "b/c", ".git-like"}


@pytest.mark.parametrize(("path", "runs"), [
    ("Dockerfile", True), ("docker-compose.yml", True), ("deploy/shekel-deploy.sh", True),
    ("deploy/nginx-shared/conf.d/shekel.conf", True), ("deploy/NOTES.md", True),
    ("deploy/README.md", False), ("deploy/postgres/README.md", False),
    ("docker-compose.dev.yml", False), ("docker-compose.build.yml", False),
    ("cloudflared/config.yml", False), ("deployed.txt", False), ("app/Dockerfile", False),
])
def test_the_files_outside_the_image_that_build_or_run_production(path, runs):
    """R-BAL400: the Dockerfile, the compose file production copies, and deploy/ whole but a
    README.md; never the dev or build compose files, the tunnel's template, or a name that
    only starts like one."""
    assert _production.runs_it(path) is runs


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    return scratch.repository(tmp_path)


def _executable(root, base, path):
    """A commit on ``base`` making ``path`` executable, nothing else; its sha."""
    _run(root, "checkout", "--quiet", "--detach", base)
    _run(root, "update-index", "--chmod=+x", "--", path)
    _run(root, "commit", "--quiet", "-m", f"chmod +x {path}")
    return _run(root, "rev-parse", "HEAD")


def test_the_dockerfiles_own_ignore_file_comes_first(repo):
    """``Dockerfile.dockerignore`` is read where the tree holds it, ``.dockerignore``
    otherwise, and neither leaves the text empty."""
    none = _files(repo, _commit(repo, "root"), "none", {"app.py": "x\n"})
    root = _files(repo, none, "root's", {".dockerignore": "tools/\n"})
    own = _files(repo, root, "the Dockerfile's", {"Dockerfile.dockerignore": "docs/\n"})
    assert [ignore_file(repo, sha) for sha in (none, root, own)] == ["", "tools/\n", "docs/\n"]


def test_what_production_runs_at_a_commit(repo):
    """The files the image holds, by mode and object, and the Dockerfile and deploy/ although
    the ignore file excludes them; not deploy's README."""
    sha = _files(repo, _commit(repo, "root"), "base", {
        ".dockerignore": "Dockerfile\n.dockerignore\ndeploy/\ntools/\n", "Dockerfile": "FROM x\n",
        "app/a.py": "a\n", "tools/t.py": "t\n", "deploy/run.sh": "r\n", "deploy/README.md": "d\n"})
    found = files(repo, sha)
    assert sorted(found) == ["Dockerfile", "app/a.py", "deploy/run.sh"]
    assert found["app/a.py"] == f"100644 {_run(repo, 'rev-parse', f'{sha}:app/a.py')}"


def test_production_changes_where_its_files_differ_and_nowhere_else(repo):
    """An edit, an add, a delete and a mode change of a file the image holds each change
    production; so do an ignore-file edit moving a file in or out of the image and an edit to
    the Dockerfile the image excludes.  An edit to a file the image excludes, to deploy's
    README, or to the ignore file's comments changes nothing."""
    ignored = "Dockerfile\n.dockerignore\ndeploy/\ntools/\ndocs/\n"
    base = _files(repo, _commit(repo, "root"), "base", {
        ".dockerignore": f"# the image\n{ignored}",
        "Dockerfile": "FROM x\n", "app/a.py": "a\n", "app/gone.py": "g\n", "tools/t.py": "t\n",
        "docs/d.md": "d\n", "deploy/README.md": "r\n"})
    edits = {
        "an edit": {"app/a.py": "a2\n"},
        "an add": {"app/new.py": "n\n"},
        "a delete": {"app/gone.py": None},
        "tools/ taken in": {".dockerignore": f"# the image\n{ignored.replace('tools/', '')}"},
        "app/ taken out": {".dockerignore": f"# the image\n{ignored}app/\n"},
        "the Dockerfile": {"Dockerfile": "FROM y\n"},
        "a tools edit": {"tools/t.py": "t2\n"},
        "deploy's README": {"deploy/README.md": "r2\n"},
        "a comment": {".dockerignore": f"# what the image holds\n{ignored}"},
    }
    found = {name: changed(repo, base, _files(repo, base, name, edit))
             for name, edit in edits.items()}
    assert found == {
        "an edit": ("app/a.py",), "an add": ("app/new.py",), "a delete": ("app/gone.py",),
        "tools/ taken in": ("tools/t.py",), "app/ taken out": ("app/a.py", "app/gone.py"),
        "the Dockerfile": ("Dockerfile",), "a tools edit": (), "deploy's README": (),
        "a comment": ()}
    assert changed(repo, base, _executable(repo, base, "app/a.py")) == ("app/a.py",)


def test_an_ignore_file_docker_refuses_refuses_the_comparison(repo):
    """A commit whose ignore file Docker refuses builds no image, so the comparison refuses
    rather than judge one."""
    base = _files(repo, _commit(repo, "root"), "base", {"app/a.py": "a\n"})
    broken = _files(repo, base, "broken", {".dockerignore": "!\n"})
    with pytest.raises(IgnoreFileError):
        changed(repo, base, broken)
