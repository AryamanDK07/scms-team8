# Smart Complaint Management System (SCMS)

[![CI](https://github.com/AryamanDK07/scms-team8/actions/workflows/ci.yml/badge.svg)](https://github.com/AryamanDK07/scms-team8/actions/workflows/ci.yml)

A web platform for lodging and tracking complaints, with departmental
routing, priority assignment, resolution with proof, and an append-only
audit trail.

Software Engineering mini project — **Team 8**

**Status:** both sprints complete, development frozen at tag
[`v1.0-sprint2`](https://github.com/AryamanDK07/scms-team8/tree/v1.0-sprint2).
365 automated tests, 0 failures, **93.8%** line coverage, running against
PostgreSQL 14 on every pull request.

---

## Team

| Name | Project role | Test-plan role | SRN |
|------|------|------|-----|
| Aryaman D K | Product Owner / Team Lead | Product Owner | PES1UG24AM344 |
| Vikas Gowda L | Backend Developer | Developer | PES1UG24AM329 |
| V S R Vinay Varma | Frontend Developer | QA Lead | PES1UG24AM317 |
| Shridhar Umesh Golipalle | DevOps | Test Engineer | PES1UG25AM814 |

Test-plan roles are those assigned in STP v1.0 section 9, which govern
the test effort specifically.

---

## Problem statement

Complaints raised through informal channels are easily lost, rarely
acknowledged, and almost never traceable. SCMS gives every complaint a
reference ID, an accountable owner, a deadline and an audit trail, so
that both the complainant and the department can see exactly where a
complaint stands.

---

## Tech stack

| Layer | Technology | Built? |
|-------|------------|--------|
| Backend | Python 3.11+, Django 5.2, Django REST Framework | Yes |
| Database | PostgreSQL 14+ (SQLite for local development) | Yes |
| Frontend | Django templates, Bootstrap 5 (responsive) | Yes |
| Password hashing | Argon2id | Yes |
| Reporting | CSV and PDF export (reportlab) | Yes |
| Deployment | Docker, docker-compose, gunicorn | Yes |
| CI | GitHub Actions | Yes |
| Async / scheduled tasks | Celery with Redis, Celery Beat | **No** — see below |
| Notifications | SMTP e-mail, HTTP SMS gateway | **No** — see below |

The last two rows are specified in the SAD but **not implemented**. No
SMS gateway or task scheduler was available to the project, which blocks
five requirements (registration OTP, guest lodging, SLA escalation, and
both notification requirements). Two features that would otherwise need
a scheduler — complaint auto-closure and draft purging — are implemented
and tested as management commands that a cron entry would call; nothing
currently calls them.

`docs/DEVIATIONS.md` records all of this in full. Nothing in this
repository claims a capability it does not have.

---

## Repository structure

```
scms-team8/
├── .github/workflows/ci.yml   Lint, migrations, tests and container build
├── .coveragerc                What is measured for coverage, and why
├── Dockerfile                 Two-stage build, runs as a non-root user
├── docker-compose.yml         Application plus PostgreSQL
├── deploy/
│   ├── audit_privileges.sql   SCMS-SR-006 privilege half (DBA)
│   └── purge_archived.sql     SCMS-NF-007 purge half (DBA)
├── docs/
│   ├── SRS / SAD / Test Plan  The three baselined documents
│   ├── DEVIATIONS.md          Where the build differs from them, and why
│   └── reports/               Test, coverage, execution and sprint reports
└── src/
    ├── manage.py
    ├── config/
    │   ├── settings.py        PostgreSQL, the deployment target
    │   └── settings_local.py  SQLite, for running on a dev machine
    └── complaints/
        ├── models.py          Complaint, Category, attachments, audit trail
        ├── services.py        Reference IDs, routing, SLA, state machine
        ├── roles.py           The four SRS roles
        ├── permissions.py     Deny-by-default authorisation
        ├── security.py        Account lockout (SCMS-F-005)
        ├── ratelimit.py       Submission and login limits (SCMS-SR-007)
        ├── attachments.py     Evidence validation (SCMS-F-008, SR-005)
        ├── retention.py       Three-year archive (SCMS-NF-007)
        ├── exports.py         CSV and PDF export (SCMS-F-026)
        ├── management/commands/
        │   ├── archive_expired_complaints.py
        │   ├── close_expired_complaints.py
        │   └── purge_expired_drafts.py
        ├── forms.py, views.py, urls.py, admin.py
        ├── migrations/        0011 adds the append-only audit trigger
        ├── templates/
        └── tests.py           365 tests
```

---

## Running it locally

Needs Python 3.11 or newer.

```bash
git clone https://github.com/AryamanDK07/scms-team8.git
cd scms-team8
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS or Linux
pip install -r requirements.txt
```

**Without installing a database server.** `config.settings_local`
uses a SQLite file, so there is nothing to install or configure:

```bash
cd src
python manage.py migrate --settings=config.settings_local
python manage.py createsuperuser --settings=config.settings_local
python manage.py runserver --settings=config.settings_local
```

Then open http://127.0.0.1:8000/. Sign in with the superuser you
just created; http://127.0.0.1:8000/admin/ is where you add the
departments and categories that complaints are routed by.

There is no self-service sign-up page. Registration requires OTP
verification of a mobile number (SCMS-F-001), which needs an SMS
gateway, so accounts are created by an administrator.

**Against PostgreSQL**, which is what the SAD specifies for
deployment: create the database and user, put the credentials in a
`.env` file in the repository root, and leave `--settings` off.

```
DB_NAME=scms_db
DB_USER=scms_user
DB_PASSWORD=your-password
DB_HOST=127.0.0.1
DB_PORT=5432
```

`.env` is git-ignored and must never be committed.

**In containers**, which is how SCMS-NF-006 is demonstrated:

```bash
docker compose up --build
docker compose exec app python manage.py migrate
```

The image builds in two stages and runs as a non-root `scms` user; CI
fails the build if it ever runs as root.

### Running the tests

On SQLite, which needs nothing installed:

```bash
cd src
python manage.py test complaints --settings=config.settings_local
```

Reports **365 tests, OK (skipped=7)**. The seven skips are the
`DatabaseLevelAuditGuardTests`, which assert that a database trigger
refuses a raw `UPDATE` or `DELETE` on the audit tables — SQLite has no
such trigger, so they cannot run there.

On PostgreSQL, with a `.env` as above — this is what CI runs, and the
only configuration in which every test actually executes:

```bash
cd src
python manage.py test complaints
```

Reports **365 tests, OK**, with nothing skipped.

### Checking it the way CI does

```bash
pip install -r requirements-dev.txt
cd src
flake8 complaints config
python manage.py makemigrations --check --dry-run
coverage run --rcfile=../.coveragerc manage.py test complaints
coverage report --rcfile=../.coveragerc
```

### Test coverage

SCMS-NF-005 requires at least 70% line coverage. The suite measures
**93.8%** over `complaints` and `config`, so CI gates at 90 rather
than 70: a gate at the minimum would let a quarter of that
protection disappear without anything objecting, while the
requirement still read as met.

`.coveragerc` explains what is excluded from the measurement and
why — chiefly `tests.py`, which would otherwise add around twenty
points of coverage that say nothing about how well the application
is tested.

### Audit log privileges (deployment)

The audit tables are append-only. Migration 0011 enforces that with
a database trigger, so it holds for every connection without any
setup. SCMS-SR-006 additionally requires the application's database
role to hold no UPDATE or DELETE privilege on those tables, which is
deployment configuration:

```bash
psql -d scms_db -f deploy/audit_privileges.sql
```

Read the comments in that file before running it. It creates a
second role, because revoking a privilege from the role that *owns*
a table achieves nothing — the owner can grant it straight back. A
single-role deployment meets the trigger half of SCMS-SR-006 but not
the privilege half, and the file says so rather than pretending
otherwise.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on every
pull request, as two jobs.

**`test`**, on a matrix of Python 3.11 and 3.13, against a PostgreSQL 14
service container:

| Step | Catches |
|------|---------|
| `flake8 complaints config` | Style and unused-import errors (SCMS-NF-005) |
| `makemigrations --check --dry-run` | A model changed without its migration generated |
| `coverage run manage.py test complaints` | Any broken behaviour, on **PostgreSQL 14** |
| `coverage report --fail-under=90` | Test coverage slipping (SCMS-NF-005) |

**`docker`**, which proves the portability claim rather than asserting
it: it builds the image, refuses it if it runs as root, starts the full
stack with PostgreSQL, waits for the login page, migrates, and smoke-tests
the running application.

Every run posts its test and coverage figures as a comment on the pull
request, including the **skip count** — some tests are PostgreSQL-only,
and a run where they silently skipped would otherwise look identical to
one where they passed.

Three deliberate choices:

**It runs on PostgreSQL, not SQLite.** `settings_local` exists for
convenience, but SQLite is not equivalent to the deployment target. It
silently ignores the row-level lock in `generate_reference_id()` and
treats `LIKE` as case-insensitive where PostgreSQL does not, so a test
can pass on SQLite for a reason that does not hold in production.

**It runs on Python 3.11 and 3.13** — the floor the SAD commits to and
the version the team develops on. That makes the portability claim in
SCMS-NF-006 something the build tests rather than something the
document asserts.

**The coverage gate is 90, not the required 70.** The suite measures
93.8%. A gate at the minimum would let a quarter of that protection
be lost without anything objecting, while SCMS-NF-005 still read as
met. Set near the current level, the gate guards what the suite
actually covers, and lowering it becomes a decision someone has to
make deliberately.

CI was added partway through Sprint 2, as issue T8-99. Sprint 1 ran
without it, and `docs/DEVIATIONS.md` section 7 says so.

---

## Documentation

| Document | What it is |
|----------|------------|
| [`docs/SRS_…pdf`](docs/) | Software Requirements Specification v1.0 — 28 functional, 7 non-functional and 7 security requirements, with a full traceability matrix |
| [`docs/SAD_…pdf`](docs/) | Software Architecture Document v1.0 — components, sequence diagrams, API design, error handling, security architecture |
| [`docs/Test_Plan_…pdf`](docs/) | Test Plan v1.0 — strategy, security validation, and 42 test cases, one per requirement |
| [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md) | **Every place the build differs from the SRS and SAD, and why** |
| [`docs/reports/test-case-execution.md`](docs/reports/test-case-execution.md) | Each of the 42 test cases, its verdict, and the tests that execute it |
| [`docs/reports/test-and-coverage-report.md`](docs/reports/test-and-coverage-report.md) | CI test and coverage figures, with the per-file breakdown |
| [`docs/reports/test-cases.md`](docs/reports/test-cases.md) | All 365 automated test cases, generated from the suite |
| [`docs/reports/sprint-summary.md`](docs/reports/sprint-summary.md) | Sprint velocity and what each sprint delivered |

---

## Requirement status

Of the 42 requirements in SRS v1.0:

| | Count |
|---|---|
| Fully met | 23 |
| Met, with a documented deviation | 5 |
| Partially met, remainder tracked as its own issue | 3 |
| Not implemented, blocked on unavailable infrastructure | 11 |

The eleven unimplemented requirements are blocked on an SMS gateway, a
task scheduler, load-testing and scanning tools, or a deployed server —
not on effort. Each is labelled `blocked-infrastructure` in Jira with a
second label naming exactly what it needs.

One pair of requirements is in direct conflict: **SCMS-NF-007** asks the
application to purge records after three years, and **SCMS-SR-006**
denies the application any `DELETE` on the audit log. The application
therefore cannot purge, by design; archive and purge are split by
privilege, and `docs/DEVIATIONS.md` section 2 sets out the reasoning.

---

## Project tracking

Work is tracked in Jira (project key `T8`) as 7 epics over 45 stories and
tasks, every one carrying a story-point estimate and its SRS requirement
ID as a label (for example `SCMS-F-001`), so the backlog traces directly
back to the specification.

| | Issues | Points |
|---|---|---|
| Sprint 1 (27 Oct – 3 Nov) | 15 | 51 |
| Sprint 2 (6 Nov – 13 Nov) | 17 | 65 |
| Backlog (blocked) | 13 | 69 |

Both sprints are closed. Every issue on the board is Done except the
thirteen backlog items described above.
