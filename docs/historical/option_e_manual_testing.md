> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Manual Testing Strategy -- Option E Unified Loan Parameters

## Prerequisites

1. Run `flask db upgrade` to apply the migration
2. Start the dev server: `flask run`
3. Log in at http://localhost:5000
4. Have at least one checking account with pay periods already set up (for the savings dashboard to work)

---

## Test 1: Existing Mortgage Account (Migration Verification)

**Purpose:** Confirm that pre-existing mortgage data survived the migration intact.

If you had a mortgage account before this refactor:

1. Navigate to the Accounts Dashboard (`/savings`)
2. Find your mortgage account card
3. Verify:
   - The card shows the house icon (bi-house) next to the name
   - The interest rate is displayed (e.g. "-- 6.500%")
   - Monthly payment and payoff date are shown
   - The detail button has a house icon
4. Click the detail button to open the loan dashboard
5. Verify the URL is `/accounts/<id>/loan` (not `/accounts/<id>/mortgage`)
6. Verify the Overview tab shows:
   - Correct original principal, current principal, interest rate, term, origination date, payment day
   - Monthly P&I is a reasonable dollar amount
   - Total interest and projected payoff date are displayed
7. Verify the Balance Over Time chart renders with a downward curve
8. If you had escrow components, click the Escrow tab and verify they are all present with correct amounts
9. If the mortgage was an ARM, verify the Rate History tab appears and any previous rate changes are listed

**If you did NOT have a mortgage before, skip to Test 2.**

---

## Test 2: Existing Auto Loan Account (Migration Verification)

Same as Test 1 but for auto loan:

1. On the Accounts Dashboard, find your auto loan card
2. Verify the car icon (bi-car-front) appears
3. Click through to the dashboard -- URL should be `/accounts/<id>/loan`
4. All original data should be intact: principal, rate, term, payment day
5. The dashboard should now show tabs (Overview, Escrow, Payoff Calculator) -- auto loans previously had no tabs, just a single-column layout

---

## Test 3: Create a New Mortgage Account

**Purpose:** Test the full create-to-dashboard flow for the most common loan type.

1. Go to Manage Accounts (`/accounts`) and click "New Account"
2. Enter name "Test Mortgage", select type "Mortgage", enter anchor balance "250000"
3. Submit
4. **Verify redirect:** You should land on a setup page at `/accounts/<id>/loan?setup=1`
5. **Verify setup page shows:**
   - House icon and "Test Mortgage -- Setup" heading
   - "Configure Mortgage Parameters" card header
   - Description mentions "mortgage"
   - Current Principal field is pre-filled with "250000.00" (from anchor balance)
   - Term field shows max 600, placeholder "600", help text "Max 600 months (50 years)"
   - ARM checkbox is present and unchecked
6. Fill in the form:
   - Original Principal: 300000
   - Current Principal: 250000 (pre-filled)
   - Interest Rate: 6.5
   - Term: 360
   - Origination Date: 2023-06-01
   - Payment Day: 1
   - Leave ARM unchecked
7. Click "Save & Continue"
8. **Verify redirect** to the loan dashboard at `/accounts/<id>/loan`
9. **Verify dashboard:**
   - Page title in browser tab: "Test Mortgage -- Mortgage -- Shekel"
   - Breadcrumb: "Accounts Dashboard > Test Mortgage Mortgage"
   - Heading shows house icon
   - Tabs visible: Overview, Escrow, Payoff Calculator (no Rate History -- ARM is off)
   - Loan Summary card:
     - Original Principal: $300,000.00
     - Current Principal: $250,000.00 (accented color, larger font)
     - Interest Rate: 6.500%
     - Term: 360 months (30.0 years)
     - Origination Date: Jun 1, 2023
     - Payment Day: 1st of each month
     - Monthly P&I: a reasonable amount (~$1,580)
     - Total Interest: a large number
     - Projected Payoff: a date in the future
   - Loan Parameters card: form pre-filled with current values, term max is 600
   - Balance Over Time chart: declining curve

