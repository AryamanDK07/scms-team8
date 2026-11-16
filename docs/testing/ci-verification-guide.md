

\# CI Pipeline Verification Guide



\## 1. Purpose



This document explains how to verify and interpret the Continuous Integration (CI) workflow of the Smart Complaint Management System (SCMS).



The workflow is defined in:



`.github/workflows/ci.yml`



It validates automated tests, code quality, database migrations, coverage and container deployment.



\## 2. CI triggers



The workflow runs when:



\- Code is pushed to the `main` branch.

\- A pull request is created or updated.



Multiple runs for the same Git reference are controlled through concurrency settings. Older runs can be cancelled when a newer push makes their results obsolete.



\## 3. Test job



The test job runs on Ubuntu and uses a Python version matrix:



\- Python 3.11

\- Python 3.13



Both versions execute against PostgreSQL 14.



\### Verification steps



1\. Open the GitHub repository.

2\. Navigate to the Actions tab.

3\. Select the CI workflow.

4\. Open the relevant workflow run.

5\. Inspect the test job for both Python versions.



\### Expected checks



\- Repository checkout succeeds.

\- Python setup succeeds.

\- Development dependencies install successfully.

\- Flake8 completes without errors.

\- Django migration consistency check succeeds.

\- Automated tests complete.

\- Coverage meets the configured threshold.



\## 4. Database verification



The CI test job starts a PostgreSQL 14 service.



The database connection is configured using environment variables:



\- DB\_NAME

\- DB\_USER

\- DB\_PASSWORD

\- DB\_HOST

\- DB\_PORT



A PostgreSQL health check is used to ensure the database is ready before dependent steps execute.



The project also provides SQLite settings for local development. However, SQLite is not equivalent to PostgreSQL for all database-specific behaviour.



In particular, the PostgreSQL-only DatabaseLevelAuditGuardTests must execute against PostgreSQL to verify the database-level audit protections.



\## 5. Code quality verification



The workflow executes:



`flake8 complaints config`



This checks Python source files for configured linting issues.



It also runs:



`python manage.py makemigrations --check --dry-run`



This checks whether model changes require migrations that have not been created.



A successful migration check means no new migration file is required for the current model state.



\## 6. Test and coverage verification



The workflow executes:



`coverage run --rcfile=../.coveragerc manage.py test complaints`



The coverage report is then generated with:



`coverage report --rcfile=../.coveragerc --fail-under=90`



The configured CI coverage threshold is 90%.



A coverage result below this threshold causes the coverage step to fail.



The workflow also collects test and coverage output for the GitHub Actions run summary.



\## 7. Docker job



The Docker job validates the container deployment workflow independently of the test matrix.



It performs these operations:



1\. Builds the SCMS Docker image.

2\. Checks that the container runs as the `scms` user rather than root.

3\. Starts the application and PostgreSQL services.

4\. Waits for the login page to respond.

5\. Runs database migrations inside the application container.

6\. Performs smoke tests against the running application.

7\. Stops the stack after execution.



\### Smoke-test expectations



\- The login page returns HTTP 200.

\- The authenticated queue endpoint redirects unauthenticated users with HTTP 302.

\- The smoke-test command completes successfully.



\## 8. Understanding failures



| Failed CI step | What to investigate |

|---|---|

| Dependency installation | Requirements files and package availability |

| Lint | Flake8 output and source formatting |

| Migration check | Model changes and migration files |

| Automated tests | Django test output and database behaviour |

| Coverage gate | Missing test coverage and coverage threshold |

| Docker build | Dockerfile and build dependencies |

| Container user check | Dockerfile user configuration |

| Application startup | Container logs and database readiness |

| Smoke test | Application routing, authentication and database connectivity |



A failed CI step should be investigated using its actual logs. Do not assume that every failure is caused by application code.



\## 9. Recording CI evidence



For each reviewed CI run, record:



| Field | Evidence |

|---|---|

| Workflow run | GitHub Actions run URL |

| Commit | Commit SHA |

| Python version | Matrix version |

| Database | PostgreSQL version |

| Test result | Tests, failures, errors and skipped count |

| Coverage | Reported line coverage |

| Lint | Pass or fail |

| Migration check | Pass or fail |

| Docker job | Pass or fail |

| Overall result | Success or failure |



Historical CI results should not be presented as evidence of a newly tested commit.



\## 10. Previously documented CI baseline



The existing project report records a CI run from 6 November 2026:



\- 365 automated tests.

\- Zero failures.

\- Zero errors.

\- Zero skipped tests on PostgreSQL.

\- 93.8% line coverage.

\- Python 3.11 and 3.13.

\- PostgreSQL 14.



These figures describe that historical run only.



\## 11. Related documents



\- `.github/workflows/ci.yml`

\- `docs/reports/test-and-coverage-report.md`

\- `docs/reports/test-case-execution.md`

\- `docs/testing/local-test-guide.md`

\- `docs/testing/release-qa-checklist.md`



