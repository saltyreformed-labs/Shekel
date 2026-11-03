"""What production runs at a commit (:mod:`tools.quill._production`): Docker's own reading of an
ignore file, and the files outside the image that build or run production (the publish
workflow among them, ``R-BAL401``).

**Every expectation of the reader below but one was measured on a REAL BuildKit build**
(Docker 29.9.2, buildx 0.38.1, 2026-10-24, X-cx L8's S3b part 5): each case's ignore file and
files made a context, ``FROM scratch`` + ``COPY . /`` built it with ``--output type=local``,
and the files it copied are the ones each case keeps; each refused case's build failed.  The
one not built is the refusal of ``"! "``, which ``ReadAll`` trims to the lone ``!`` before
it judges it, the path the built ``"!"`` case takes.  The
documented cases are Docker's own examples (its documentation, "Build context",
``.dockerignore files``); the others pin what ``moby/patternmatcher``'s source does where the
documentation says nothing, the walk's parent results above all, where its other matcher
(``MatchesOrParentMatches``) would answer otherwise.  Git histories are throwaway
repositories under pytest's ``tmp_path``.
"""
from __future__ import annotations

import pytest

from tools.ci import arcs, scratch
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
    (".github/workflows/docker-publish.yml", True), ("scripts/_container_lib.sh", True),
    ("deploy/README.md", False), ("deploy/postgres/README.md", False),
    ("docker-compose.dev.yml", False), ("docker-compose.build.yml", False),
    ("cloudflared/config.yml", False), ("deployed.txt", False), ("app/Dockerfile", False),
    (".github/workflows/ci.yml", False), ("docker-publish.yml", False),
    ("scripts/_backup_lib.sh", False),
])
def test_the_files_outside_the_image_that_build_or_run_production(path, runs):
    """R-BAL400: the Dockerfile, the compose file production copies, and deploy/ whole but a
    README.md, with the helper the deploy script sources (R-BAL254: the deploy script is two
    files); R-BAL401: the workflow that builds and signs the image.  Never the dev or build
    compose files, the tunnel's template, another workflow, the backup scripts' helper (no
    production path loads it), or a name that only starts or ends like one."""
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
    """The files the image holds, by mode and object, and the Dockerfile, deploy/ and the
    publish workflow although the ignore file excludes them; not deploy's README, nor another
    workflow."""
    sha = _files(repo, _commit(repo, "root"), "base", {
        ".dockerignore": "Dockerfile\n.dockerignore\ndeploy/\ntools/\n.github/\n",
        "Dockerfile": "FROM x\n", "app/a.py": "a\n", "tools/t.py": "t\n", "deploy/run.sh": "r\n",
        "deploy/README.md": "d\n", _production.PUBLISH: "on: push\n",
        ".github/workflows/ci.yml": "on: pull_request\n"})
    found = files(repo, sha)
    assert sorted(found) == [_production.PUBLISH, "Dockerfile", "app/a.py", "deploy/run.sh"]
    assert found["app/a.py"] == f"100644 {_run(repo, 'rev-parse', f'{sha}:app/a.py')}"


