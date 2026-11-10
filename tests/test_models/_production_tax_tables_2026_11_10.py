"""What production's catalogue held for the six tables plan step salary:X-at-2 drops.

Read on production, read-only, on 2026-11-10 at revision ``897a6663d221``, the
oracle the revision's downgrade is graded against
(``test_the_per_user_tax_tables_go_migration.py``, ruling plan#3211: "in
production's exact shape").  Spelled here as read, never from the revision, so
the grade cannot echo what it grades.  A chain-built database differs in two
places, which the revision's docstring names: ``state_tax_configs``' and
``tax_bracket_sets``' column order, and the two credit columns' ``DEFAULT 0``.
"""

#: Each table's columns in production's order: name, type, numeric precision and
#: scale, character length, nullability and default (``information_schema.columns``).
PRODUCTION_COLUMNS = {
    'ref.tax_types': [
        ('id', 'integer', 32, 0, None, 'NO', "nextval('ref.tax_types_id_seq'::regclass)"),
        ('name', 'character varying', None, None, 10, 'NO', None),
    ],
    'salary.fica_configs': [
        ('id', 'integer', 32, 0, None, 'NO', "nextval('salary.fica_configs_id_seq'::regclass)"),
        ('user_id', 'integer', 32, 0, None, 'NO', None),
        ('tax_year', 'integer', 32, 0, None, 'NO', None),
        ('ss_rate', 'numeric', 5, 4, None, 'NO', '0.0620'),
        ('ss_wage_base', 'numeric', 12, 2, None, 'NO', '176100'),
        ('medicare_rate', 'numeric', 5, 4, None, 'NO', '0.0145'),
        ('medicare_surtax_rate', 'numeric', 5, 4, None, 'NO', '0.0090'),
        ('medicare_surtax_threshold', 'numeric', 12, 2, None, 'NO', '200000'),
        ('created_at', 'timestamp with time zone', None, None, None, 'NO', 'now()'),
    ],
    'salary.state_child_deductions': [
        (
            'id',
            'integer',
            32,
            0,
            None,
            'NO',
            "nextval('salary.state_child_deductions_id_seq'::regclass)",
        ),
        ('filing_status_id', 'integer', 32, 0, None, 'NO', None),
        ('state_code', 'character varying', None, None, 2, 'NO', None),
        ('tax_year', 'integer', 32, 0, None, 'NO', None),
        ('agi_min', 'numeric', 12, 2, None, 'NO', None),
        ('agi_max', 'numeric', 12, 2, None, 'YES', None),
        ('deduction_per_child', 'numeric', 12, 2, None, 'NO', None),
        ('user_id', 'integer', 32, 0, None, 'NO', None),
        ('created_at', 'timestamp with time zone', None, None, None, 'NO', 'now()'),
    ],
    'salary.state_tax_configs': [
        (
            'id',
            'integer',
            32,
            0,
            None,
            'NO',
            "nextval('salary.state_tax_configs_id_seq'::regclass)",
        ),
        ('user_id', 'integer', 32, 0, None, 'NO', None),
        ('tax_type_id', 'integer', 32, 0, None, 'NO', None),
        ('state_code', 'character varying', None, None, 2, 'NO', None),
        ('tax_year', 'integer', 32, 0, None, 'NO', None),
        ('flat_rate', 'numeric', 5, 4, None, 'YES', None),
        ('standard_deduction', 'numeric', 12, 2, None, 'YES', None),
        ('created_at', 'timestamp with time zone', None, None, None, 'NO', 'now()'),
        ('filing_status_id', 'integer', 32, 0, None, 'NO', None),
    ],
    'salary.tax_bracket_sets': [
        (
            'id',
            'integer',
            32,
            0,
            None,
            'NO',
            "nextval('salary.tax_bracket_sets_id_seq'::regclass)",
        ),
        ('user_id', 'integer', 32, 0, None, 'NO', None),
        ('filing_status_id', 'integer', 32, 0, None, 'NO', None),
        ('tax_year', 'integer', 32, 0, None, 'NO', None),
        ('standard_deduction', 'numeric', 12, 2, None, 'NO', None),
        ('child_credit_amount', 'numeric', 12, 2, None, 'NO', None),
        ('other_dependent_credit_amount', 'numeric', 12, 2, None, 'NO', None),
        ('description', 'character varying', None, None, 200, 'YES', None),
        ('created_at', 'timestamp with time zone', None, None, None, 'NO', 'now()'),
        ('child_credit_refundable_cap', 'numeric', 12, 2, None, 'NO', "'0'::numeric"),
    ],
    'salary.tax_brackets': [
        ('id', 'integer', 32, 0, None, 'NO', "nextval('salary.tax_brackets_id_seq'::regclass)"),
        ('bracket_set_id', 'integer', 32, 0, None, 'NO', None),
        ('min_income', 'numeric', 12, 2, None, 'NO', None),
        ('max_income', 'numeric', 12, 2, None, 'YES', None),
        ('rate', 'numeric', 5, 4, None, 'NO', None),
        ('sort_order', 'integer', 32, 0, None, 'NO', '0'),
    ],
}

