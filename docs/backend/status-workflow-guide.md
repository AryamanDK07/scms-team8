# Complaint Status Workflow

The backend defines legal complaint transitions in
`STATUS_TRANSITIONS` inside `src/complaints/services.py`.

## Workflow
OPEN -> ASSIGNED -> IN_PROGRESS

From IN_PROGRESS:
- Move to PENDING_INFORMATION and back.
- Resolve through the guarded resolution workflow.

From RESOLVED:
- Move to CLOSED through the appropriate workflow.
- Reopen within the configured seven-day window.

From REOPENED:
- Return to IN_PROGRESS.

CLOSED is a terminal state.

## Safety rules
- Direct generic transitions to RESOLVED or CLOSED
  are guarded.
- Resolution requires its associated validation.
- The status transition rules must be preserved.

## Regression checks
Review StatusModelTests, StatusTrackingTests,
ResolutionTests and ReopenWindowTests.
