"""
Shekel Budget App -- Unit Tests for Paycheck Calculator

Tests the recurring raise compounding logic -- the pay list's walk,
PayrollBasis.base_pay_on(), since plan step salary:X-av-3a deleted
salary_raises.apply_raises() and its yearly salary -- and the full
calculate_paycheck()
pipeline including deductions, taxes, 3rd-paycheck detection, inflation,
cumulative wages, and project_salary().
"""