def test_production_changes_where_its_files_differ_and_nowhere_else(repo):
    """An edit, an add, a delete and a mode change of a file the image holds each change
    production; so do an ignore-file edit moving a file in or out of the image, and an edit
    to the Dockerfile or to the publish workflow, a comment there included, which the image
    excludes.  An edit to a file the image excludes, to another workflow, to deploy's README,
    or to the ignore file's comments changes nothing."""
    ignored = "Dockerfile\n.dockerignore\ndeploy/\ntools/\ndocs/\n.github/\n"
    publish = _production.PUBLISH
    base = _files(repo, _commit(repo, "root"), "base", {
        ".dockerignore": f"# the image\n{ignored}",
        "Dockerfile": "FROM x\n", "app/a.py": "a\n", "app/gone.py": "g\n", "tools/t.py": "t\n",
        "docs/d.md": "d\n", "deploy/README.md": "r\n", publish: "on: push\n",
        ".github/workflows/ci.yml": "on: pull_request\n"})
    edits = {
        "an edit": {"app/a.py": "a2\n"},
        "an add": {"app/new.py": "n\n"},
        "a delete": {"app/gone.py": None},
        "tools/ taken in": {".dockerignore": f"# the image\n{ignored.replace('tools/', '')}"},
        "app/ taken out": {".dockerignore": f"# the image\n{ignored}app/\n"},
        "the Dockerfile": {"Dockerfile": "FROM y\n"},
        "the publish workflow": {publish: "on: [push, workflow_dispatch]\n"},
        "a publish comment": {publish: "# builds the image\non: push\n"},
        "a tools edit": {"tools/t.py": "t2\n"},
        "another workflow": {".github/workflows/ci.yml": "on: merge_group\n"},
        "deploy's README": {"deploy/README.md": "r2\n"},
        "a comment": {".dockerignore": f"# what the image holds\n{ignored}"},
    }
    found = {name: tuple(changed(repo, base, _files(repo, base, name, edit)))
             for name, edit in edits.items()}
    assert found == {
        "an edit": ("app/a.py",), "an add": ("app/new.py",), "a delete": ("app/gone.py",),
        "tools/ taken in": ("tools/t.py",), "app/ taken out": ("app/a.py", "app/gone.py"),
        "the Dockerfile": ("Dockerfile",), "the publish workflow": (publish,),
        "a publish comment": (publish,), "a tools edit": (), "another workflow": (),
        "deploy's README": (), "a comment": ()}
    assert tuple(changed(repo, base, _executable(repo, base, "app/a.py"))) == ("app/a.py",)


#: Actions the publish workflow loads, one through another, and actions it does not.
_ACTIONS = {
    ".dockerignore": ".dockerignore\n.github/\n",
    _production.PUBLISH: ("jobs:\n  b:\n    steps:\n      - uses: $/.github/actions/lock\n"
                          "      - uses: './.github/actions/empty'  # holds no action\n"
                          "      - run: echo .github/actions/named\n"),
    ".github/actions/lock/action.yml": "runs:\n  steps:\n    - uses: \"./.github/actions/in/\"\n",
    ".github/actions/in/action.yaml": "runs:\n  steps:\n    - uses: $/.github/actions/lock\n",
    ".github/actions/empty/README.md": "x\n",
    ".github/actions/named/action.yml": "runs: {}\n",
    ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - uses: $/.github/actions/ci\n",
    ".github/actions/ci/action.yml": "runs: {}\n",
}


def test_each_action_the_publish_workflow_loads_runs_production(repo):
    """Each action of the repository's that the publish workflow loads by a step's ``uses:``,
    in either form, quoted or not, and each that one loads in turn, in whichever file GitHub
    reads it from, though the ignore file excludes them (plan#2482); one loading the other
    back is read once.  Not a ``uses:`` of a directory holding no action, an action whose
    directory alone the workflow's text names (its file named would count, as any file
    named does, ruling plan#2573), nor one only another workflow loads."""
    sha = _files(repo, _commit(repo, "root"), "base", _ACTIONS)
    assert sorted(files(repo, sha)) == [
        ".github/actions/in/action.yaml", ".github/actions/lock/action.yml",
        _production.PUBLISH]


def test_an_action_changes_production_while_the_publish_workflow_loads_it(repo):
    """An edit to an action the publish workflow loads changes production, and one to an
    action only another workflow loads nothing; the workflow dropping its ``uses:`` changes
    the workflow and takes each action it loaded out of production."""
    base = _files(repo, _commit(repo, "root"), "base", _ACTIONS)
    lock, inner = ".github/actions/lock/action.yml", ".github/actions/in/action.yaml"
    edits = {"a loaded action": {lock: _ACTIONS[lock] + "# moved\n"},
             "another workflow's": {".github/actions/ci/action.yml": "runs: {x: 1}\n"},
             "dropped": {_production.PUBLISH: "jobs: {}\n"}}
    found = {name: tuple(changed(repo, base, _files(repo, base, name, edit)))
             for name, edit in edits.items()}
    assert found == {"a loaded action": (lock,), "another workflow's": (),
                     "dropped": (inner, lock, _production.PUBLISH)}


