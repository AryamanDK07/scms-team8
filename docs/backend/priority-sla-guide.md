# Complaint Priority and SLA Logic

Backend implementation: `src/complaints/services.py`.

## Priority assignment
The `priority_for()` function retrieves the default
priority configured for the complaint category.

When no category priority is configured, the function
falls back to Medium.

## SLA calculation
`calculate_sla_deadline()` calculates the deadline
from the start time and category SLA hours.

A missing or falsy SLA value falls back to 48 hours.

## Priority overrides
`override_priority()` requires a non-empty reason
and records the priority change in complaint history.

## Regression checks
Review PriorityMatrixTests and related routing tests.
