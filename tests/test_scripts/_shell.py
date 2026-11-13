"""What the shell-script tests share: a script's text, read in pieces, run under its own flags.

The operations scripts (``scripts/restore.sh``, ``scripts/verify_backup.sh`` and the
others ``test_ops_shell_readers.py`` grades) drop or write real state, so their tests
never run a script whole: each reads a function, a configuration block or a ``trap``
statement out of the script's text and runs that, against a stub ``docker``
(:func:`docker_stub`) whose ``DOCKER_HOST`` names no daemon.
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path


def function_text(script: Path, name: str) -> str:
    """Return shell function *name*'s definition from *script*, verbatim.

    A definition opens with ``name() {`` at the start of a line and closes
    with the first ``}`` alone at the start of a line, which is how every
    top-level function in these scripts is written (shfmt enforces it).

    Args:
        script: The script holding the function.
        name: The function's name.

    Returns:
        The definition, from its first line through its closing brace.
    """
    found = re.findall(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
        script.read_text(encoding="utf-8"), flags=re.S | re.M,
    )
    assert len(found) == 1, (
        f"expected one definition of {name}() in {script}, found {len(found)}"
    )
    return found[0]


def every_function_text(script: Path) -> str:
    """Return every top-level function definition in *script*, verbatim, in order.

    Each is matched as :func:`function_text` matches one.

    Args:
        script: The script holding the functions.

    Returns:
        The definitions, concatenated.
    """
    found = re.findall(
        r"^\w+\(\) \{\n.*?^\}\n",
        script.read_text(encoding="utf-8"), flags=re.S | re.M,
    )
    assert found, f"found no function definitions in {script}"
    return "".join(found)


def section_text(script: Path, start: str, end: str | None) -> str:
    """Return *script*'s lines from the one opening with *start* to the one opening with *end*.

    Args:
        script: The script holding the section.
        start: How the section's first line opens.
        end: How the line after its last opens, or ``None`` for a section
            that runs to the end of the script.

    Returns:
        The section, *end*'s line excluded.
    """
    stop = r"\Z" if end is None else rf"^{re.escape(end)}"
    found = re.findall(
        rf"^{re.escape(start)}.*?(?={stop})",
        script.read_text(encoding="utf-8"), flags=re.S | re.M,
    )
    assert len(found) == 1, (
        f"expected one section from {start!r} to {end!r} in {script}, "
        f"found {len(found)}"
    )
    return found[0]


def trap_line(script: Path) -> str:
    """Return *script*'s one top-level ``trap`` statement, verbatim.

    Args:
        script: The script holding it.

    Returns:
        The line.
    """
    found = re.findall(
        r"^trap .*$", script.read_text(encoding="utf-8"), flags=re.M,
    )
    assert len(found) == 1, (
        f"expected one top-level trap in {script}, found {len(found)}"
    )
    return found[0]


def run_bash(program: str, *args: str, path_prefix: Path | None = None,
             ) -> subprocess.CompletedProcess:
    """Run *program* under the swept scripts' own ``set -euo pipefail``.

    A function its script calls inside a command substitution is called the
    same way here, since bash runs that subshell WITHOUT errexit: grading it
    bare under ``set -e`` would grade a context its callers never give it.

    With stubs, ``DOCKER_HOST`` names a socket that does not exist, so a
    ``docker`` that is not the stub reaches no daemon.

    Args:
        program: Bash source, reading its inputs from ``$1``, ``$2`` ...
        args: The positional parameters.
        path_prefix: A directory to put first on ``PATH`` (the stubs).

    Returns:
        The completed process, stdout and stderr captured.
    """
    env = None
    if path_prefix is not None:
        env = {
            "PATH": f"{path_prefix}:/usr/bin:/bin",
            "DOCKER_HOST": f"unix://{path_prefix}/no-daemon.sock",
        }
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{program}", "_", *args],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )


def docker_stub(bin_dir: Path, *, arms: str,
                labels: dict[str, str] | None = None) -> Path:
    """Install a ``docker`` stub, and a ``sleep`` that returns at once.

    Each call's arguments are logged twice: joined by spaces in
    ``docker.log``, and each in brackets in ``argv.log``, which keeps the
    word boundaries the first loses.  An ``inspect`` whose format names
    ``com.docker.compose.<key>`` labels prints each one's value from
    *labels*, a line each in the order the format names them, and an empty
    line for a key *labels* lacks, as docker prints a missing label.  Every
    other call runs the first of *arms* its arguments match, else exits 0
    printing nothing.

    Args:
        bin_dir: Directory placed first on ``PATH``; made here.
        arms: Bash ``case`` arms matched against ``"$*"``, each ending ``;;``.
        labels: The compose labels any inspected container carries, by key
            (``project``, ``project.working_dir``, ...).

    Returns:
        The ``docker.log`` path.
    """
    bin_dir.mkdir()
    log = bin_dir / "docker.log"
    argv_log = bin_dir / "argv.log"
    for key, value in (labels or {}).items():
        (bin_dir / f"label.{key}").write_text(value, encoding="utf-8")
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{log}'
        printf '[%s]' "$@" >>'{argv_log}'
        echo >>'{argv_log}'
        case "$*" in
            *com.docker.compose.*)
                for key in $(printf '%s' "$*" | grep -o 'com\\.docker\\.compose\\.[a-z_.]*'); do
                    file='{bin_dir}/label.'"${{key#com.docker.compose.}}"
                    if [ -f "$file" ]; then cat "$file"; fi
                    echo
                done
                exit 0 ;;
        """) + textwrap.indent(textwrap.dedent(arms).strip("\n"), "    ") + "\nesac\nexit 0\n"
    (bin_dir / "docker").write_text(script, encoding="utf-8")
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n",
                                   encoding="utf-8")
    for name in ("docker", "sleep"):
        (bin_dir / name).chmod(0o755)
    return log
