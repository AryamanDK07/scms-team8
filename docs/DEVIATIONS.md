# Deviations from the SRS and SAD

Smart Complaint Management System — Team 8
As of 6 November 2026, at the close of Sprint 2.

This document records every place where what was built differs from
what the SRS v1.0 and SAD v1.0 describe, and why. It exists so that
the demo and the viva can speak to these deliberately rather than
discovering them in the room.

Nothing here is hidden in the code: each item is also commented at
the place it occurs, and most carry a test that will fail if someone
later closes the gap without updating the record.

---

## 1. Requirements status at a glance

| | Count |
|---|---|
| Fully met | 23 |
| Met with a deviation, documented below | 5 |
| Partially met, remainder tracked as its own issue | 3 |
| Not implemented, blocked on unavailable infrastructure | 11 |
| **Total requirements in the SRS** | **42** |

42 = 28 functional + 7 non-functional + 7 security.

Evidence: 365 automated tests, 93.8% line coverage, running on
PostgreSQL 14 under CI on every pull request, plus a container build
and smoke test.

---

## 2. The one requirements conflict

### SCMS-NF-007 cannot be satisfied together with SCMS-SR-006

**SCMS-NF-007** — retain records for 3 years, then "archive and purge
by a scheduled job."

**SCMS-SR-006** — the audit log is append-only; the application
database account holds no UPDATE or DELETE privilege on it.

These contradict each other. We confirmed it by trying, not by
reading:

```
can the application delete an audit row?   ValueError: Audit rows are
                                           append-only (SCMS-SR-006)
can the application delete the complaint?  ProtectedError
```

The audit rows are protected three ways (model, queryset, and a
PostgreSQL trigger from migration 0011), and complaint rows are
`PROTECT`-ed by their audit rows. **The application cannot purge.**

We treated this as correct rather than as a defect. An audit trail
the application can erase on a timer is not an audit trail, and a
purge path inside the application is precisely the capability
SCMS-SR-006 exists to deny.

**Resolution adopted — split the two halves by privilege:**

| Half | Who | How |
|---|---|---|
| Archive | the application | `manage.py archive_expired_complaints <path>` writes every expired complaint with its full audit trail to JSON, verifies the file by reading it back, and deletes nothing |
| Purge | a database administrator | `deploy/purge_archived.sql` drops the trigger, deletes, and restores the trigger in one transaction |

**Action required on the SRS:** NF-007 should be reworded to say the
purge is a database-administration procedure, not an application
feature. Until it is, the two requirements read as contradictory and
whoever implements this next will either weaken SR-006 or abandon
NF-007.

---

## 3. Partially met requirements

Each has its remainder tracked as a separate Jira issue, so nothing
is lost by closing the parent.

### SCMS-SR-005 — evidence storage (T8-47, remainder T8-100)

Met: uploads stored outside any served path, randomised filenames,
content-sniffed type validation, extension agreement, `Content-
Disposition: attachment`, and `X-Content-Type-Options: nosniff`.
A `.php` upload is rejected three ways and stored files have no URL.

**Not met:** malware scanning. No scanner is available to the
project. The integration point is built and tested —
`ATTACHMENT_MALWARE_SCANNER` takes a dotted path, every upload passes
through it, a flagged file is refused — but nothing is configured, so
no scanning happens.

We did not write a stub returning "clean". It would make the
requirement look met while giving no protection, and would stop
anyone looking again. A test asserts no scanner is configured, so
wiring one up later breaks that test deliberately.

### SCMS-SR-007 — rate limiting (T8-49, remainder T8-101)

Met: the 6th complaint submission in an hour and the 11th login
attempt from an IP in 5 minutes are both refused. Both acceptance
criteria pass.

**Not met, two clauses:**

- *"per mobile number"* — no account has a mobile number, because
  registration with OTP verification (SCMS-F-001) is not built. The
  limit is per account instead: real protection for a registered
  user, none against somebody creating accounts.
- *"CAPTCHA on guest lodging"* — guest lodging (SCMS-F-009) is not
  built, so there is no guest path to protect.

**Deployment limit:** counters live in Django's default per-process
cache. With more than one application instance each keeps its own
counters and the effective limit multiplies. A shared cache (Redis or
memcached) is needed, which is the same infrastructure SCMS-NF-003
requires.

### SCMS-NF-007 — retention (T8-42)

See section 2. The archive half is built and tested; the purge half
is a documented DBA procedure.

---

## 4. Met, but with a deviation worth stating

### SCMS-F-008 — "up to 3 attachments per complaint"

Read literally, the cap would include the agent's resolution proof.
A complainant who attaches three files would then make their own
complaint impossible to resolve, because SCMS-F-018 requires at least
one proof file and there would be nowhere to put it.

**Decision:** the cap applies to complainant evidence. Resolution
proof is stored with `kind=PROOF` and counted separately. A test
asserts three pieces of evidence do not block resolution.

**Action on the SRS:** add a clarifying line. The current wording and
the code can be made to look inconsistent by anyone reading strictly.

### SCMS-F-013 — priority is not the complainant's to set

The lodging form originally let the complainant choose the priority.
That makes the field useless for triage: anyone reporting a problem
has an incentive to mark it Critical.

**Decision:** priority comes from the category's matrix. A priority
submitted on the lodging form is ignored. A Department Admin or
System Admin may override it, with a mandatory reason, recorded in
the audit trail with the old and new values.

