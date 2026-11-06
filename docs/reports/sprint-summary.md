# Sprint summary and velocity

Smart Complaint Management System — Team 8
Figures read from Jira (project T8, board 68) on 6 November 2026.

## Velocity

| | Issues | Story points | Status |
|---|---|---|---|
| Sprint 1 (27 Oct – 3 Nov, closed) | 15 | 51 | all Done |
| Sprint 2 (6 Nov – 13 Nov, active) | 17 | 65 | all Done |
| Product backlog | 13 | 69 | all To Do |
| **Total** | **45** | **185** | |

Average velocity across the two sprints: **58 points**.

Estimates use the Fibonacci scale 1 / 2 / 3 / 5 / 8. Nothing is a 1 — no
issue in this project turned out that small — and nothing above 8, because
anything larger would have been split.

## What each sprint delivered

**Sprint 1 (51 points)** — the core complaint lifecycle: login and RBAC,
password reset, lodging with evidence, reference IDs, auto-routing,
assignment, the priority matrix, status tracking, comments, resolution with
proof, master data, the audit trail, and search.

**Sprint 2 (65 points)** — the surrounding machinery, and the security
requirements that Sprint 1 had assumed rather than enforced: Argon2id
hashing, account lockout, deny-by-default authorisation, secure evidence
storage, the append-only audit guard at database level, rate limiting,
drafts, duplicate detection, reopen and rating, the dashboard, CSV and PDF
export, retention, the container build, and the CI pipeline itself (T8-99).

The second sprint is 14 points larger than the first while covering 17
issues rather than 15. Two things explain it: the data model and service
layer were already in place by then, so most Sprint 2 work extended
existing code rather than creating it; and CI (added as T8-99 partway
through) caught mistakes within minutes that would otherwise have been
found by hand, or not at all.

## The backlog (69 points, unstarted)

Every backlog issue is labelled `blocked-infrastructure` with a second
label naming what it needs: `needs-sms-gateway`, `needs-scheduler`,
`needs-load-testing`, `needs-deployed-server`,
`needs-accessibility-scanner`, `needs-security-scanner`, or
`partially-met-followup`.

None of it is blocked on effort. The two largest clusters are
notifications (T8-21, T8-28, T8-29 — 21 points, needing an SMS gateway and
a task scheduler) and the measured non-functional requirements (T8-36,
T8-38, T8-39 — 21 points, needing load-testing and scanning tools).

`docs/DEVIATIONS.md` records why each one was not attempted, and the two
`partially-met-followup` issues (T8-100, T8-101) carry the specific clauses
their parents could not meet.

## Caveat on Sprint 1's figures

Sprint 1 ran without CI, so its test results were produced by hand and
recorded as Jira comments. The story points above were assigned
retrospectively, in one pass, after both sprints were complete — they are
a consistent relative scale across the project, not estimates the team
committed to before starting. A velocity figure derived from
retrospective estimates is useful for sizing the backlog and should not be
read as a measure of how well the team predicted its own capacity.
