

\# Frontend UI Validation Guide

## Smart Complaint Management System (SCMS)



**Contributor:** Vinay Varma  

**Role:** Frontend Developer and QA Lead  

**Project:** SCMS Team 8



## 1. Objective



This document provides a manual validation guide for the SCMS frontend. It covers complaint management pages, authentication pages, form inputs, validation messages and user interactions.



The objective is to ensure that users can interact with the application correctly and understand validation errors.



## 2. Frontend Pages



The application includes the following Django templates:



| Page | Template |

|---|---|

| Base layout | base.html |

| Complaint creation | complaint\_form.html |

| Complaint listing | complaint\_list.html |

| Complaint details | complaint\_detail.html |

| Complaint assignment | complaint\_assign.html |

| Attachment upload | complaint\_attachment\_form.html |

| Priority management | complaint\_priority.html |

| Complaint resolution | complaint\_resolve.html |

| Status management | complaint\_status.html |

| Dashboard | dashboard.html |

| Department queue | department\_queue.html |

| Login | login.html |



Password reset pages are also available under the registration templates.



## 3. Form Validation Checks



### 3.1 Complaint Creation



Fields:

\- Subject

\- Category

\- Sub-category

\- Location

\- Description



Validation requirements:

\- Required fields must not be empty.

\- Description must not exceed 2,000 characters.

\- Location must not exceed 200 characters.

\- Sub-category must belong to the selected category.

\- Inactive categories and sub-categories should not be offered.



### 3.2 Login



Validation requirements:

\- Accept a valid email address format.

\- Display an appropriate message for incorrect credentials.

\- Display a clear message when login attempts are rate-limited.

\- Show the account lockout message when applicable.



### 3.3 Complaint Search



Check the following filters:

\- Keyword

\- Reference ID

\- Status

\- Category

\- Department

\- Date range



The application should reject a date range where the starting date is later than the ending date.



### 3.4 Complaint Assignment



Validation requirements:

\- Only eligible agents should be available.

\- The current assigned agent should not be selected again.

\- A reason must be provided when reassigning an already assigned complaint.



### 3.5 Attachment Upload



Validation requirements:

\- An attachment must be selected.

\- Supported formats are JPG, PNG and PDF.

\- The documented upload limit is 5 MB.

\- The documented maximum is three attachments per complaint.



### 3.6 Priority Override



Validation requirements:

\- A valid priority must be selected.

\- A reason must be provided.

\- Selecting the existing priority should be rejected.



### 3.7 Complaint Resolution



Validation requirements:

\- Resolution remarks are required.

\- Remarks must satisfy the minimum length configured by the application.

\- Proof of resolution must be uploaded.

\- Invalid uploads should display an appropriate validation message.



### 3.8 Comments and Status Updates



Check that:

\- Empty comments are rejected.

\- Comments respect the configured character limit.

\- Only allowed complaint status transitions are offered.

\- Optional status comments can be entered.



### 3.9 Rating and Reopening



Check that:

\- Complaint ratings accept values from 1 to 5.

\- Rating comments do not exceed 1,000 characters.

\- Reopening reasons are optional and limited to 1,000 characters.



## 4. General UI Validation



The following manual checks should be performed:



1\. Verify that page titles and form labels are understandable.

2\. Check that required fields are clearly identified.

3\. Verify that validation errors appear near the relevant fields.

4\. Check that navigation links open the correct pages.

5\. Verify that forms can be submitted using the expected controls.

6\. Check that success and error messages are understandable.

7\. Verify that tables and complaint details remain readable.

8\. Check keyboard navigation and basic accessibility.

9\. Check that pages remain usable at different browser window sizes.



## 5. Manual Test Execution Record



Record the actual outcome when each check is performed.



| Test Area | Expected Result | Execution Status |

|---|---|---|

| Complaint creation | Required fields validated | Not yet recorded |

| Login | Appropriate authentication errors displayed | Not yet recorded |

| Complaint search | Filters and date validation work | Not yet recorded |

| Complaint assignment | Eligible agents and reassignment rules enforced | Not yet recorded |

| Attachment upload | Upload restrictions enforced | Not yet recorded |

| Priority override | Valid priority and reason required | Not yet recorded |

| Complaint resolution | Remarks and proof required | Not yet recorded |

| Comments | Empty and oversized comments rejected | Not yet recorded |

| Rating | Rating restricted to 1–5 | Not yet recorded |



## 6. Conclusion



This guide provides a structured approach to frontend validation for the Smart Complaint Management System.



It covers the main complaint workflows, authentication pages, form validation and general user interface checks.



Actual manual test outcomes should be recorded after executing the checks in the application.
