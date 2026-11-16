

\# Local Development and Test Execution Guide



\## 1. Purpose



This guide explains how to configure the local development environment, start the Smart Complaint Management System (SCMS), and execute its automated test suite.



The project uses Django and supports two database configurations:



\- PostgreSQL: the deployment and CI database.

\- SQLite: a lightweight local development database.



\## 2. Prerequisites



\- Python installed.

\- Git installed.

\- A terminal such as Command Prompt.

\- Project dependencies listed in `requirements.txt` and `requirements-dev.txt`.



The CI pipeline runs tests using Python 3.11 and 3.13 with PostgreSQL 14.



\## 3. Set up the Python environment



From the repository root:



```cmd

python -m venv .venv

.venv\\Scripts\\activate

```



Install development dependencies:



```cmd

pip install -r requirements-dev.txt

```



\## 4. Configure the local database



The project provides `config.settings\_local`, which configures SQLite.



The SQLite database file is created at the repository root:



`scms-local.sqlite3`



It is intended for local development and is Git-ignored.



No PostgreSQL server is required when using this configuration.



\## 5. Apply database migrations



Move into the Django source directory:



```cmd

cd src

```



Run:



```cmd

python manage.py migrate --settings=config.settings\_local

```



\## 6. Create an administrator account



To create a local Django administrator:



```cmd

python manage.py createsuperuser --settings=config.settings\_local

```



Follow the terminal prompts.



\## 7. Start the application



Run the development server:



```cmd

python manage.py runserver --settings=config.settings\_local

```



The application is available at:



`http://127.0.0.1:8000/`



Stop the server using `Ctrl+C`.



\## 8. Execute automated tests



From the `src` directory:



```cmd

python manage.py test complaints --settings=config.settings\_local

```



This executes the complaints application's automated Django tests using SQLite.



\### Important database limitation



SQLite does not implement all PostgreSQL database behaviours.



In particular, the seven `DatabaseLevelAuditGuardTests` are PostgreSQL-specific and are skipped when running against SQLite.



Therefore:



\- SQLite results are useful for local development.

\- SQLite results do not prove that PostgreSQL-specific database protections work.

\- PostgreSQL CI results remain the authoritative results for the complete test suite.



\## 9. Generate a coverage report



From the `src` directory:



```cmd

coverage run --rcfile=../.coveragerc manage.py test complaints --settings=config.settings\_local

```



Generate the report:



```cmd

coverage report --rcfile=../.coveragerc

```



The project's CI coverage gate is configured separately from the local SQLite test execution.



\## 10. Run code quality checks



From the repository root:



```cmd

.venv\\Scripts\\python.exe -m flake8 complaints config

```



Run the Django migration consistency check from the `src` directory:



```cmd

python manage.py makemigrations --check --dry-run --settings=config.settings\_local

```



\## 11. Troubleshooting



| Problem | Possible cause | Recommended action |

|---|---|---|

| PostgreSQL connection timeout | Default settings are attempting to connect to PostgreSQL | Use `--settings=config.settings\_local` for local SQLite development |

| Module not found | Dependencies are missing | Activate `.venv` and install `requirements-dev.txt` |

| Database table does not exist | Migrations have not been applied | Run the migration command |

| Port 8000 is already in use | Another process is using the port | Stop the other server or use `runserver 8001` |

| PostgreSQL-specific tests are skipped | SQLite does not support the required database behaviour | Refer to PostgreSQL CI execution results |



\## 12. Important notes



\- Do not commit `.venv` or `scms-local.sqlite3`.

\- Do not store production credentials or secret keys in source control.

\- Use PostgreSQL 14 for deployment-equivalent testing.

\- Use SQLite for convenient local development, while recognising its limitations.



\## 13. References



\- `src/config/settings.py`

\- `src/config/settings\_local.py`

\- `requirements.txt`

\- `requirements-dev.txt`

\- `.coveragerc`

\- `docs/reports/test-and-coverage-report.md`

\- `docs/reports/test-case-execution.md`