def test_the_release_s_tax_law_gate_runs_install_lock():
    """This repository's own commit: the publish workflow's tax-law gate, which decides
    whether the image is built, installs the lock by ``.github/actions/install-lock``, so an
    edit to it changes production (plan#2482)."""
    assert ".github/actions/install-lock/action.yml" in files(arcs.REPO, "HEAD")


def test_each_action_the_release_loads_runs_production(repo):
    """The publish workflow and the three actions it loads as this checkout holds them,
    committed to a scratch repository whose ignore file excludes ``.github/`` and itself: the
    install action the release's tax-law gate runs takes its uv from
    ``.github/actions/dockerfile-uv`` (plan#2484), and the release builds its image by
    ``.github/actions/build-image`` (ruling plan#2752), so an edit to either changes
    production too.  The checkout's files, not HEAD's, so the commit that changes the wiring
    is graded before it is made."""
    names = (_production.PUBLISH, ".github/actions/install-lock/action.yml",
             ".github/actions/dockerfile-uv/action.yml",
             ".github/actions/build-image/action.yml")
    held = {name: (arcs.REPO / name).read_text(encoding="utf-8") for name in names}
    sha = _files(repo, _commit(repo, "root"), "base",
                 {".dockerignore": ".dockerignore\n.github/\n", **held})
    assert sorted(files(repo, sha)) == sorted(names)


#: A publish workflow naming files each way the reading counts and each way it does not, an
#: action it loads naming one, another workflow naming one, and the files, which the image
#: excludes.
_NAMES = {
    ".dockerignore": ".dockerignore\n.github/\nscripts/\ndocs/\ntools/\n.tool-config\n",
    _production.PUBLISH: (
        "# the release: scripts/yaml_comment.py\n"
        "jobs:\n  b:\n    steps:\n"
        "      - uses: $/.github/actions/lock\n"
        "      - uses: some/build@v1\n"
        "        with:\n          file: tools/input.txt\n"
        "      - name: Run scripts/step_name.py\n"
        "        run: python scripts/run.py refuse\n"
        "      - run: |\n"
        "          # a shell comment: scripts/shell_comment.py\n"
        "          epoch=\"$(python ./scripts/dotted.py --starts-epoch)\"\n"
        "          bash \"${GITHUB_WORKSPACE}/scripts/workspace.sh\"\n"
        "          python \"$GITHUB_WORKSPACE/scripts/bare.py\"\n"
        "          cat .tool-config\n"
        "          echo \"${#v}\" scripts/after_count.py\n"
        "          curl https://x.example/#frag -o scripts/after_url.py\n"
        "          echo \"see docs/echoed.md.\"\n"
        "          echo ok  # scripts/trailing.py\n"
        "          python scripts/missing.py\n"),
    ".github/actions/lock/action.yml": "runs:\n  steps:\n    - run: python scripts/action.py\n",
    ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: python scripts/ci.py\n",
    "scripts/run.py": "print('scripts/by_named.py')\n",
    **{name: f"{name}\n" for name in (
        "tools/input.txt", "scripts/step_name.py", "scripts/dotted.py", "scripts/workspace.sh",
        "scripts/bare.py", ".tool-config", "scripts/after_count.py", "scripts/after_url.py",
        "docs/echoed.md", "scripts/action.py", "scripts/yaml_comment.py",
        "scripts/shell_comment.py", "scripts/trailing.py", "scripts/ci.py",
        "scripts/by_named.py")},
}


def test_each_file_the_publish_workflow_names_outside_a_comment_runs_production(repo):
    """Each file of the tree that the publish workflow or an action it loads names outside a
    comment, though the ignore file excludes it (ruling plan#2573): in a step's input, its
    name, a ``run:`` line, with a leading ``./``, after a variable's ``/`` braced or bare, a
    dotfile, after a ``#`` no whitespace precedes (``${#v}``, a URL's fragment), and ending
    a sentence in an ``echo``.  Not one named in a YAML comment line, a shell comment line or
    after a ``#`` that follows whitespace, one only another workflow names, or one only a
    named file names; and what the workflow reaches holds no path the tree does not."""
    sha = _files(repo, _commit(repo, "root"), "base", _NAMES)
    reaches = [".github/actions/lock/action.yml", ".tool-config", "docs/echoed.md",
               "scripts/action.py", "scripts/after_count.py", "scripts/after_url.py",
               "scripts/bare.py", "scripts/dotted.py", "scripts/run.py", "scripts/step_name.py",
               "scripts/workspace.sh", "tools/input.txt"]
    assert sorted(files(repo, sha)) == sorted([*reaches, _production.PUBLISH])
    assert sorted(_production.reached(repo, _production.tree(repo, sha),
                                      _production.PUBLISH)) == reaches


