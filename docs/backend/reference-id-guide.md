# Complaint Reference ID Generation

The backend generates unique complaint references using
`generate_reference_id()` in `src/complaints/services.py`.

## Format
SCMS-YYYY-NNNNNN

## Implementation
- Determine the current year.
- Use a per-year ReferenceSequence database record.
- Lock the sequence row within a transaction.
- Increment the counter atomically.
- Return the formatted reference ID.

PostgreSQL row-level locking protects against concurrent
requests receiving the same sequence number.

## Verification
Review the ReferenceIdTests test class.
