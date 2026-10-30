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
python manage.py test complaints
```

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on every
pull request. Each run does three things:

| Step | Catches |
|------|---------|
| `flake8 complaints config` | Style and unused-import errors |
| `makemigrations --check --dry-run` | A model changed without its migration generated |
| `python manage.py test complaints` | Any broken behaviour, on **PostgreSQL 14** |

The result shows as a tick or a cross on the pull request, so a change
that breaks the suite is caught before review rather than after merge.

Two deliberate choices:

**It runs on PostgreSQL, not SQLite.** `settings_local` exists for
convenience, but SQLite is not equivalent to the deployment target. It
silently ignores the row-level lock in `generate_reference_id()` and
treats `LIKE` as case-insensitive where PostgreSQL does not, so a test
can pass on SQLite for a reason that does not hold in production.

**It runs on Python 3.11 and 3.13** — the floor the SAD commits to and
the version the team develops on. That makes the portability claim in
SCMS-NF-006 something the build tests rather than something the
document asserts.

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