def test_a_named_file_changes_production_while_the_publish_workflow_names_it(repo):
    """An edit to a file the publish workflow or its action names changes production; one to
    a file named only in a comment, only by another workflow or only by a named file changes
    nothing; and the workflow dropping its name changes the workflow and takes the file out
    of production."""
    base = _files(repo, _commit(repo, "root"), "base", _NAMES)
    publish = _NAMES[_production.PUBLISH]
    assert publish.count("python scripts/run.py refuse") == 1
    edits = {"a named file": {"scripts/run.py": "print(1)\n"},
             "the action's": {"scripts/action.py": "a2\n"},
             "a commented file": {"scripts/trailing.py": "t2\n"},
             "another workflow's": {"scripts/ci.py": "c2\n"},
             "a named file's": {"scripts/by_named.py": "b2\n"},
             "the name dropped": {_production.PUBLISH: publish.replace(
                 "python scripts/run.py refuse", "echo done")}}
    found = {name: tuple(changed(repo, base, _files(repo, base, name, edit)))
             for name, edit in edits.items()}
    assert found == {"a named file": ("scripts/run.py",), "the action's": ("scripts/action.py",),
                     "a commented file": (), "another workflow's": (), "a named file's": (),
                     "the name dropped": (_production.PUBLISH, "scripts/run.py")}


def test_the_release_s_tax_law_gate_runs_its_script(repo):
    """The publish workflow, its two actions, the ignore file and the tax-law script as this
    checkout holds them, committed to a scratch repository: the image excludes the script, and
    the gate that decides whether the image is built runs it, so an edit to it changes
    production (task plan#2549, ruling plan#2573).  The checkout's files, not HEAD's, so the
    commit that changes the wiring is graded before it is made."""
    script = "scripts/check_tax_law.py"
    names = (_production.PUBLISH, ".github/actions/install-lock/action.yml",
             ".github/actions/dockerfile-uv/action.yml", ".dockerignore", script)
    held = {name: (arcs.REPO / name).read_text(encoding="utf-8") for name in names}
    sha = _files(repo, _commit(repo, "root"), "base", held)
    assert not kept(rules(held[".dockerignore"]), [script])
    edited = _files(repo, sha, "an edit", {script: f"{held[script]}# moved\n"})
    assert tuple(changed(repo, sha, edited)) == (script,)


def test_an_ignore_file_docker_refuses_refuses_the_comparison(repo):
    """A commit whose ignore file Docker refuses builds no image, so the comparison refuses
    rather than judge one."""
    base = _files(repo, _commit(repo, "root"), "base", {"app/a.py": "a\n"})
    broken = _files(repo, base, "broken", {".dockerignore": "!\n"})
    with pytest.raises(IgnoreFileError):
        changed(repo, base, broken)


def test_each_change_names_the_file_on_each_side(repo):
    """Each path where production differs names its mode and object on each side, None on
    the side production holds no such file: what the landing check reads to judge a change
    moving versions alone (:mod:`tools.quill._versions`)."""
    base = _files(repo, _commit(repo, "root"), "base", {"app/a.py": "a\n", "app/gone.py": "g\n"})
    edited = _files(repo, base, "edit", {"app/a.py": "a2\n", "app/gone.py": None,
                                         "app/new.py": "n\n"})

    def held(sha, path):
        """``path``'s mode and object at ``sha``, as production names them."""
        return f"100644 {_run(repo, 'rev-parse', f'{sha}:{path}')}"

    assert changed(repo, base, edited) == {
        "app/a.py": (held(base, "app/a.py"), held(edited, "app/a.py")),
        "app/gone.py": (held(base, "app/gone.py"), None),
        "app/new.py": (None, held(edited, "app/new.py"))}
