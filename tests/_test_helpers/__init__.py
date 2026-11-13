"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.

The helpers live in this package's modules, one subject each, moved verbatim from the
single module this package replaced (plan#3586).  This module imports every name they
define but the four globals the database clock rebinds as it runs, so
``from tests._test_helpers import X`` reads each helper where it always did.
"""

from ._html_and_paths import APP_PACKAGE_DIR, select_option_values, field_is_disabled
from ._clock import (
    freeze_today,
    _DB_CLOCK_INSERT_ATTRS,
    _DB_CLOCK_BOUND_ENGINES,
    _DB_CLOCK_CALL_RX,
    _DB_CLOCK_TEXT_DEFAULT_RX,
    _DB_CLOCK_EXEMPT_VERBS,
    _FrozenDbClock,
    same_instant_writes,
    _db_clock_insert_attrs,
    _stamp_omitted_db_defaults,
    _db_clock_literal,
    _rewrite_db_clock_calls,
    bind_db_clock_rewriter,
    _freeze_db_clock,
)
from ._loans import (
    _SplitLoan,
    SPLIT_LOAN,
    insert_origination_rate,
    insert_trueup_event,
    insert_tracking_start_event,
    create_loan_account,
    loan_params_for,
    add_escrow_line,
    create_loan_with_trueup,
)
from ._accounts import (
    create_savings_account,
    create_hysa_account,
    _LEDGER_SUITE_ANCHOR_BALANCE,
    create_account_via_service,
    create_account_of_type,
    make_balanced_entry,
)
from ._migrations import (
    _C4C_REVISION_FILE,
    _C17A_REVISION_FILE,
    _C17D2_REVISION_FILE,
    _R18A_REVISION_FILE,
    _R18B_REVISION_FILE,
    _S11E_REVISION_FILE,
    restore_pay_schedule_rhythm_columns,
    rewind_pay_schedule_rhythm,
    _pay_schedule_carries_a_cadence,
    rewind_paycheck_lines_rename,
    replay_paycheck_lines_rename,
    _R5A_REVISION_FILE,
    restore_rule_due_day_column,
    restore_pay_period_derived_columns,
    relax_pay_schedule_shift_not_null,
    run_migration_callable,
    RAISE_EXCEPTION_SQLSTATE,
    refused_by_database_rule,
    constraint_name_from,
    load_migration_module,
    step_down_through_the_journal_link_drop,
    step_down_through_the_transfer_column_drop,
    load_init_database_module,
)
from ._users_and_eras import (
    enable_mfa,
    register_form_data,
    cadence_form_values,
    shift_form_value,
    shift_id_of,
    rhythm_of,
    era_of,
    EMPTY_CALENDAR_PHASE,
    eras_of,
    mint_fixture_era,
    restate_fixture_era,
    strip_owner_schedule,
    registration_spec,
)
from ._transfers import (
    seam_confirmed_view,
    contract_forward_references,
    seam_cash_balance_at,
    create_transfer,
    create_settled_transfer,
    create_settled_cash_transaction,
    settle_cash_row,
    set_default_grid_account,
)
from ._salary_and_entries import (
    start_test_pay_list,
    DEFAULT_TEST_PAY,
    make_salary_profile,
    make_recurring_raise,
    add_entry,
    payback_row_of,
    mark_purchase_settled,
)
from ._settle_days_and_sides import (
    _UNSET_SETTLED_ON,
    settle_instant_on,
    default_settle_day,
    typed,
    observed,
    settlement_if_settling,
    settled_day_basis_id,
    an_entered_day,
    on_both_sides,
    an_asserted_day,
    an_observed_day,
    settle_day_columns,
    figure_source_columns,
    transfer_side_record,
    side_records_by_key,
    transfer_side_figure,
    transfer_side_settle_day,
    purchases_of,
)
from ._rows import (
    add_txn,
    cover_bare_settled_row,
    generate_row_of,
    one_off_row_of,
    generate_transfer_of,
    _the_one_row_the_engine_writes,
)
from ._owner_edits import (
    repriced_by_the_owner,
    transfer_repriced_by_the_owner,
    moved_by_the_owner,
    a_deleted_occurrence,
    definition_firing_twice_in_a_paycheck,
)
from ._anchors import (
    require_assertion_instant,
    observed_day_of,
    add_anchor_history,
    override_anchor,
    reassert_balance_on,
    append_only_guard_lifted,
    account_never_asserted,
    open_books_before_the_first_assertion,
    governing_opening_row,
    _governing_opening_day,
    restate_account_opening,
    append_balance_assertion,
    assert_pay_period_invariants,
)
from ._prices_and_rules import (
    resolved_amount,
    state_template_price,
    bare_expense_template,
    sole_rule_owned_by,
    make_every_period_rule,
    make_line_cadence_rule,
    first_occurrence_on_day,
    _default_first_occurrence,
    transient_cadence_rule,
    _cadence_spec,
    make_cadence_rule,
    rows_by_paycheck,
    WEEKLY_FIRST_ON_SEED_PERIODS,
)
from ._templates import (
    create_envelope_txn,
    make_expense_template,
    make_income_template,
    make_projected_envelope_expense,
    _priced_repeating_template,
    populate_in_a_fresh_pass,
    make_transfer_template,
)
from ._loan_templates_and_assets import (
    bind_rule_to_loan,
    make_loan_payment_template,
    make_retired_loan_payment,
    make_appreciating_account,
    make_investment_account,
)
from ._sql_and_locks import (
    _FROZEN_COUNTER_COLUMNS,
    _KIND_INJECTED_COUNTER_COLUMNS,
    _FROZEN_FALLBACK_CONFLICT,
    _HEAD_FALLBACK_CONFLICT,
    _FROZEN_FALLBACK_JOIN,
    _HEAD_FALLBACK_JOIN,
    _inject_pass_a_kind,
    inject_cash_backfill_kind_id,
    capture_sql_statements,
    took_advisory_lock,
    advisory_lock_keys,
    OWNER_LOCK_BLOCK_TIMEOUT_MS,
    owner_lock_key,
    HeldOwnerKey,
    bound_lock_waits,
    assert_timed_out_on_owner_key,
)
from ._tax_law import (
    MADE_UP_SOURCES,
    EMPTY_TAX_LAW,
    zero_fica,
    made_up_fica,
    made_up_state,
    no_income_tax_state,
    made_up_year,
    bracket_set_law,
    NO_TAX_LAW,
    fica_only_law,
    state_and_fica_law,
    made_up_law,
)
from ._payloads_and_windows import (
    validated_cadence,
    end_bound_payload,
    cadence_payload,
    payroll_basis,
    pricing_over,
    derived_calendar,
    derived_window,
    biweekly_window,
    window_head,
    period_window,
    dashboard_section,
)
from ._read_passes import (
    current_pay_period,
    read_pass_over_paydays,
    read_pass,
    counting_calls,
    counting_read_passes,
    PlantedPricing,
    planted_basis,
    transfer_amount,
    basis_for,
    all_periods,
    derived_span,
    last_covered_day,
)
from ._calendar import (
    open_owner_calendar,
    record_paydays_across_a_hole,
    rebuild_calendar,
    rebuild_calendar_on,
    rebuild_calendar_from_spans,
    pay_periods_hydrated,
)
from ._amount_bases_and_stubs import (
    rendered_transfer_amount,
    amount_basis_for,
    count_amount_bases,
    _A_MATCHED_GROUP,
    match_two_lines,
    write_past_the_amount_seam,
    _figure_column_of,
    make_flat_paycheck_line,
    build_pay_stub_world,
    add_test_pay_stub,
    _stub_line_amount,
)
