"""Tests for remediation Commit C-36: Dockerfile + image supply-chain refresh.

Covers audit findings:

    F-025  OpenSSL packages in container image have available updates
    F-060  Container image pins to ``:latest`` tag
    F-062  Two HIGH OS CVEs with no fix available in container image
    F-120  pip CVE in container (build-time only)
    F-155  No Cosign / image-signature verification
    BAL-653  Compose images pinned by tag alone (ledger finding, plan step X-em;
             :class:`TestEveryComposeImageIsPinnedByDigest`, which lists the
             compose files with ``git ls-files``)
    M01    The process clock runs UTC while the screens run New York (parity
           audit finding; :class:`TestDockerfilePinsTheAppsTimezone`, rulings
           plan#3807, plan#3811)

The artifacts under audit are the multi-stage Dockerfile, the
production compose override at ``deploy/docker-compose.prod.yml``,
``.github/workflows/docker-publish.yml`` (CI signing pipeline),
and ``.gitignore`` (private-key exclusion).  Since plan step balance:X-dj
it also holds the runtime stage to psycopg's C implementation (ruling
balance:R-BAL209, :class:`TestDockerfileRequiresPsycopgC`).

Tests are filesystem/text-based: the fast unit suite must not depend
on a live Docker daemon, GHCR network, or installed Cosign binary.
A separate subprocess test (``TestComposeOverrideRequiresDigest``)
runs ``docker compose config`` to exercise the merged interpolation
behaviour and skips when Docker is not available.

The runtime-behaviour tests (``cosign verify`` against a real signed
image, trivy scan delta) are the manual verification step in the
remediation plan and are not automated here -- automating them
would require a valid signing key on the test host and a full
re-pull of the multi-megabyte image on every suite run.
"""