This is a correction towards the SRS's intent rather than away from
it, but the behaviour changed, so it is recorded.

### SCMS-F-005 — account lockout is a denial-of-service vector

Locking by account means anyone who knows a colleague's e-mail can
lock them out by failing to log in five times.

**Decision:** implemented as specified. It is the usual trade — the
alternative leaves the password as the only barrier — and the
IP-based limits of SCMS-SR-007 are the half that frustrates an
attacker without handing them a denial of service. The trade-off is
written into `complaints/security.py` rather than left implicit.

### SCMS-F-019 and SCMS-F-010 — the scheduled jobs are not scheduled

Auto-closure after the reopen window, and purging drafts after 7
days, are both implemented and tested as management commands:

```
python manage.py close_expired_complaints [--dry-run]
python manage.py purge_expired_drafts
```

**Neither runs itself.** Scheduling them is deployment configuration
— cron, or Celery Beat once SCMS-F-021 brings Celery in. The
behaviour the requirements describe is implemented and tested; the
scheduling is not, and should not be assumed from a green test.

### SAD — SQLite added for local development

The SAD specifies PostgreSQL 14+. That remains the deployment target
and is what CI tests against. `config/settings_local.py` was added so
a developer can run the application without installing a database
server first.

**Known difference:** SQLite silently ignores the row-level lock in
`generate_reference_id()`, and treats `LIKE` as case-insensitive
where PostgreSQL does not. This is why CI runs on PostgreSQL: a test
can pass on SQLite for a reason that does not hold in production. The
SCMS-SR-006 database tests skip on SQLite entirely and run only under
CI.

---

## 5. Not implemented

Eleven requirements were not attempted. Each is blocked on
infrastructure the project does not have, not on effort. All are
labelled `blocked-infrastructure` in Jira with a specific reason.

| Requirement | Issue | Blocked on |
|---|---|---|
| SCMS-F-001 Registration with OTP | T8-8 | An SMS gateway |
| SCMS-F-009 Guest lodging | T8-16 | An SMS gateway |
| SCMS-F-014 SLA escalation | T8-21 | A task scheduler |
| SCMS-F-021 E-mail and SMS notifications | T8-28 | Scheduler and SMS gateway |
| SCMS-F-022 Notification retry | T8-29 | A task scheduler |
| SCMS-NF-001 p95 under 2s at 200 users | T8-36 | JMeter and a host to run it |
| SCMS-NF-002 99.5% uptime | T8-37 | A deployed server and uptime monitoring |
| SCMS-NF-003 50,000/year, 500 sessions | T8-38 | Load testing and multiple instances |
| SCMS-NF-004 WCAG 2.1 AA | T8-39 | An accessibility scanner |
| SCMS-SR-001 TLS 1.2+, HSTS | T8-43 | A deployed server with a certificate |
| SCMS-SR-004 Input validation, CSRF, escaping | T8-46 | OWASP ZAP for the scan |

**SCMS-SR-004 deserves a note.** Django provides parameterised
queries, output escaping and CSRF tokens by default, and every form
validates server-side, so the *protections* are largely present. What
is absent is the ZAP scan the acceptance criterion names as the
measurement. It is listed as not implemented because the requirement
is written in terms of that evidence, and we did not produce it.

**SCMS-F-001 is the most visible gap in a demo.** There is currently
no self-service registration: accounts are created by an
administrator. A citizen cannot sign themselves up.

---

## 6. Additions not in the SRS

Two things were built that no requirement asked for.

### Continuous integration (T8-99)

GitHub Actions runs flake8, a missing-migration check, the full test
suite against PostgreSQL 14 on Python 3.11 and 3.13, a coverage gate,
and a container build with a smoke test — on every push and pull
request, with the figures posted onto each pull request.

Added because Sprint 1 ran with no automated verification at all:
every test result was produced by hand and recorded in a Jira
comment, which is a record rather than a check.

### Append-only enforcement beyond the requirement (T8-48)

SCMS-SR-006 asks for the application's database role to hold no
UPDATE or DELETE on the audit table. We additionally added a database
trigger refusing both from *any* connection.

A trigger is stronger than the privilege grant: a superuser is exempt
from privileges but not from a trigger. The privilege script
(`deploy/audit_privileges.sql`) is kept as well, for defence in depth,
and documents the trap that makes most such scripts useless — revoking
a privilege from the role that *owns* the table achieves nothing.

---

## 7. Known weaknesses in our own process

Recorded because the viva asks the team to explain its Agile process
honestly.

- **Sprint 1 had no CI.** The pipeline was built afterwards. Every
  test figure recorded against those fifteen issues came from a
  manual run written into a Jira comment. Only work from PR #19
  onwards is machine-verified.
- **Pull requests were not raised by their assigned owners.** 32 of
  the 33 merged pull requests came from a single account, while the
  issues are assigned across four people. The board and the commit
  history tell different stories about who did what.
- **Review was not independent.** The same person authored and merged
  most changes. The process has a review step and it was followed,
  but an author approving their own change catches less than a second
  pair of eyes.
- **A rendering bug sat unnoticed from Sprint 1 until Sprint 2.**
  Twelve templates printed their own source comments to users,
  because Django's `{# #}` comment only works on a single line. It
  was found from a screenshot, not from the test suite — every
  template test asserted that certain strings were *present*, and
  none asserted that nothing unwanted was.
