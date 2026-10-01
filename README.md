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










---

## Documentation

| Document | Status |
|----------|--------|
| Software Requirements Specification (SRS) | v1.0 — submitted |
| Software Architecture Document (SAD) | Not started |
| Test Plan | Not started |

The SRS defines 28 functional requirements, 7 non-functional
requirements and 7 security requirements, with a full requirements
traceability matrix.

---

## Project tracking

Work is tracked in Jira (project key `T8`) as 7 epics covering 42
stories and tasks. Every issue is labelled with its SRS requirement ID
(for example `SCMS-F-001`) so the backlog traces directly back to the
specification.