#: Each table's keys and CHECKs by name, with PostgreSQL's definition
#: (``pg_get_constraintdef``), NOT NULLs aside.
PRODUCTION_CONSTRAINTS = {
    'ref.tax_types': [
        ('tax_types_name_key', 'UNIQUE (name)'),
        ('tax_types_pkey', 'PRIMARY KEY (id)'),
    ],
    'salary.fica_configs': [
        (
            'ck_fica_configs_positive_surtax_threshold',
            'CHECK ((medicare_surtax_threshold > (0)::numeric))',
        ),
        ('ck_fica_configs_positive_wage_base', 'CHECK ((ss_wage_base > (0)::numeric))'),
        (
            'ck_fica_configs_valid_medicare_rate',
            'CHECK (((medicare_rate >= (0)::numeric) AND (medicare_rate <= (1)::numeric)))',
        ),
        (
            'ck_fica_configs_valid_ss_rate',
            'CHECK (((ss_rate >= (0)::numeric) AND (ss_rate <= (1)::numeric)))',
        ),
        (
            'ck_fica_configs_valid_surtax_rate',
            'CHECK (((medicare_surtax_rate >= (0)::numeric) AND (medicare_surtax_rate <= '
            '(1)::numeric)))',
        ),
        ('ck_fica_configs_valid_tax_year', 'CHECK (((tax_year >= 2000) AND (tax_year <= 2100)))'),
        ('fica_configs_pkey', 'PRIMARY KEY (id)'),
        (
            'fica_configs_user_id_fkey',
            'FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE',
        ),
        ('uq_fica_configs_user_year', 'UNIQUE (user_id, tax_year)'),
    ],
    'salary.state_child_deductions': [
        (
            'ck_state_child_deductions_agi_order',
            'CHECK (((agi_max IS NULL) OR (agi_max > agi_min)))',
        ),
        ('ck_state_child_deductions_nonneg_agi_min', 'CHECK ((agi_min >= (0)::numeric))'),
        (
            'ck_state_child_deductions_nonneg_deduction',
            'CHECK ((deduction_per_child >= (0)::numeric))',
        ),
        (
            'ck_state_child_deductions_valid_tax_year',
            'CHECK (((tax_year >= 2000) AND (tax_year <= 2100)))',
        ),
        (
            'fk_state_child_deductions_filing_status_id',
            'FOREIGN KEY (filing_status_id) REFERENCES ref.filing_statuses(id) ON DELETE RESTRICT',
        ),
        (
            'fk_state_child_deductions_user_id',
            'FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE',
        ),
        ('state_child_deductions_pkey', 'PRIMARY KEY (id)'),
        (
            'uq_state_child_deductions_user_state_year_status_agi',
            'UNIQUE (user_id, state_code, tax_year, filing_status_id, agi_min)',
        ),
    ],
    'salary.state_tax_configs': [
        (
            'ck_state_tax_configs_nonneg_standard_deduction',
            'CHECK (((standard_deduction IS NULL) OR (standard_deduction >= (0)::numeric)))',
        ),
        (
            'ck_state_tax_configs_valid_rate',
            'CHECK (((flat_rate IS NULL) OR ((flat_rate >= (0)::numeric) AND (flat_rate <= '
            '(1)::numeric))))',
        ),
        (
            'ck_state_tax_configs_valid_tax_year',
            'CHECK (((tax_year >= 2000) AND (tax_year <= 2100)))',
        ),
        (
            'fk_state_tax_configs_filing_status_id',
            'FOREIGN KEY (filing_status_id) REFERENCES ref.filing_statuses(id) ON DELETE RESTRICT',
        ),
        (
            'fk_state_tax_configs_tax_type_id',
            'FOREIGN KEY (tax_type_id) REFERENCES ref.tax_types(id) ON DELETE RESTRICT',
        ),
        ('state_tax_configs_pkey', 'PRIMARY KEY (id)'),
        (
            'state_tax_configs_user_id_fkey',
            'FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE',
        ),
        (
            'uq_state_tax_configs_user_state_year_status',
            'UNIQUE (user_id, state_code, tax_year, filing_status_id)',
        ),
    ],
    'salary.tax_bracket_sets': [
        (
            'ck_tax_bracket_sets_nonneg_child_credit',
            'CHECK ((child_credit_amount >= (0)::numeric))',
        ),
        ('ck_tax_bracket_sets_nonneg_deduction', 'CHECK ((standard_deduction >= (0)::numeric))'),
        (
            'ck_tax_bracket_sets_nonneg_other_credit',
            'CHECK ((other_dependent_credit_amount >= (0)::numeric))',
        ),
        (
            'ck_tax_bracket_sets_nonneg_refundable_cap',
            'CHECK ((child_credit_refundable_cap >= (0)::numeric))',
        ),
        (
            'ck_tax_bracket_sets_valid_tax_year',
            'CHECK (((tax_year >= 2000) AND (tax_year <= 2100)))',
        ),
        (
            'fk_tax_bracket_sets_filing_status_id',
            'FOREIGN KEY (filing_status_id) REFERENCES ref.filing_statuses(id) ON DELETE RESTRICT',
        ),
        ('tax_bracket_sets_pkey', 'PRIMARY KEY (id)'),
        (
            'tax_bracket_sets_user_id_fkey',
            'FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE',
        ),
        ('uq_tax_bracket_sets_user_year_status', 'UNIQUE (user_id, tax_year, filing_status_id)'),
    ],
    'salary.tax_brackets': [
        (
            'ck_tax_brackets_income_order',
            'CHECK (((max_income IS NULL) OR (max_income >= min_income)))',
        ),
        ('ck_tax_brackets_nonneg_min', 'CHECK ((min_income >= (0)::numeric))'),
        (
            'ck_tax_brackets_valid_rate',
            'CHECK (((rate >= (0)::numeric) AND (rate <= (1)::numeric)))',
        ),
        (
            'tax_brackets_bracket_set_id_fkey',
            'FOREIGN KEY (bracket_set_id) REFERENCES salary.tax_bracket_sets(id) ON DELETE '
            'CASCADE',
        ),
        ('tax_brackets_pkey', 'PRIMARY KEY (id)'),
    ],
}

