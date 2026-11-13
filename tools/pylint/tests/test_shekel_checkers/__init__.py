"""Unit tests for the Shekel project-specific pylint checkers.

Each checker is exercised against AST snippets built with ``astroid.extract_node``
and verified with pylint's ``CheckerTestCase`` harness. Every positive case
(the antipattern is flagged) is paired with the corresponding negative case (the
legitimate form is NOT flagged), because a checker that over-fires creates the
cargo-cult-disable noise the rules exist to prevent.

The package-privacy checker's physical-membership primitive,
``_importer_file_inside``, is tested DIRECTLY: its placeholder-path guards have
no other discriminating observer.  It is internal to the checker module rather
than a package re-export, so it is imported from its home,
``shekel_checkers.package_privacy``.
"""
