"""
Shekel Budget App -- Schema Validation Tests

Tests each Marshmallow schema's load() method directly for:
  - Required field enforcement (missing → ValidationError)
  - Type coercion (string amounts → Decimal)
  - Range validation (amount >= 0, month 1-12, etc.)
  - @pre_load empty-string stripping
  - @validates_schema cross-field rules
"""