#: Each table's indexes by name, with their definition (``pg_indexes``).
PRODUCTION_INDEXES = {
    'ref.tax_types': [
        (
            'tax_types_name_key',
            'CREATE UNIQUE INDEX tax_types_name_key ON ref.tax_types USING btree (name)',
        ),
        ('tax_types_pkey', 'CREATE UNIQUE INDEX tax_types_pkey ON ref.tax_types USING btree (id)'),
    ],
    'salary.fica_configs': [
        (
            'fica_configs_pkey',
            'CREATE UNIQUE INDEX fica_configs_pkey ON salary.fica_configs USING btree (id)',
        ),
        (
            'uq_fica_configs_user_year',
            'CREATE UNIQUE INDEX uq_fica_configs_user_year ON salary.fica_configs USING btree '
            '(user_id, tax_year)',
        ),
    ],
    'salary.state_child_deductions': [
        (
            'state_child_deductions_pkey',
            'CREATE UNIQUE INDEX state_child_deductions_pkey ON salary.state_child_deductions '
            'USING btree (id)',
        ),
        (
            'uq_state_child_deductions_user_state_year_status_agi',
            'CREATE UNIQUE INDEX uq_state_child_deductions_user_state_year_status_agi ON '
            'salary.state_child_deductions USING btree (user_id, state_code, tax_year, '
            'filing_status_id, agi_min)',
        ),
    ],
    'salary.state_tax_configs': [
        (
            'state_tax_configs_pkey',
            'CREATE UNIQUE INDEX state_tax_configs_pkey ON salary.state_tax_configs USING btree '
            '(id)',
        ),
        (
            'uq_state_tax_configs_user_state_year_status',
            'CREATE UNIQUE INDEX uq_state_tax_configs_user_state_year_status ON '
            'salary.state_tax_configs USING btree (user_id, state_code, tax_year, '
            'filing_status_id)',
        ),
    ],
    'salary.tax_bracket_sets': [
        (
            'tax_bracket_sets_pkey',
            'CREATE UNIQUE INDEX tax_bracket_sets_pkey ON salary.tax_bracket_sets USING btree '
            '(id)',
        ),
        (
            'uq_tax_bracket_sets_user_year_status',
            'CREATE UNIQUE INDEX uq_tax_bracket_sets_user_year_status ON '
            'salary.tax_bracket_sets USING btree (user_id, tax_year, filing_status_id)',
        ),
    ],
    'salary.tax_brackets': [
        (
            'idx_tax_brackets_bracket_set',
            'CREATE INDEX idx_tax_brackets_bracket_set ON salary.tax_brackets USING btree '
            '(bracket_set_id, sort_order)',
        ),
        (
            'tax_brackets_pkey',
            'CREATE UNIQUE INDEX tax_brackets_pkey ON salary.tax_brackets USING btree (id)',
        ),
    ],
}
