# scms-team8
Smart Complaint Management System — Software Engineering mini project, Team 8

# Smart Complaint Management System (SCMS)

A web platform for lodging and tracking complaints, with departmental
routing, SLA-based escalation and admin resolution.

Software Engineering mini project — **Team 8**

---

## Team

| Name | Role | SRN |
|------|------|-----|
| Aryaman D K | Product Owner / Team Lead | PES1UG24AM344 |
| Vikas Gowda L | Backend Developer | PES1UG24AM329 |
| V S R Vinay Varma | Frontend Developer | PES1UG24AM317 |
| Shridhar Umesh Golipalle | QA Lead / DevOps | PES1UG25AM814 |

---

## Problem statement

Complaints raised through informal channels are easily lost, rarely
acknowledged, and almost never traceable. SCMS gives every complaint a
reference ID, an accountable owner, a deadline and an audit trail, so
that both the complainant and the department can see exactly where a
complaint stands.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, Django 5.x, Django REST Framework |
| Database | PostgreSQL 14+ |
| Frontend | Django templates, Bootstrap 5 (responsive) |
| Async / scheduled tasks | Celery with Redis, Celery Beat |
| Notifications | SMTP e-mail, HTTP SMS gateway |

---

## Repository structure

```
scms-team8/
├── .github/workflows/ci.yml  Lint, migration check and tests on every push
├── docs/                     SRS, SAD and Test Plan
├── requirements.txt          What the application needs to run
├── requirements-dev.txt      The above, plus the linter
└── src/
    ├── manage.py
    ├── config/               Django project settings and root URLs
    │   ├── settings.py       PostgreSQL, the deployment target
    │   └── settings_local.py SQLite, for running on a dev machine
    └── complaints/           The application
        ├── models.py         Complaint, Category, attachments, audit trail
        ├── services.py       Reference IDs, routing, SLA, state machine
        ├── roles.py          The four SRS roles
        ├── permissions.py    Deny-by-default authorisation
        ├── attachments.py    Evidence validation (SCMS-F-008)
        ├── forms.py, views.py, urls.py, admin.py
        ├── migrations/
        ├── templates/
        └── tests.py
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

### Running the tests

On SQLite, which needs nothing installed:

```bash
cd src
python manage.py test complaints --settings=config.settings_local
```

On PostgreSQL, with a `.env` as above — slower to set up, but it is
what CI runs and what the application is deployed on:

```bash
cd src
python manage.py test complaints
```

Both should report **219 tests, OK**.

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
pull request. Each run does three things:

| Step | Catches |
|------|---------|
| `flake8 complaints config` | Style and unused-import errors (SCMS-NF-005) |
| `makemigrations --check --dry-run` | A model changed without its migration generated |
| `coverage run manage.py test complaints` | Any broken behaviour, on **PostgreSQL 14** |
| `coverage report --fail-under=90` | Test coverage slipping (SCMS-NF-005) |

The result shows as a tick or a cross on the pull request, so a change
that breaks the suite is caught before review rather than after merge.
Each run also publishes its test and coverage figures to the run's
summary page, including the **skip count** — some tests are
PostgreSQL-only, and a run where they silently skipped would
otherwise look identical to one where they passed.

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

---

## Documentation

| Document | Status |
|----------|--------|
| Software Requirements Specification (SRS) | v1.0 — submitted |
| Software Architecture Document (SAD) | v1.0 — submitted |
| Test Plan | v1.0 — submitted |

The SRS defines 28 functional requirements, 7 non-functional
requirements and 7 security requirements, with a full requirements
traceability matrix.

---

## Project tracking

Work is tracked in Jira (project key `T8`) as 7 epics covering 42
stories and tasks. Every issue is labelled with its SRS requirement ID
(for example `SCMS-F-001`) so the backlog traces directly back to the
specification.
