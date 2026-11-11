"""``deploy/shekel-deploy.sh`` is DRIVEN, and its safety properties observed.

Plan step **R-F8**, findings **F-8** and **F-14**, ruling **R-R14**.

**Why this file replaced a source-level one.**  The first attempt at gating
R-F8 asserted the ORDER of the script's steps by locating substrings in its
text, on the premise that a behavioural control "needs Docker, two real GHCR
images with different migration sets, and about ninety seconds -- it cannot
run in CI".  Adversarial review demolished both halves.  The premise is false:
every target in the script is an environment override by deliberate design and
every external command resolves through ``PATH``, so a stub ``docker`` drives
the real branch logic in about a second with no daemon at all.  And the
consequence of the premise was worse -- of 45 mutations, **24 stayed green**,
including one that replaced the entire script with a 33-line do-nothing stub
whose ``take_predeploy_dump`` body was a comment reading ``# die die die die``.
Every assertion was a substring-position check, so any file with the right
bytes in the right order satisfied all of them.  Among the mutations that
passed: inverting the migration guard so a migration-bearing release re-pins
(finding F-8, restored verbatim), and making the dump a no-op.

So this file observes OUTCOMES instead: which commands ran and in what order,
what the pin says afterwards, whether a dump exists on disk, and the exit
status.  None of that can be satisfied by a comment.

**The stub.**  ``_write_fake_docker`` emits a ``docker`` shell script onto a
``PATH`` prefix.  It answers the handful of subcommands the deploy script
issues, logs every invocation so ordering is observable, and is scripted per
test: which migrations each image "contains" (a listing FILE per image, since
the regression below needs a listing past 128 KiB, the most one environment
string may hold), and through environment variables whether the database
container is "running", whether ``pg_dump`` writes a readable archive, and
whether the container ever reports healthy.  Nothing here touches a real
daemon, so these tests are NOT ``@pytest.mark.docker`` and run in CI like any
other.
"""
