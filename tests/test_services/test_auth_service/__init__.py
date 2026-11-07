"""
Shekel Budget App -- Auth Service Tests

Tests for password hashing, verification, user authentication,
user registration, account lockout (F-033 / C-11), and the HIBP
breached-password check (F-086 / C-11).  See ``§1.10`` of the test
plan for the original auth-service coverage; the lockout and HIBP
sections were added in commit C-11 of the 2026-04-15 security
remediation plan.
"""