---

## Test 4: Create a New Auto Loan Account

**Purpose:** Test type-specific term validation and a different icon.

1. Create account: name "Test Auto", type "Auto Loan", balance "25000"
2. **Verify redirect** to setup page
3. **Verify setup page:**
   - Car icon (bi-car-front) in heading
   - "Configure Auto Loan Parameters"
   - Term max is 120, placeholder "120", help text "Max 120 months (10 years)"
   - Current Principal pre-filled with "25000.00"
4. **Test term validation:** Enter term = 180 (exceeds Auto Loan's 120 max)
   - Fill all other fields: Original 30000, Current 25000, Rate 5.0, Date 2025-01-01, Day 15
   - Submit
   - **Verify:** Error flash "Term cannot exceed 120 months for Auto Loan."
   - The setup form re-renders (you stay on the same page, not redirected)
5. Fix term to 60, submit again
6. **Verify dashboard** renders with car icon, correct data, 60-month term

---

## Test 5: Create a Student Loan Account (Previously Dead End)

**Purpose:** Student loans were previously stub-only with no params model. Verify they now work end-to-end.

1. Create account: name "Test Student Loan", type "Student Loan", balance "40000"
2. **Verify redirect** to `/accounts/<id>/loan?setup=1` (not the accounts list)
3. **Verify setup page:**
   - Mortarboard icon (bi-mortarboard)
   - "Configure Student Loan Parameters"
   - Term max 300, help text "Max 300 months (25 years)"
4. Fill in: Original 45000, Current 40000, Rate 4.5, Term 120, Date 2022-09-01, Day 1
5. Submit
6. **Verify dashboard** renders with mortarboard icon, all data correct
7. All tabs work (Overview, Escrow, Payoff Calculator)

---

## Test 6: Create a Personal Loan Account (Previously Dead End)

1. Create: "Test Personal Loan", type "Personal Loan", balance "10000"
2. Verify redirect to loan setup
3. Verify cash-coin icon (bi-cash-coin), max term 120
4. Fill in: Original 12000, Current 10000, Rate 8.0, Term 36, Date 2025-06-01, Day 15
5. Submit, verify dashboard works

---

## Test 7: Create a HELOC Account (Previously No Params)

**Purpose:** HELOC had has_parameters=False before. Now True. Verify it works.

1. Create: "Test HELOC", type "HELOC", balance "30000"
2. Verify redirect to loan setup
3. Verify bank icon (bi-bank), max term 360
4. Fill in: Original 50000, Current 30000, Rate 7.25, Term 240, Date 2024-01-01, Day 5
5. Submit, verify dashboard renders

---

## Test 8: Update Loan Parameters

1. Go to any loan dashboard (e.g. Test Mortgage from Test 3)
2. On the Overview tab, find the "Loan Parameters" card on the right
3. Change Current Principal to 245000.00
4. Change Interest Rate to 6.250
5. Change Payment Day to 15
6. Click "Update Parameters"
7. **Verify:**
   - Flash message "Loan parameters updated."
   - Summary card reflects the new values (rate 6.250%, day 15th)
   - Monthly P&I recalculated (should differ from before)
   - Chart re-renders with the updated projection

---

## Test 9: Toggle ARM and Rate History

1. Go to a mortgage loan dashboard
2. In Loan Parameters, check the "Adjustable Rate (ARM)" checkbox
3. Click "Update Parameters"
4. **Verify:** The "Rate History" tab now appears in the tab bar
5. Click the "Rate History" tab
6. **Verify:** Shows "No rate changes recorded." with an add form
7. Add a rate change:
   - Effective Date: today's date
   - New Rate: 7.000
   - Notes: "Annual adjustment"
   - Click "Record Change"
8. **Verify (HTMX):**
   - The rate appears in the table without a page reload
   - The table shows: date, 7.000%, "Annual adjustment"
9. Go back to Overview tab
10. **Verify:** Interest Rate in summary now shows 7.000% (the rate change also updates the params)
11. Add a second rate change with rate 6.750, verify both appear in descending date order

---

## Test 10: Escrow Components

1. Go to any loan dashboard (mortgage is most natural, but works on any type)
2. Click the "Escrow" tab
3. **Verify:** Shows "No escrow components configured." and "$0.00/mo" badge
4. Add an escrow component:
   - Name: "Property Tax"
   - Annual Amount: 4800
   - Inflation Rate: 3
   - Click "Add"
5. **Verify (HTMX -- no page reload):**
   - "Property Tax" row appears in the table
   - Annual: $4,800.00
   - Monthly: $400.00
   - Inflation: 3.0%
   - Badge updates to "$400.00/mo"
   - On the Overview tab, "Total Monthly (with escrow)" line appears showing P&I + $400
6. Add another: "Homeowner Insurance", $2400, leave inflation blank
7. **Verify:**
   - Both components in the table
   - Badge shows "$600.00/mo" ($400 + $200)
   - Insurance inflation shows "--"
8. Delete Property Tax by clicking the trash icon
9. **Verify (HTMX):**
   - Property Tax disappears
   - Badge updates to "$200.00/mo"
   - Overview tab total payment updates

---

## Test 11: Payoff Calculator -- Extra Payment Mode

1. Go to any loan dashboard, click the "Payoff Calculator" tab
2. The "Extra Payment" sub-tab should be active with a slider and text input
3. **Verify slider sync:**
   - Move the slider -- the text input value should update
   - Type a value in the text input -- the slider should move
4. Click "Calculate" with extra = $200
5. **Verify results appear (HTMX):**
   - "New Payoff Date" -- a date earlier than the standard payoff
   - "Months Saved" -- a positive number
   - "Interest Saved" -- a dollar amount
   - A chart showing two lines: Standard (blue) and Accelerated (different color)
6. Move the slider to $0 and re-calculate
7. **Verify:** Months saved = 0, interest saved = $0.00
8. Move the slider to $1000 and re-calculate
9. **Verify:** More months and interest saved than $200

---

## Test 12: Payoff Calculator -- Target Date Mode

1. On the Payoff Calculator tab, click the "Target Date" sub-tab
2. Enter a target date that's about 5 years from now
3. Click "Calculate"
4. **Verify:**
   - Shows "Required Extra Monthly Payment" -- a dollar amount
   - Shows "New Total Monthly" -- base payment + extra
5. Enter a target date far in the future (past the standard payoff)
6. **Verify:** Message "Your loan will be paid off before this date with standard payments."
7. Enter a target date in the past
8. **Verify:** Message "Target date is not achievable -- it may be in the past or too soon."

---

## Test 13: Accounts Dashboard Integration

1. Navigate to the Accounts Dashboard (`/savings`)
2. **Verify each loan account card shows:**
   - Correct type-specific icon (house, car, mortarboard, cash-coin, or bank)
   - Account type name displayed
   - Interest rate shown (e.g. "-- 6.500%")
   - Current balance
   - Monthly payment amount
   - Payoff date
   - 3/6/12 month balance projections (declining for debt)
3. **Verify the detail button** for each loan account links to `/accounts/<id>/loan` (not old routes)
4. Click the detail button for each loan type and verify the dashboard loads

---

## Test 14: Setup Required Badge

1. Create a new loan account but do NOT complete the setup form
   - Create account, arrive on setup page, click "Back to Accounts" without filling the form
2. Go to the Accounts Dashboard
3. **Verify:** The account card shows a "Setup Required" badge in yellow
4. Click the detail button on that card
5. **Verify:** Lands on the setup form (not the dashboard)

---

## Test 15: Charts Dashboard Integration

1. Navigate to the Charts dashboard (if accessible)
2. Look for the "Amortization Breakdown" chart or "Balance Over Time" chart
3. **Verify:** Loan accounts show up correctly with their amortization projections
4. The charts should display principal and interest breakdowns

---

## Test 16: Navigation and Breadcrumbs

1. From any loan dashboard, verify the breadcrumb shows: "Accounts Dashboard > [Name] [Type]"
2. Click "Accounts Dashboard" in the breadcrumb -- verify it goes to `/savings`
3. Click "Back to Accounts" button -- verify it goes to `/savings`
4. Use the browser back button from the loan dashboard -- verify no errors

---

## Test 17: Old URLs Return 404

**Purpose:** Confirm the old routes are gone and don't produce confusing errors.

1. Manually navigate to `/accounts/<any-mortgage-id>/mortgage`
2. **Verify:** 404 page (not a 500 or a redirect loop)
3. Navigate to `/accounts/<any-auto-loan-id>/auto-loan`
4. **Verify:** 404 page

---

## Test 18: Edge Cases

1. **Zero interest rate:** Create a loan with rate = 0. Verify the dashboard renders without errors (interest-free loan).
2. **Payment day 31:** Set payment day to 31. Verify it saves and displays correctly (months with fewer days will use the last day).
3. **Very short term:** Create a loan with term = 1. Verify the dashboard shows the schedule with a single payment.
4. **ARM checkbox on auto loan:** On an auto loan dashboard, check the ARM box and update. Verify the Rate History tab appears. This proves ARM is not mortgage-locked.
5. **Escrow on auto loan:** On an auto loan dashboard, add an escrow component. Verify it works (proves escrow is not mortgage-locked).

---

## Test 19: Data Integrity Spot Checks

1. After creating and modifying several accounts, open a database client (psql or similar)
2. Run: `SELECT * FROM budget.loan_params;`
3. **Verify:**
   - All loan accounts have exactly one row
   - `interest_rate` values are stored as decimals (0.065, not 6.5)
   - `is_arm` is FALSE for accounts you didn't check the ARM box
   - `arm_first_adjustment_months` and `arm_adjustment_interval_months` are NULL for non-ARM loans
4. Run: `SELECT * FROM budget.rate_history;`
5. **Verify:** Rate changes you recorded appear with correct decimal rates
6. Run: `SELECT name, icon_class, max_term_months, has_parameters, has_amortization FROM ref.account_types WHERE has_amortization = TRUE;`
7. **Verify:** Mortgage (600), Auto Loan (120), Student Loan (300), Personal Loan (120), HELOC (360) all have correct values and `has_parameters = TRUE`
8. Run: `SELECT COUNT(*) FROM budget.auto_loan_params;`
9. **Verify:** Error -- table does not exist (it was dropped)
10. Same for `budget.mortgage_params` and `budget.mortgage_rate_history`

---

## Realistic Test Values

Use these values when creating accounts. Expected monthly payments are calculated
from `original_principal` and `term_months` (the contractual payment), NOT from
current balance. The dashboard balance chart should show a smooth declining curve
from the current principal to zero.

### Mortgage -- standard 30-year fixed

| Field | Value |
|-------|-------|
| Original Principal | 320000 |
| Current Principal | 295000 |
| Interest Rate | 6.875 |
| Term | 360 |
| Origination Date | 2024-03-01 |
| Payment Day | 1 |
| ARM | unchecked |

**Expected:** Monthly P&I ~$2,101. Payoff ~Mar 2054. Balance chart starts at
$295k and declines smoothly. Total interest should be in the $430k range.

### Auto Loan -- 5-year term, partially paid

| Field | Value |
|-------|-------|
| Original Principal | 35000 |
| Current Principal | 18000 |
| Interest Rate | 3.25 |
| Term | 60 |
| Origination Date | 2022-09-01 |
| Payment Day | 15 |

**Expected:** Monthly P&I ~$632. NOT ~$1,663 (which would be re-amortizing $18k
over the remaining months). Payoff ~Sep 2027. Chart starts at $18k and declines
over ~30 remaining months.

### Student Loan -- 10-year standard

| Field | Value |
|-------|-------|
| Original Principal | 45000 |
| Current Principal | 32000 |
| Interest Rate | 5.5 |
| Term | 120 |
| Origination Date | 2022-01-15 |
| Payment Day | 15 |

**Expected:** Monthly P&I ~$488. Payoff ~Jan 2032. Chart starts at $32k.

### Personal Loan -- 3-year term

| Field | Value |
|-------|-------|
| Original Principal | 15000 |
| Current Principal | 15000 |
| Interest Rate | 9.99 |
| Term | 36 |
| Origination Date | 2026-04-01 |
| Payment Day | 1 |

**Expected:** Monthly P&I ~$484. Brand-new loan so current = original. Chart
shows full 36-month declining arc. Total interest ~$2,400.

### HELOC -- 20-year draw/repay

| Field | Value |
|-------|-------|
| Original Principal | 50000 |
| Current Principal | 28000 |
| Interest Rate | 8.25 |
| Term | 240 |
| Origination Date | 2020-06-01 |
| Payment Day | 5 |

**Expected:** Monthly P&I ~$427. Chart starts at $28k.

### Mortgage -- ARM with escrow

Use the mortgage above, then update it:
1. Check the ARM box, save
2. Add rate change: date 2026-04-01, rate 7.125, notes "First adjustment"
3. Add escrow: "Property Tax", annual $6000, inflation 3%
4. Add escrow: "Homeowner Insurance", annual $2400, inflation 0

**Expected after escrow:**
- Monthly escrow = ($6000 + $2400) / 12 = $700/mo
- Total Monthly = ~$2,101 + $700 = ~$2,801 (shown on Overview tab)
- Rate History tab shows the 7.125% entry
- Interest Rate on summary updates to 7.125%

## Edge Case Test Values

### Zero interest rate (family loan)

| Field | Value |
|-------|-------|
| Original Principal | 5000 |
| Current Principal | 5000 |
| Interest Rate | 0 |
| Term | 24 |
| Origination Date | 2026-01-01 |
| Payment Day | 1 |

**Expected:** Monthly P&I = $208.33 ($5000/24). Total interest = $0.
Chart should be a straight line declining from $5k to $0.

### Nearly paid off (1 month remaining)

| Field | Value |
|-------|-------|
| Original Principal | 20000 |
| Current Principal | 500 |
| Interest Rate | 4.5 |
| Term | 48 |
| Origination Date | 2022-05-01 |
| Payment Day | 10 |

**Expected:** Monthly P&I ~$456. Only 1-2 months left on chart since balance
is almost zero. Dashboard should render without error.

### Payment day 31

| Field | Value |
|-------|-------|
| Original Principal | 10000 |
| Current Principal | 10000 |
| Interest Rate | 5.0 |
| Term | 36 |
| Origination Date | 2026-04-01 |
| Payment Day | 31 |

**Expected:** Monthly P&I ~$300. February payments should show as the 28th/29th.
April/June/September/November as the 30th. All other months on the 31st.

### Maximum term mortgage

| Field | Value |
|-------|-------|
| Original Principal | 500000 |
| Current Principal | 500000 |
| Interest Rate | 7.0 |
| Term | 600 |
| Origination Date | 2026-04-01 |
| Payment Day | 1 |

**Expected:** Monthly P&I ~$3,326 for a 50-year mortgage. This should pass
since mortgage max is 600 months. Total interest will be enormous (~$1.5M).

### Auto loan term rejection

Use the same values as the auto loan above but set term to 180.

**Expected:** Error "Term cannot exceed 120 months for Auto Loan." The form
should re-render without creating the params record.

---

## Order of Testing

Run them in this order -- earlier tests create data that later tests verify:

1. Tests 1-2 (migration verification -- skip if no pre-existing data)
2. Test 3 (mortgage -- the full reference flow)
3. Tests 4-7 (other loan types -- confirm type-specific behavior)
4. Tests 8-12 (feature verification on the accounts you just created)
5. Tests 13-14 (dashboard integration)
6. Tests 15-16 (secondary UI)
7. Tests 17-18 (edge cases and old route verification)
8. Test 19 (database spot check -- do this last)
