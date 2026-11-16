

# SCMS Regression Testing Matrix



## 1. Purpose



This document maps important application change areas to the existing automated Django test classes.



The purpose is to help developers identify relevant regression tests after modifying application functionality.



This matrix is a test-selection reference. It does not claim that the tests listed here were executed for every code change.



## 2. How to use the matrix



1\. Identify the application module or functionality changed.

2\. Find the corresponding area in the matrix.

3\. Execute the relevant existing test classes.

4\. Review failures and investigate regressions.

5\. Execute the complete test suite before merging significant changes.



## 3. Regression test matrix



| Change area | Existing test classes |

|---|---|

| Authentication and login | EmailLoginTests, SessionExpiryTests, PasswordResetTests, AccountLockoutTests |

| User profiles and permissions | ProfileTests, ViewAccessTests, ObjectLevelAccessTests, RoleAdminTests |

| Complaint registration | LodgeComplaintTests, ReferenceIdTests, DraftTests |

| Complaint routing and assignment | RoutingTests, RoutingFlowTests, AssignmentTests, PriorityMatrixTests |

| Complaint status and tracking | StatusModelTests, StatusTrackingTests, CommentTests |

| Resolution and reopening | ResolutionTests, ReopenWindowTests, ReopenWindowFlowTests, RatingTests |

| Attachments and evidence security | AttachmentTests, SecureEvidenceStorageTests |

| Complaint search and reporting | SearchTests, DashboardTests, ExportTests |

| Administrative functionality | MasterDataTests, DeactivationTests, RoleAdminTests |

| Audit trail and retention | AuditTrailTests, AuditTrailCompletenessTests, DatabaseLevelAuditGuardTests, RetentionTests |

| Security and URL access | UrlTamperingSweepTests, ObjectLevelAccessTests, RateLimitTests |

| Duplicate complaint detection | DuplicateDetectionTests |

| Templates and presentation | TemplateCommentLeakTests |

| Docker and container configuration | DockerImageTests |



## 4. Running focused regression tests



Run commands from the `src` directory.



Use the local SQLite configuration for convenient local testing:



```cmd

python manage.py test complaints.tests.EmailLoginTests --settings=config.settings_local

```



Replace the class name with the relevant test class.



Examples:



Authentication:



```cmd

python manage.py test complaints.tests.PasswordResetTests --settings=config.settings_local

```



Complaint assignment:



```cmd

python manage.py test complaints.tests.AssignmentTests --settings=config.settings_local

```



Audit protection:



```cmd

python manage.py test complaints.tests.DatabaseLevelAuditGuardTests --settings=config.settings_local

```



Important: DatabaseLevelAuditGuardTests requires PostgreSQL-specific behaviour. SQLite execution cannot validate those database-level protections.



## 5. Full regression suite



After significant changes, run the complete test suite:



```cmd

python manage.py test complaints --settings=config.settings_local

```



For deployment-equivalent validation, execute the complete suite against PostgreSQL 14 through the CI workflow.



## 6. Regression testing guidelines



- Run relevant focused tests after modifying a specific feature.

- Run permission-related tests after changing authentication or authorization logic.

- Run attachment security tests after modifying upload or download handling.

- Run audit tests after modifying audit-related models or database protections.

- Run database-specific tests against PostgreSQL.

- Run the complete suite before merging significant changes.

- Investigate skipped tests rather than treating them as successful executions.

- Record the commit SHA and test results when reporting regression evidence.



## 7. Limitations



This matrix is based on the existing test class names and documented test coverage.



It is a guide for selecting tests, not a complete dependency map of every internal function.



A single application change may affect multiple areas and require additional tests beyond the classes listed here.



## 8. References



- `src/complaints/tests.py`

- `docs/reports/test-cases.md`

- `docs/reports/test-case-execution.md`

- `docs/reports/test-and-coverage-report.md`

- `docs/testing/local-test-guide.md`

- `docs/testing/release-qa-checklist.md`



