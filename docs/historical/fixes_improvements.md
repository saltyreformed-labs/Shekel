> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Fixes and Improvements

## Grid

- Grid transaction names  are sorted by Category Item Name but only the Category Group and Transaction name are displayed. This makes the list of transactions appear unsorted.

- The Grid calculates based on estimated amounts and ignores actual amounts. This prohibits accurate budgeting and limits the usefulness of tracking estimated vs actual. For an expense like electricity that fluctuates a user must change the estimated amount for the grid to calculate an accurate projected end balance. Once the estimated amount is updated there isn't any reason to add the actual amount because they would be the same value. I think the better approach might be to calculate on estimated amount if actual is null and actual if it is populated. Then when a user received their bill they could add the actual amount and see the real world effect on the budget. For example, an end user budgets $300 per month for electricity but their bill comes in at $350 because of the weather, the user can enter $350 as the actual amount but the projected end balance is still calculating on the estimated $300 which leads the user to believe there is more money available than there is in reality.

## CRUD

- There is some inconsistency in how deactivate and delete function across the app.
  - Some areas have no method to delete only deactivate. If a user creates an by mistake they have to view it indefinitely.
    - Recurring Transactions, Accounts, Recurring Transfers, etc

- In settings Categories can only be deleted but not edited

- Account Types can only be added and deleted but not edited.
  - Types need more options to set with the changes to how the app handles accounts. Each should have a form with settings to match the new database structure.

## Salary

- Salary Profile page /salary  shows inline icons for View Breakdown and View Projection and full buttons below the salary profile. The inline icons should be kept under the Actions columns and the full buttons removed.

## Charts

- On the Charts page /charts the Balance Over Time chart shows only month and day for the x-axis without the year. The user has no context for actual length of time

- Some of the values on the charts are not accurate. Some account balances show zero or a static number even though the account page shows a changing balance.

- I think the charts page needs a total overhaul. I want to add a new phase to the project roadmap dedicated to visualization and reporting.