"""
Shekel Budget App -- Health Check Endpoint

Provides a lightweight health check for Docker HEALTHCHECK, load
balancers, and external monitoring.  Returns database connectivity
status as JSON.

This endpoint:
- Requires no authentication (external monitors must reach it).
- Is excluded from request logging (avoid noise from frequent checks).
- Is exempt from Flask-Limiter (Docker healthchecks fire from a single
  loopback IP every 30 seconds and would otherwise consume the per-IP
  budget that defends auth and mutating routes).  The exemption is
  enforced at the route level so it cannot be silently revoked by
  changing default_limits.
- Does not trigger audit log writes (SELECT 1 touches no audited tables).
"""

import logging

from flask import Blueprint, jsonify

from app.extensions import db
from app.rate_limits import limiter

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
@limiter.exempt
def health_check():
    """Return application and database health status.

    Returns:
        200 JSON: {"status": "healthy", "database": "connected"}
        500 JSON: {"status": "unhealthy", "database": "error"}

    The error response intentionally omits exception details to prevent
    information disclosure (connection strings, hostnames, credentials).
    Full details are logged server-side for operator diagnostics.
    """
    try:
        # Verify database connectivity with a lightweight query.
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "healthy", "database": "connected"}), 200
    # Pylint: ``broad-except`` -- A health endpoint must convert ANY failure
    # (DB connectivity, driver, connection-pool exhaustion) into a controlled
    # "unhealthy" response rather than propagate a 500.  The broad catch is
    # deliberate and is locked by tests/test_routes/test_health.py (which inject
    # a bare Exception); do not narrow it to SQLAlchemyError.
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Health check failed: %s", exc)
        # Do NOT include str(exc) in the response -- it may contain
        # database hostnames, ports, or credentials.  See audit M5.
        return jsonify({
            "status": "unhealthy",
            "database": "error",
        }), 500
