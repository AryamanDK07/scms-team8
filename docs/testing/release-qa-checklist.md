

\# SCMS Release QA Checklist



\## 1. Purpose



This checklist provides a structured set of quality assurance checks before releasing the Smart Complaint Management System (SCMS).



It supplements the existing Test Plan and test-case execution report. It does not replace automated tests or the formal requirements traceability matrix.



\## 2. Checklist status



Use the following status values:



\- \[ ] Not checked

\- \[x] Verified with available evidence

\- \[!] Blocked or partially verified



Do not mark a check as complete without supporting evidence.



\## 3. Functional testing



\- \[ ] Verify user registration and authentication.

\- \[ ] Verify email-based login and password reset.

\- \[ ] Verify role-based access for COMPLAINANT, SUPPORT_AGENT, DEPARTMENT_ADMIN and SYSTEM_ADMIN.

\- \[ ] Verify complaint creation and reference ID generation.

\- \[ ] Verify complaint assignment and routing.

\- \[ ] Verify complaint status tracking.

\- \[ ] Verify comments and complaint resolution.

\- \[ ] Verify reopening and rating workflows.

\- \[ ] Verify administrative master-data operations.

\- \[ ] Verify reporting and complaint exports.



Evidence: Django automated test results and the test-case execution report.



\## 4. Security testing



\- \[ ] Verify password hashing uses Argon2id.

\- \[ ] Verify unauthorised users cannot access restricted complaint records.

\- \[ ] Verify URL tampering does not bypass object-level permissions.

\- \[ ] Verify CSRF protection and server-side form validation.

\- \[ ] Verify evidence attachments are stored outside the web root.

\- \[ ] Verify attachment download permissions.

\- \[ ] Verify rate-limiting behaviour.

\- \[ ] Verify audit records cannot be modified or deleted through application access.

\- \[ ] Perform an OWASP ZAP security scan before claiming scan-based security validation.

\- \[ ] Verify production secrets are not exposed in source control.



Important: The existing test execution report records some security requirements as partial or not executed. Passing individual automated tests does not automatically mean every security acceptance criterion is satisfied.



\## 5. Performance and non-functional testing



\- \[ ] Execute load testing against the required concurrent-user target.

\- \[ ] Measure response time and verify the p95 target.

\- \[ ] Verify behaviour under multiple application instances.

\- \[ ] Verify uptime and monitoring on a deployed environment.

\- \[ ] Perform accessibility testing against WCAG 2.1 AA.

\- \[ ] Record test environment, workload, measurements and results.



Do not mark these checks as passed without actual measurements.



\## 6. Database and deployment testing



\- \[ ] Verify migrations apply successfully.

\- \[ ] Verify no unexpected model changes are pending.

\- \[ ] Verify PostgreSQL 14 compatibility.

\- \[ ] Verify PostgreSQL-specific audit protection tests execute.

\- \[ ] Verify the Docker image builds successfully.

\- \[ ] Verify the application container does not run as root.

\- \[ ] Verify PostgreSQL health checks and application startup.

\- \[ ] Verify application smoke tests after deployment.

\- \[ ] Verify media storage configuration.



SQLite is suitable for convenient local development, but it does not replace PostgreSQL validation for database-specific behaviour.



\## 7. Code quality and automated checks



\- \[ ] Run the Django automated test suite.

\- \[ ] Review test failures and errors.

\- \[ ] Review skipped tests and their reasons.

\- \[ ] Run Flake8.

\- \[ ] Run the migration consistency check.

\- \[ ] Generate and review the coverage report.

\- \[ ] Confirm the CI coverage threshold is satisfied.

\- \[ ] Review CI workflow results.



\## 8. Existing project evidence



According to the documented CI run on 6 November 2026:



| Metric | Recorded result |

|---|---|

| Automated tests | 365 |

| Failures | 0 |

| Errors | 0 |

| Skipped on PostgreSQL CI | 0 |

| Line coverage | 93.8% |

| CI Python versions | 3.11 and 3.13 |

| CI database | PostgreSQL 14 |



The test-case execution report records:



| Test-case status | Count |

|---|---:|

| Pass | 23 |

| Pass with documented deviation | 5 |

| Partial | 3 |

| Not executed | 11 |

| Total | 42 |



These are historical results from the documented CI run, not a claim that a new release test has been performed.



\## 9. Release sign-off



Before approving a release:



\- \[ ] All required test cases have an execution result or an approved documented exception.

\- \[ ] Critical defects are resolved.

\- \[ ] High-priority requirements satisfy their acceptance criteria.

\- \[ ] Security gaps have been reviewed.

\- \[ ] Performance and accessibility requirements have been evaluated.

\- \[ ] Deployment checks have been completed.

\- \[ ] Known deviations and limitations are documented.

\- \[ ] QA evidence is available for review.

\- \[ ] An authorised reviewer approves release readiness.



\## 10. Current release-readiness reference



The existing `docs/reports/test-case-execution.md` explicitly records the project as not signed off for release because several planned test cases remain unexecuted or partially executed.



This checklist must be updated with fresh evidence before any future release approval.



\## 11. Related documents



\- `docs/Test\_Plan\_Smart\_Complaint\_Management\_System\_Team8.pdf`

\- `docs/reports/test-cases.md`

\- `docs/reports/test-case-execution.md`

\- `docs/reports/test-and-coverage-report.md`

\- `docs/DEVIATIONS.md`

\- `docs/testing/local-test-guide.md`



