# Test case execution record

Smart Complaint Management System — Team 8
Against Test Plan STP v1.0, section 13 (RTM).
Execution evidence: CI run
[42160435599](https://github.com/AryamanDK07/scms-team8/actions/runs/42160435599)
on commit `39b8a3c`, 6 November 2026 — 365 automated tests, 0 failures,
0 skipped, on PostgreSQL 14 under Python 3.11 and 3.13.

The Test Plan defines 42 test cases, one per requirement. This document
records what each one became once the sprints were over, and how it
executed. It exists because the Test Plan was baselined on 2 October,
before either sprint, and two things changed afterwards: most cases moved
from manual execution to the automated suite, and eleven could not be
executed at all.

Where a case is anything other than a clean pass, the reason is in the
row and the detail is in `docs/DEVIATIONS.md`.

---

## Summary

| Verdict | Cases |
|---|---|
| Pass | 23 |
| Pass, with a documented deviation | 5 |
| Partial — the case's main assertions pass, a named clause does not | 3 |
| Not executed — blocked on unavailable infrastructure | 11 |
| **Total** | **42** |

**Against the Test Plan's exit criteria (section 5), the honest position:**

| Exit criterion | Met? |
|---|---|
| 100% of planned test cases executed | **No** — 31 of 42. The other 11 are blocked, not skipped; each is listed below with its Jira issue. |
| No critical defect open | Yes |
| Every High-priority functional requirement passes its acceptance criterion | Yes, for those executed |
| All seven security test cases pass, ZAP scan clean | **No** — TC-Sec-01 and TC-Sec-04 not executed; TC-Sec-05 and TC-Sec-07 partial. TC-Sec-02, TC-Sec-03 and TC-Sec-06 pass. No ZAP scan was run. |
| No non-functional requirement fails its measurement | None fails; four were never measured (TC-Perf-01, TC-Perf-02, TC-Ops-01, TC-UX-01) |
| Unit test line coverage at or above 70% | Yes — **93.8%**, and CI fails the build below 90% |

This is a release-readiness position of *not signed off*, and saying so
is the point of the document. The gap is infrastructure, not defects:
every executed case passes.

---

## Change from the Test Plan as baselined

Four changes affect how these cases run, and none of them was in the plan
of 2 October.

1. **Execution moved from manual to automated.** The Test Plan (section
   6) names pytest, Postman, Selenium, JMeter, ZAP and Axe. What exists
   is the Django test runner, run by GitHub Actions on every pull
   request. Selenium, JMeter, ZAP and Axe were never set up, which is why
   the UI-regression, performance, security-scan and accessibility cases
   are unexecuted. Postman was not needed: the API surface is exercised
   through the test client.
2. **CI did not exist for Sprint 1.** It arrived as T8-99 during Sprint
   2. Results recorded against Sprint 1 issues came from manual runs
   written into Jira comments. Only work from PR #19 onwards was
   machine-verified at the time, though the whole suite now runs on
   every change.
3. **Tests run against PostgreSQL 14, not SQLite.** `config.settings_local`
   runs on SQLite for convenience, but SQLite ignores the row-level lock
   in `generate_reference_id()` and treats `LIKE` as case-insensitive.
   The seven `DatabaseLevelAuditGuardTests` skip entirely on SQLite. CI
   runs on PostgreSQL so TC-Sec-06 is actually executed rather than
   silently skipped.
4. **One test class has no test case.** `TemplateCommentLeakTests`
   asserts that no page renders its own source comments. The Test Plan
   did not anticipate it, because the bug it guards against was found
   from a screenshot in Sprint 2 — twelve templates had been printing
   `{# ... #}` comments to users since Sprint 1, since Django's one-line
   comment syntax does not work across two lines. It is listed here
   rather than omitted, because an orphaned test is a gap in the plan,
   not in the suite.

---

## Functional requirements

| Req | Test case | Executed by | Verdict |
|---|---|---|---|
| SCMS-F-001 | TC-Auth-01 | — | **Not executed.** Registration with OTP needs an SMS gateway. T8-8. |
| SCMS-F-002 | TC-Auth-02 | `EmailLoginTests` (8), `SessionExpiryTests` (4) | Pass |
| SCMS-F-003 | TC-Auth-03 | `ViewAccessTests` (7), `ObjectLevelAccessTests` (9), `RoleAdminTests` (5), `ProfileTests` (2) | Pass |
| SCMS-F-004 | TC-Auth-04 | `PasswordResetTests` (10) | Pass |
| SCMS-F-005 | TC-Auth-05 | `AccountLockoutTests` (14) | **Pass, with deviation.** Behaves as specified. Locking by account means a third party who knows an e-mail can lock that account out; implemented anyway, trade-off recorded in `complaints/security.py`. |
| SCMS-F-006 | TC-Lodge-01 | `LodgeComplaintTests` (11) | Pass |
| SCMS-F-007 | TC-Lodge-02 | `ReferenceIdTests` (3) | Pass. The plan's 10,000-submission uniqueness run was not performed; uniqueness rests on a database constraint plus a row-level lock, and the lock is only effective on PostgreSQL. |
| SCMS-F-008 | TC-Lodge-03 | `AttachmentTests` (17) | **Pass, with deviation.** The 3-file cap applies to complainant evidence; resolution proof is counted separately, or a complainant with three files could make their own complaint unresolvable under SCMS-F-018. |
| SCMS-F-009 | TC-Lodge-04 | — | **Not executed.** Guest lodging needs an SMS gateway for OTP. T8-16. |
| SCMS-F-010 | TC-Lodge-05 | `DraftTests` (10) | **Pass, with deviation.** The 7-day purge is implemented and tested as `manage.py purge_expired_drafts`, but nothing schedules it. |
| SCMS-F-011 | TC-Route-01 | `RoutingTests` (2), `RoutingFlowTests` (8) | Pass |
| SCMS-F-012 | TC-Route-02 | `AssignmentTests` (18) | Pass |
| SCMS-F-013 | TC-Route-03 | `PriorityMatrixTests` (14) | **Pass, with deviation.** Priority comes from the category matrix and a complainant cannot set it; an admin override is recorded with old and new values. The lodging form originally accepted a priority, which made the field useless for triage. |
| SCMS-F-014 | TC-Route-04 | `SlaTests` (2) — SLA derivation only | **Not executed.** The SLA deadline is derived and tested; nothing escalates on breach, which needs a task scheduler. T8-21. |
| SCMS-F-015 | TC-Route-05 | `DuplicateDetectionTests` (12) | Pass |
| SCMS-F-016 | TC-Track-01 | `StatusTrackingTests` (18), `StatusModelTests` (3) | Pass |
| SCMS-F-017 | TC-Track-02 | `CommentTests` (15) | Pass |
| SCMS-F-018 | TC-Res-01 | `ResolutionTests` (15) | Pass |
| SCMS-F-019 | TC-Res-02 | `ReopenWindowTests` (4), `ReopenWindowFlowTests` (16) | **Pass, with deviation.** Reopen-once-within-7-days works. Auto-closure is `manage.py close_expired_complaints`, implemented and tested, but nothing schedules it. |
| SCMS-F-020 | TC-Res-03 | `RatingTests` (14) | Pass |
| SCMS-F-021 | TC-Notif-01 | — | **Not executed.** Needs a scheduler and an SMS gateway. T8-28. |
| SCMS-F-022 | TC-Notif-02 | — | **Not executed.** Needs a task scheduler. T8-29. |
| SCMS-F-023 | TC-Admin-01 | `MasterDataTests` (12) | Pass |
| SCMS-F-024 | TC-Admin-02 | `DeactivationTests` (5), `RoleAdminTests` (5) | Pass |
| SCMS-F-025 | TC-Rep-01 | `DashboardTests` (10) | Pass |
| SCMS-F-026 | TC-Rep-02 | `ExportTests` (7) | Pass |
| SCMS-F-027 | TC-Audit-01 | `AuditTrailTests` (2), `AuditTrailCompletenessTests` (11) | Pass |
| SCMS-F-028 | TC-Rep-03 | `SearchTests` (19) | Pass |

## Non-functional requirements

| Req | Test case | Executed by | Verdict |
|---|---|---|---|
| SCMS-NF-001 | TC-Perf-01 | — | **Not executed.** p95 under 2s at 200 users needs JMeter and a host. T8-36. |
| SCMS-NF-002 | TC-Ops-01 | — | **Not executed.** Uptime needs a deployed server and monitoring. T8-37. |
| SCMS-NF-003 | TC-Perf-02 | — | **Not executed.** Needs load testing and multiple instances. T8-38. |
| SCMS-NF-004 | TC-UX-01 | — | **Not executed.** WCAG 2.1 AA needs an accessibility scanner. T8-39. |
| SCMS-NF-005 | TC-Maint-01 | CI `flake8` step; coverage gate at `--fail-under=90` | Pass. 93.8% against the 70% required. The gate is set at 90, not 70, so the margin cannot quietly erode. |
| SCMS-NF-006 | TC-Port-01 | `DockerImageTests` (6), plus the CI `docker` job | Pass. CI builds the image, refuses it if it runs as root, starts the stack on PostgreSQL and smoke-tests the running application. |
| SCMS-NF-007 | TC-Audit-02 | `RetentionTests` (9) | **Partial.** Archive passes. Purge cannot be done by the application at all — see below. |

## Security requirements

| Req | Test case | Executed by | Verdict |
|---|---|---|---|
| SCMS-SR-001 | TC-Sec-01 | — | **Not executed.** TLS and HSTS need a deployed server with a certificate. T8-43. |
| SCMS-SR-002 | TC-Sec-02 | `PasswordHashingTests` (8) | Pass. Argon2id, and passwords absent from logs. |
| SCMS-SR-003 | TC-Sec-03 | `ObjectLevelAccessTests` (9), `UrlTamperingSweepTests` (7) | Pass. The sweep runs the tampering case across *every* complaint-scoped route, and includes a route-completeness guard so a new route added without a check fails the suite. |
| SCMS-SR-004 | TC-Sec-04 | — (protections present, not measured as specified) | **Not executed.** Django gives parameterised queries, output escaping and CSRF tokens, and every form validates server-side, so the protections are largely in place. The acceptance criterion names an OWASP ZAP scan, and no scan was run — so the case is recorded as unexecuted rather than passed on inference. T8-46. |
| SCMS-SR-005 | TC-Sec-05 | `SecureEvidenceStorageTests` (14) | **Partial.** Storage outside any served path, randomised filenames, content-sniffed type checks, `Content-Disposition: attachment`, `nosniff`; a `.php` upload is refused three ways. No malware scanning — no scanner is available. The integration point exists and is tested, and a test asserts none is configured so the gap stays visible. T8-100. |
| SCMS-SR-006 | TC-Sec-06 | `DatabaseLevelAuditGuardTests` (7), plus model and queryset guards | Pass, **on PostgreSQL only**. These seven skip on SQLite, which has no trigger to refuse the raw UPDATE and DELETE. They execute in CI. Protection is three-deep: model, queryset, and a database trigger that binds even a superuser. |
| SCMS-SR-007 | TC-Sec-07 | `RateLimitTests` (6) | **Partial.** Both acceptance assertions pass: the 6th submission in an hour and the 11th login in 5 minutes are refused. Two requirement clauses are unmet — "per mobile number" (no account has one until SCMS-F-001 exists) and "CAPTCHA on guest lodging" (no guest path until SCMS-F-009 exists). Counters also live in a per-process cache, so a multi-instance deployment multiplies the effective limit. T8-101. |

---

## The one case that cannot be made to pass

**TC-Audit-02 (SCMS-NF-007)** is not an incomplete implementation. It is
a conflict between two requirements, and the Test Plan could not have
known it when it was written.

NF-007 asks the application to archive and purge records after three
years. SCMS-SR-006 denies the application any UPDATE or DELETE on the
audit log, and complaint rows are `PROTECT`-ed by their audit rows. We
checked by trying:

```
can the application delete an audit row?   ValueError: Audit rows are
                                           append-only (SCMS-SR-006)
can the application delete the complaint?  ProtectedError
```

The application cannot purge, by design. A purge path inside the
application is exactly the capability SR-006 exists to deny.

`RetentionTests` therefore asserts the split that was adopted: the
application archives (`manage.py archive_expired_complaints`, which
writes JSON and verifies it by reading it back, and deletes nothing), and
a database administrator purges
(`deploy/purge_archived.sql`, which drops the trigger, deletes, and
restores the trigger inside one transaction). One test asserts that
`purge_is_available_to_the_application()` returns `False` — the case
passes by proving the application *cannot* do what NF-007 asks of it.

`docs/DEVIATIONS.md` section 2 recommends NF-007 be reworded to say the
purge is a database-administration procedure.

---

## How to re-run

```
cd src
coverage run --rcfile=../.coveragerc manage.py test complaints
coverage report --rcfile=../.coveragerc
```

Against PostgreSQL this executes all 365 tests with none skipped. Against
SQLite (`--settings=config.settings_local`) seven skip, and TC-Sec-06 is
then not executed. Every push and pull request runs the PostgreSQL
version automatically.
