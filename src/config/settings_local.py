"""Local development settings: a file-based database.

PostgreSQL 14+ remains the deployment target (SCMS-NF-006) and the
settings in config.settings are unchanged. This module exists so a
developer can run and demo the application without installing a
database server first, which is otherwise the longest step in
setting up a new machine.

The application code is database-agnostic -- it goes through the
Django ORM -- and the test suite already runs on SQLite, so what
you see locally behaves the same as it will on PostgreSQL. The one
difference worth knowing is that SQLite ignores the row-level lock
in services.generate_reference_id(); the reference ID is still
unique because the column carries a unique constraint, but the
lock itself is only exercised on PostgreSQL.

Use it by passing --settings to any management command:

    python manage.py migrate --settings=config.settings_local
    python manage.py runserver --settings=config.settings_local

The database file is scms-local.sqlite3 in the repository root and
is git-ignored, so it never reaches a pull request.
"""

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR.parent / "scms-local.sqlite3",
    }
}
