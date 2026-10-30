import logging
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from .attachments import MAX_BYTES
from .models import (
    AttachmentKind,
    Category,
    Complaint,
    ComplaintAttachment,
    ComplaintComment,
    ComplaintStatusHistory,
    Department,
    SecurityEvent,
    SecurityEventType,
    SubCategory,
    UserProfile,
)
from .security import LOCKOUT_THRESHOLD
from .services import (
    STATUS_TRANSITIONS,
    add_comment,
    assign_complaint,
    calculate_sla_deadline,
    can_reopen,
    comment_thread,
    eligible_agents,
    offerable_transitions,
    override_priority,
    priority_for,
    RESOLUTION_REMARKS_MIN,
    resolve_complaint,
    transition_status,
)


#: Names the stub scanners below were handed, for SCMS-SR-005 tests.
#: They live at module level because the scanner is configured by
#: dotted path, which import_string has to be able to resolve.
_SCANNER_CALLS = []


def _scanner_that_records(upload):
    _SCANNER_CALLS.append(upload.name)

    return None


def _scanner_that_flags(upload):
    return "EICAR-Test-Signature"


def _scanner_that_reads_everything(upload):
    upload.read()

    return None


class BaseFixture(TestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name="IT Support",
            code="it-support",
        )
        self.category = Category.objects.create(
            name="IT Support",
            code="it",
            department=self.department,
            sla_hours=24,
            default_priority=Complaint.Priority.HIGH,
        )
        self.user = get_user_model().objects.create_user(
            username="complainant",
            password="not-a-real-password",
        )

    def make_complaint(self, **kwargs):
        defaults = {
            "title": "Projector not working",
            "description": "The projector in room 302 does not switch on.",
            "category": self.category,
            "submitted_by": self.user,
        }
        defaults.update(kwargs)
        return Complaint.objects.create(**defaults)


class ReferenceIdTests(BaseFixture):
    """SCMS-F-007 / TC-Lodge-02."""

    def test_reference_id_matches_required_format(self):
        complaint = self.make_complaint()
        self.assertRegex(complaint.reference_id, r"^SCMS-\d{4}-\d{6}$")

    def test_reference_id_carries_current_year(self):
        complaint = self.make_complaint()
        year = timezone.now().year
        self.assertTrue(complaint.reference_id.startswith(f"SCMS-{year}-"))

    def test_reference_ids_are_sequential_and_unique(self):
        ids = [self.make_complaint().reference_id for _ in range(25)]
        self.assertEqual(len(set(ids)), 25)
        numbers = [int(re.split(r"-", i)[2]) for i in ids]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(numbers[-1] - numbers[0], 24)


class StatusModelTests(BaseFixture):
    """SCMS-F-016: the seven states named in the SRS."""

    def test_status_set_matches_srs(self):
        self.assertEqual(
            sorted(Complaint.Status.values),
            sorted([
                "OPEN", "ASSIGNED", "IN_PROGRESS", "PENDING_INFORMATION",
                "RESOLVED", "CLOSED", "REOPENED",
            ]),
        )

    def test_priority_set_matches_srs(self):
        self.assertEqual(
            sorted(Complaint.Priority.values),
            sorted(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
        )

    def test_new_complaint_starts_open(self):
        self.assertEqual(self.make_complaint().status, Complaint.Status.OPEN)


class SlaTests(BaseFixture):
    """SCMS-F-014: SLA comes from the category, not the priority."""

    def test_deadline_uses_category_sla_hours(self):
        start = timezone.now()
        deadline = calculate_sla_deadline(self.category, start_time=start)
        self.assertEqual(deadline, start + timedelta(hours=24))

    def test_different_categories_give_different_deadlines(self):
        slow = Category.objects.create(
            name="Facilities",
            code="facilities",
            department=self.department,
            sla_hours=72,
        )
        start = timezone.now()
        self.assertNotEqual(
            calculate_sla_deadline(self.category, start_time=start),
            calculate_sla_deadline(slow, start_time=start),
        )


class ReopenWindowTests(BaseFixture):
    """SCMS-F-019 / TC-Res-02: reopen once, within 7 days."""

    def resolved(self, days_ago, reopen_count=0):
        return self.make_complaint(
            status=Complaint.Status.RESOLVED,
            resolved_at=timezone.now() - timedelta(days=days_ago),
            reopen_count=reopen_count,
        )

    def test_can_reopen_on_day_six(self):
        self.assertTrue(can_reopen(self.resolved(6)))

    def test_cannot_reopen_on_day_eight(self):
        self.assertFalse(can_reopen(self.resolved(8)))

    def test_cannot_reopen_twice(self):
        self.assertFalse(can_reopen(self.resolved(2, reopen_count=1)))

    def test_cannot_reopen_an_open_complaint(self):
        self.assertFalse(can_reopen(self.make_complaint()))


class AuditTrailTests(BaseFixture):
    """SCMS-F-027 / SCMS-SR-006 / TC-Audit-01: append-only."""

    def entry(self):
        return ComplaintStatusHistory.objects.create(
            complaint=self.make_complaint(),
            old_status="",
            new_status=Complaint.Status.OPEN,
            changed_by=self.user,
        )

    def test_audit_row_cannot_be_updated(self):
        row = self.entry()
        row.new_status = Complaint.Status.CLOSED
        with self.assertRaises(ValueError):
            row.save()

    def test_audit_row_cannot_be_deleted(self):
        with self.assertRaises(ValueError):
            self.entry().delete()


class RoutingTests(BaseFixture):
    """SCMS-F-011 / ADR-06: routing comes from master data."""

    def test_category_carries_its_department(self):
        self.assertEqual(self.category.department, self.department)

    def test_changing_mapping_needs_no_code_change(self):
        other = Department.objects.create(name="Estates", code="estates")
        self.category.department = other
        self.category.save()
        self.category.refresh_from_db()
        self.assertEqual(self.category.department, other)


class EmailLoginTests(TestCase):
    """SCMS-F-002 / TC-Auth-02: sign in with e-mail and password."""

    LOGIN_URL = "/accounts/login/"
    PASSWORD = "not-a-real-password"

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username="complainant",
            email="asha@example.com",
            password=self.PASSWORD,
        )

    def login(self, email, password=None):
        return self.client.post(
            self.LOGIN_URL,
            {"username": email, "password": password or self.PASSWORD},
        )

    def logged_in_as(self):
        return self.client.session.get("_auth_user_id")

    def test_valid_email_and_password_creates_session(self):
        response = self.login("asha@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.logged_in_as(), str(self.user.pk))

    def test_email_is_case_insensitive(self):
        self.login("Asha@Example.COM")
        self.assertEqual(self.logged_in_as(), str(self.user.pk))

    def test_surrounding_whitespace_is_ignored(self):
        self.login("  asha@example.com  ")
        self.assertEqual(self.logged_in_as(), str(self.user.pk))

    def test_wrong_password_is_refused(self):
        self.login("asha@example.com", password="wrong-password")
        self.assertIsNone(self.logged_in_as())

    def test_unknown_email_is_refused(self):
        self.login("nobody@example.com")
        self.assertIsNone(self.logged_in_as())

    def test_username_is_not_accepted_in_place_of_email(self):
        """The form validates the field as an e-mail address."""
        response = self.login("complainant")
        self.assertIsNone(self.logged_in_as())
        self.assertFormError(
            response.context["form"],
            "username",
            "Enter a valid email address.",
        )

    def test_deactivated_account_cannot_log_in(self):
        """Supports SCMS-F-024: deactivation takes effect at login."""
        self.user.is_active = False
        self.user.save()
        self.login("asha@example.com")
        self.assertIsNone(self.logged_in_as())

    def test_duplicate_email_refuses_both_accounts(self):
        """An ambiguous e-mail must never resolve to either account."""
        self.User.objects.create_user(
            username="imposter",
            email="asha@example.com",
            password=self.PASSWORD,
        )
        self.login("asha@example.com")
        self.assertIsNone(self.logged_in_as())


class SessionExpiryTests(TestCase):
    """SCMS-F-002 / TC-Auth-02: 30 minutes of inactivity ends it."""

    IDLE_LIMIT = 30 * 60

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="complainant",
            email="asha@example.com",
            password="not-a-real-password",
        )

    def test_idle_window_is_thirty_minutes(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, self.IDLE_LIMIT)

    def test_expiry_is_measured_from_last_request(self):
        """Without this, the 30 minutes would run from login."""
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)

    def test_request_after_idle_window_is_no_longer_authenticated(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/").status_code, 200)

        # Age the stored session past the idle window.
        record = Session.objects.get(
            session_key=self.client.session.session_key,
        )
        record.expire_date = timezone.now() - timedelta(seconds=1)
        record.save()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_request_inside_idle_window_stays_authenticated(self):
        self.client.force_login(self.user)

        record = Session.objects.get(
            session_key=self.client.session.session_key,
        )
        record.expire_date = timezone.now() + timedelta(minutes=5)
        record.save()

        self.assertEqual(self.client.get("/").status_code, 200)


class PasswordResetTests(TestCase):
    """SCMS-F-004 / TC-Auth-04: single-use link, valid 15 minutes."""

    REQUEST_URL = "/accounts/password-reset/"
    OLD_PASSWORD = "not-a-real-password"
    NEW_PASSWORD = "Ts8-quarry-lantern"
    IDLE_LIMIT = 15 * 60

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="complainant",
            email="asha@example.com",
            password=self.OLD_PASSWORD,
        )

    def request_reset(self, email="asha@example.com"):
        return self.client.post(self.REQUEST_URL, {"email": email})

    def emailed_link(self):
        """The reset path from the message Django just sent."""
        body = mail.outbox[0].body
        match = re.search(r"/accounts/reset/[^\s]+/", body)
        self.assertIsNotNone(
            match, f"no reset link found in e-mail body:\n{body}",
        )
        return match.group(0)

    def set_password_via(self, link, password=None):
        """Walk the two-step confirm flow and submit a new password."""
        redirect = self.client.get(link)
        self.assertEqual(redirect.status_code, 302)

        new = password or self.NEW_PASSWORD
        return self.client.post(
            redirect["Location"],
            {"new_password1": new, "new_password2": new},
        )

    def test_link_validity_window_is_fifteen_minutes(self):
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, self.IDLE_LIMIT)

    def test_request_sends_one_email_with_a_link(self):
        response = self.request_reset()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("asha@example.com", mail.outbox[0].to)
        self.assertRegex(mail.outbox[0].body, r"/accounts/reset/")

    def test_unknown_address_sends_nothing_but_does_not_say_so(self):
        """No account enumeration: the page looks the same either way."""
        response = self.request_reset("nobody@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/password-reset/sent/")
        self.assertEqual(len(mail.outbox), 0)

    def test_link_lets_the_user_set_a_new_password(self):
        self.request_reset()
        self.set_password_via(self.emailed_link())

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW_PASSWORD))
        self.assertFalse(self.user.check_password(self.OLD_PASSWORD))

    def test_new_password_works_at_login(self):
        self.request_reset()
        self.set_password_via(self.emailed_link())

        self.client.post(
            "/accounts/login/",
            {
                "username": "asha@example.com",
                "password": self.NEW_PASSWORD,
            },
        )
        self.assertEqual(
            self.client.session.get("_auth_user_id"), str(self.user.pk),
        )

    def test_link_cannot_be_used_twice(self):
        self.request_reset()
        link = self.emailed_link()
        self.set_password_via(link)

        # Second visit, after the password has already changed.
        self.client.logout()
        response = self.client.get(link)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

    def test_second_link_cannot_undo_the_first_reset(self):
        """Reuse must not be possible by re-POSTing the set-password URL."""
        self.request_reset()
        link = self.emailed_link()
        redirect = self.client.get(link)
        set_password_url = redirect["Location"]

        self.client.post(
            set_password_url,
            {
                "new_password1": self.NEW_PASSWORD,
                "new_password2": self.NEW_PASSWORD,
            },
        )

        self.client.post(
            set_password_url,
            {
                "new_password1": "Zq4-harbour-kettle",
                "new_password2": "Zq4-harbour-kettle",
            },
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW_PASSWORD))
        self.assertFalse(self.user.check_password("Zq4-harbour-kettle"))

    def test_link_is_refused_after_fifteen_minutes(self):
        self.request_reset()
        link = self.emailed_link()

        # Validate the token as though 16 minutes had passed. The
        # token generator's clock is naive local time, so the fake
        # "now" has to be built the same way or the comparison skews
        # by the UTC offset.
        sixteen_minutes_on = datetime.now() + timedelta(minutes=16)

        with patch.object(
            PasswordResetTokenGenerator,
            "_now",
            return_value=sixteen_minutes_on,
        ):
            response = self.client.get(link)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD_PASSWORD))

    def test_link_still_works_inside_the_window(self):
        self.request_reset()
        link = self.emailed_link()

        fourteen_minutes_on = datetime.now() + timedelta(minutes=14)

        with patch.object(
            PasswordResetTokenGenerator,
            "_now",
            return_value=fourteen_minutes_on,
        ):
            response = self.client.get(link)

        self.assertEqual(response.status_code, 302)

    def test_tampered_token_is_refused(self):
        self.request_reset()
        link = self.emailed_link()
        tampered = link[:-6] + "aaaaa/"

        response = self.client.get(tampered)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])


class LodgeComplaintTests(BaseFixture):
    """SCMS-F-006 / TC-Lodge-01: mandatory fields, field-wise errors."""

    URL = "/new/"

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.sub_category = SubCategory.objects.create(
            name="Projector",
            code="projector",
            category=self.category,
        )

        # A sub-category under a different parent, for the
        # mismatched-pair case.
        self.other_category = Category.objects.create(
            name="Facilities",
            code="facilities",
            department=self.department,
        )
        self.other_sub_category = SubCategory.objects.create(
            name="Leaking Tap",
            code="leaking-tap",
            category=self.other_category,
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()
        self.client.force_login(self.user)

    def payload(self, **overrides):
        data = {
            "title": "Projector not working",
            "category": self.category.pk,
            "sub_category": self.sub_category.pk,
            "location": "Block C, Room 302",
            "description": "The projector in room 302 does not switch on.",
            "priority": Complaint.Priority.MEDIUM,
        }
        data.update(overrides)
        return data

    def test_valid_submission_is_saved_with_every_field(self):
        response = self.client.post(self.URL, self.payload())
        self.assertEqual(response.status_code, 302)

        complaint = Complaint.objects.get()
        self.assertEqual(complaint.title, "Projector not working")
        self.assertEqual(complaint.category, self.category)
        self.assertEqual(complaint.sub_category, self.sub_category)
        self.assertEqual(complaint.location, "Block C, Room 302")
        self.assertEqual(complaint.submitted_by, self.user)

    def assert_rejected_with_error_on(self, field, **overrides):
        """Nothing saved, and the error sits on the named field."""
        response = self.client.post(self.URL, self.payload(**overrides))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Complaint.objects.exists())

        form = response.context["form"]
        self.assertIn(
            field,
            form.errors,
            f"expected an error on {field}, got {dict(form.errors)}",
        )
        return form

    def test_subject_is_mandatory(self):
        self.assert_rejected_with_error_on("title", title="")

    def test_category_is_mandatory(self):
        self.assert_rejected_with_error_on("category", category="")

    def test_sub_category_is_mandatory(self):
        self.assert_rejected_with_error_on("sub_category", sub_category="")

    def test_location_is_mandatory(self):
        self.assert_rejected_with_error_on("location", location="")

    def test_description_is_mandatory(self):
        self.assert_rejected_with_error_on("description", description="")

    def test_description_over_two_thousand_characters_is_refused(self):
        """The browser's maxlength is bypassable, so check the server."""
        form = self.assert_rejected_with_error_on(
            "description", description="x" * 2001,
        )
        self.assertIn("2000", str(form.errors["description"]))

    def test_description_at_exactly_two_thousand_is_accepted(self):
        response = self.client.post(
            self.URL, self.payload(description="x" * 2000),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Complaint.objects.count(), 1)

    def test_sub_category_from_another_category_is_refused(self):
        form = self.assert_rejected_with_error_on(
            "sub_category", sub_category=self.other_sub_category.pk,
        )
        self.assertIn(
            "does not belong", str(form.errors["sub_category"]),
        )

    def test_several_invalid_fields_each_report_their_own_error(self):
        response = self.client.post(
            self.URL,
            self.payload(title="", location="", description=""),
        )

        form = response.context["form"]
        self.assertEqual(
            set(form.errors),
            {"title", "location", "description"},
        )

    def test_inactive_sub_category_is_not_offered(self):
        self.sub_category.is_active = False
        self.sub_category.save()

        response = self.client.get(self.URL)
        choices = response.context["form"].fields["sub_category"].queryset
        self.assertNotIn(self.sub_category, choices)


class RoutingFlowTests(BaseFixture):
    """SCMS-F-011 / TC-Route-01: lodging routes to the mapped queue.

    RoutingTests above covers the mapping as master data. This covers
    the path a real complaint takes: submitted by a complainant,
    appearing in the right department's queue and nowhere else.
    """

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.estates = Department.objects.create(
            name="Estates", code="estates",
        )
        self.sub_category = SubCategory.objects.create(
            name="Projector", code="projector", category=self.category,
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.it_admin = self.make_admin("itadmin", self.department)
        self.estates_admin = self.make_admin("estatesadmin", self.estates)

    def make_admin(self, username, department):
        from .roles import Role

        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = Role.DEPARTMENT_ADMIN
        profile.department = department
        profile.save()
        return user

    def lodge(self, category=None, sub_category=None):
        self.client.force_login(self.user)
        response = self.client.post("/new/", {
            "title": "Projector not working",
            "category": (category or self.category).pk,
            "sub_category": (sub_category or self.sub_category).pk,
            "location": "Block C, Room 302",
            "description": "The projector in room 302 does not switch on.",
            "priority": Complaint.Priority.MEDIUM,
        })
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        return Complaint.objects.latest("created_at")

    def queue_for(self, user):
        self.client.force_login(user)
        response = self.client.get("/queue/")
        self.assertEqual(response.status_code, 200)
        return response

    def test_lodging_assigns_the_mapped_department(self):
        complaint = self.lodge()
        self.assertEqual(complaint.assigned_department, self.department)

    def test_routing_happens_before_the_response_returns(self):
        """The 5-second criterion: assignment is synchronous."""
        complaint = self.lodge()
        complaint.refresh_from_db()
        self.assertIsNotNone(complaint.assigned_department)

    def test_complaint_appears_in_the_mapped_department_queue(self):
        complaint = self.lodge()
        self.assertContains(
            self.queue_for(self.it_admin), complaint.reference_id,
        )

    def test_complaint_is_absent_from_other_department_queues(self):
        complaint = self.lodge()
        self.assertNotContains(
            self.queue_for(self.estates_admin), complaint.reference_id,
        )

    def test_remapping_a_category_reroutes_later_complaints(self):
        """ADR-06: routing changes without touching code."""
        first = self.lodge()
        self.assertEqual(first.assigned_department, self.department)

        self.category.department = self.estates
        self.category.save()

        second = self.lodge()
        self.assertEqual(second.assigned_department, self.estates)

        # The earlier complaint keeps the department it was routed to.
        first.refresh_from_db()
        self.assertEqual(first.assigned_department, self.department)

    def test_lodging_sets_the_sla_from_the_category(self):
        complaint = self.lodge()
        expected = complaint.created_at + timedelta(
            hours=self.category.sla_hours,
        )
        self.assertIsNotNone(complaint.sla_due_at)
        self.assertLess(
            abs((complaint.sla_due_at - expected).total_seconds()), 5,
        )

    def test_inactive_department_leaves_the_complaint_unassigned(self):
        """Routing must not silently drop a complaint into a dead queue."""
        self.department.is_active = False
        self.department.save()

        complaint = self.lodge()
        self.assertIsNone(complaint.assigned_department)

    def test_routing_is_recorded_in_the_audit_trail(self):
        complaint = self.lodge()
        entry = complaint.status_history.get()
        self.assertEqual(entry.new_status, Complaint.Status.OPEN)
        self.assertIn(self.department.name, entry.comment)


class AssignmentTests(BaseFixture):
    """SCMS-F-012 / TC-Route-02: assign, reassign, reason recorded."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.estates = Department.objects.create(
            name="Estates", code="estates",
        )

        self.agent = self.make_staff(
            "agent", Role.SUPPORT_AGENT, self.department,
        )
        self.other_agent = self.make_staff(
            "agent2", Role.SUPPORT_AGENT, self.department,
        )
        self.estates_agent = self.make_staff(
            "estatesagent", Role.SUPPORT_AGENT, self.estates,
        )
        self.admin = self.make_staff(
            "itadmin", Role.DEPARTMENT_ADMIN, self.department,
        )
        self.estates_admin = self.make_staff(
            "estatesadmin", Role.DEPARTMENT_ADMIN, self.estates,
        )

        self.complaint = self.make_complaint(
            assigned_department=self.department,
        )

    def make_staff(self, username, role, department):
        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = role
        profile.department = department
        profile.save()
        return user

    def url(self, complaint=None):
        target = complaint or self.complaint
        return f"/{target.reference_id}/assign/"

    def post_assign(self, agent, reason="", complaint=None):
        return self.client.post(
            self.url(complaint),
            {"agent": agent.pk, "reason": reason},
        )

    # --- the service -------------------------------------------------

    def test_first_assignment_needs_no_reason(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.assigned_to, self.agent)

    def test_first_assignment_moves_status_to_assigned(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.status, Complaint.Status.ASSIGNED)

    def test_reassignment_without_a_reason_is_blocked(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)

        with self.assertRaises(ValueError):
            assign_complaint(
                self.complaint, self.other_agent, actor=self.admin,
            )

        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.assigned_to, self.agent)

    def test_whitespace_does_not_count_as_a_reason(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)

        with self.assertRaises(ValueError):
            assign_complaint(
                self.complaint,
                self.other_agent,
                actor=self.admin,
                reason="   \n  ",
            )

    def test_reason_appears_in_the_audit_trail(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)
        assign_complaint(
            self.complaint,
            self.other_agent,
            actor=self.admin,
            reason="Original agent is on leave",
        )

        latest = self.complaint.status_history.first()
        self.assertIn("Original agent is on leave", latest.comment)
        self.assertIn("agent2", latest.comment)
        self.assertEqual(latest.changed_by, self.admin)

    def test_reassignment_does_not_rewind_work_in_progress(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)
        Complaint.objects.filter(pk=self.complaint.pk).update(
            status=Complaint.Status.IN_PROGRESS,
        )
        self.complaint.refresh_from_db()

        assign_complaint(
            self.complaint,
            self.other_agent,
            actor=self.admin,
            reason="Workload rebalance",
        )

        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.IN_PROGRESS,
        )

    def test_eligible_agents_are_scoped_to_the_department(self):
        agents = eligible_agents(self.complaint)
        self.assertIn(self.agent, agents)
        self.assertNotIn(self.estates_agent, agents)

    def test_unrouted_complaint_has_no_eligible_agents(self):
        unrouted = self.make_complaint(assigned_department=None)
        self.assertFalse(eligible_agents(unrouted).exists())

    def test_inactive_agent_is_not_eligible(self):
        self.agent.is_active = False
        self.agent.save()
        self.assertNotIn(self.agent, eligible_agents(self.complaint))

    # --- the view ----------------------------------------------------

    def test_department_admin_can_assign(self):
        self.client.force_login(self.admin)
        response = self.post_assign(self.agent)

        self.assertEqual(response.status_code, 302)
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.assigned_to, self.agent)

    def test_view_blocks_reassignment_with_no_reason(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)

        self.client.force_login(self.admin)
        response = self.post_assign(self.other_agent)

        self.assertEqual(response.status_code, 200)
        self.assertIn("reason", response.context["form"].errors)

        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.assigned_to, self.agent)

    def test_view_accepts_reassignment_with_a_reason(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)

        self.client.force_login(self.admin)
        response = self.post_assign(
            self.other_agent, reason="Agent reallocated to urgent work",
        )

        self.assertEqual(response.status_code, 302)
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.assigned_to, self.other_agent)

    def test_agent_cannot_assign(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_complainant_cannot_assign(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_admin_of_another_department_cannot_assign(self):
        self.client.force_login(self.estates_admin)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_agent_from_another_department_is_not_offered(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url())
        choices = response.context["form"].fields["agent"].queryset
        self.assertNotIn(self.estates_agent, choices)

    def test_assigning_to_the_current_owner_is_rejected(self):
        assign_complaint(self.complaint, self.agent, actor=self.admin)

        self.client.force_login(self.admin)
        response = self.post_assign(self.agent, reason="No change intended")

        self.assertEqual(response.status_code, 200)
        self.assertIn("agent", response.context["form"].errors)

    def test_assignment_records_the_source_ip(self):
        """Supports SCMS-F-027: the audit row names where it came from."""
        self.client.force_login(self.admin)
        self.post_assign(self.agent)

        entry = self.complaint.status_history.first()
        self.assertIsNotNone(entry.source_ip)


class PriorityMatrixTests(BaseFixture):
    """SCMS-F-013 / TC-Route-03: matrix default, logged override."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        # BaseFixture's category is High; add a Low one to prove the
        # priority tracks the category rather than a constant.
        self.low_category = Category.objects.create(
            name="Stationery",
            code="stationery",
            department=self.department,
            default_priority=Complaint.Priority.LOW,
        )
        self.sub_category = SubCategory.objects.create(
            name="Projector", code="projector", category=self.category,
        )
        self.low_sub_category = SubCategory.objects.create(
            name="Pens", code="pens", category=self.low_category,
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.admin = self.make_staff(
            "itadmin", Role.DEPARTMENT_ADMIN, self.department,
        )
        self.agent = self.make_staff(
            "agent", Role.SUPPORT_AGENT, self.department,
        )

    def make_staff(self, username, role, department):
        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = role
        profile.department = department
        profile.save()
        return user

    def lodge(self, category=None, sub_category=None, priority=None):
        """Lodge a complaint, optionally trying to force a priority."""
        self.client.force_login(self.user)
        data = {
            "title": "Projector not working",
            "category": (category or self.category).pk,
            "sub_category": (sub_category or self.sub_category).pk,
            "location": "Block C, Room 302",
            "description": "The projector in room 302 does not switch on.",
        }
        if priority is not None:
            data["priority"] = priority

        response = self.client.post("/new/", data)
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        return Complaint.objects.latest("created_at")

    def url(self, complaint=None):
        target = complaint or self.complaint_for_override()
        return f"/{target.reference_id}/priority/"

    def complaint_for_override(self):
        if not hasattr(self, "_complaint"):
            self._complaint = self.make_complaint(
                assigned_department=self.department,
                priority=Complaint.Priority.MEDIUM,
            )
        return self._complaint

    # --- the matrix --------------------------------------------------

    def test_priority_comes_from_the_category(self):
        self.assertEqual(
            self.lodge().priority, Complaint.Priority.HIGH,
        )

    def test_a_different_category_gives_a_different_priority(self):
        complaint = self.lodge(
            category=self.low_category,
            sub_category=self.low_sub_category,
        )
        self.assertEqual(complaint.priority, Complaint.Priority.LOW)

    def test_complainant_cannot_choose_their_own_priority(self):
        """A self-declared priority would make triage meaningless."""
        complaint = self.lodge(priority=Complaint.Priority.CRITICAL)
        self.assertEqual(complaint.priority, Complaint.Priority.HIGH)

    def test_priority_is_not_a_field_on_the_lodging_form(self):
        self.client.force_login(self.user)
        response = self.client.get("/new/")
        self.assertNotIn("priority", response.context["form"].fields)

    def test_changing_the_matrix_changes_later_complaints(self):
        """ADR-06: triage is configuration, not code."""
        self.assertEqual(self.lodge().priority, Complaint.Priority.HIGH)

        self.category.default_priority = Complaint.Priority.CRITICAL
        self.category.save()

        self.assertEqual(self.lodge().priority, Complaint.Priority.CRITICAL)

    def test_category_without_a_priority_falls_back_to_medium(self):
        self.category.default_priority = ""
        self.assertEqual(
            priority_for(self.category), Complaint.Priority.MEDIUM,
        )

    # --- the override ------------------------------------------------

    def test_override_records_old_and_new_values(self):
        complaint = self.complaint_for_override()

        override_priority(
            complaint,
            priority=Complaint.Priority.CRITICAL,
            actor=self.admin,
            reason="Affects an entire floor",
        )

        complaint.refresh_from_db()
        self.assertEqual(complaint.priority, Complaint.Priority.CRITICAL)

        entry = complaint.status_history.first()
        self.assertIn("Medium", entry.comment)
        self.assertIn("Critical", entry.comment)
        self.assertIn("Affects an entire floor", entry.comment)
        self.assertEqual(entry.changed_by, self.admin)

    def test_override_without_a_reason_is_blocked(self):
        complaint = self.complaint_for_override()

        with self.assertRaises(ValueError):
            override_priority(
                complaint,
                priority=Complaint.Priority.CRITICAL,
                actor=self.admin,
                reason="   ",
            )

        complaint.refresh_from_db()
        self.assertEqual(complaint.priority, Complaint.Priority.MEDIUM)

    def test_override_to_the_same_priority_is_refused(self):
        complaint = self.complaint_for_override()

        with self.assertRaises(ValueError):
            override_priority(
                complaint,
                priority=Complaint.Priority.MEDIUM,
                actor=self.admin,
                reason="No change intended",
            )

    def test_override_does_not_change_the_status(self):
        complaint = self.complaint_for_override()
        before = complaint.status

        override_priority(
            complaint,
            priority=Complaint.Priority.LOW,
            actor=self.admin,
            reason="Lower impact than first thought",
        )

        complaint.refresh_from_db()
        self.assertEqual(complaint.status, before)

    # --- the view ----------------------------------------------------

    def test_admin_can_override_through_the_view(self):
        complaint = self.complaint_for_override()
        self.client.force_login(self.admin)

        response = self.client.post(self.url(), {
            "priority": Complaint.Priority.CRITICAL,
            "reason": "Affects an entire floor",
        })

        self.assertEqual(response.status_code, 302)
        complaint.refresh_from_db()
        self.assertEqual(complaint.priority, Complaint.Priority.CRITICAL)

    def test_view_blocks_an_override_with_no_reason(self):
        complaint = self.complaint_for_override()
        self.client.force_login(self.admin)

        response = self.client.post(self.url(), {
            "priority": Complaint.Priority.CRITICAL,
            "reason": "",
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn("reason", response.context["form"].errors)

        complaint.refresh_from_db()
        self.assertEqual(complaint.priority, Complaint.Priority.MEDIUM)

    def test_agent_cannot_override_priority(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_complainant_cannot_override_priority(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)


class AttachmentTests(BaseFixture):
    """SCMS-F-008 / TC-Lodge-03: three files, three formats, 5 MB."""

    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    PDF = b"%PDF-1.7\n" + b"\x00" * 64
    EXE = b"MZ\x90\x00" + b"\x00" * 64

    @classmethod
    def setUpClass(cls):
        """Send uploads to a temp directory, not into the repo tree."""
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(
            MEDIA_ROOT=cls._media_root,
        )
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        from .roles import Role

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.other = get_user_model().objects.create_user(
            username="other", password="not-a-real-password",
        )

        self.complaint = self.make_complaint(
            assigned_department=self.department,
        )
        self.client.force_login(self.user)

    def url(self):
        return f"/{self.complaint.reference_id}/attach/"

    def upload(self, name, content, content_type="image/jpeg"):
        return self.client.post(self.url(), {
            "file": SimpleUploadedFile(name, content, content_type),
        })

    def attach_directly(self, name="evidence.jpg", content=None):
        """Create an attachment without going through the view."""
        upload = SimpleUploadedFile(
            name, content or self.JPEG, "image/jpeg",
        )
        return ComplaintAttachment.objects.create(
            complaint=self.complaint,
            file=upload,
            original_name=name,
            content_type="image/jpeg",
            size_bytes=upload.size,
            uploaded_by=self.user,
        )

    def form_errors(self, response):
        return str(response.context["form"].errors)

    # --- the three permitted formats ---------------------------------

    def test_jpg_is_accepted(self):
        response = self.upload("evidence.jpg", self.JPEG)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.complaint.attachments.count(), 1)

    def test_png_is_accepted(self):
        response = self.upload("evidence.png", self.PNG, "image/png")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.complaint.attachments.count(), 1)

    def test_pdf_is_accepted(self):
        response = self.upload(
            "evidence.pdf", self.PDF, "application/pdf",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.complaint.attachments.count(), 1)

    def test_content_type_is_taken_from_the_file_not_the_client(self):
        """A lying Content-Type must not reach the database."""
        self.upload("evidence.png", self.PNG, "application/x-evil")
        self.assertEqual(
            self.complaint.attachments.get().content_type, "image/png",
        )

    # --- the three rejections the criterion names --------------------

    def test_a_fourth_file_is_rejected(self):
        for index in range(3):
            self.attach_directly(f"evidence{index}.jpg")

        response = self.upload("fourth.jpg", self.JPEG)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.complaint.attachments.count(), 3)
        self.assertIn("at most 3", self.form_errors(response))

    def test_a_file_over_five_megabytes_is_rejected(self):
        oversized = self.JPEG + b"\x00" * (5 * 1024 * 1024)

        response = self.upload("huge.jpg", oversized)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.complaint.attachments.exists())
        self.assertIn("5 MB", self.form_errors(response))

    def test_an_executable_is_rejected(self):
        response = self.upload(
            "payload.exe", self.EXE, "application/octet-stream",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.complaint.attachments.exists())
        self.assertIn("not a JPG, PNG or PDF", self.form_errors(response))

    def test_an_executable_renamed_to_jpg_is_still_rejected(self):
        """The case an extension check alone would let through."""
        response = self.upload("payload.jpg", self.EXE)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.complaint.attachments.exists())
        self.assertIn("not a JPG, PNG or PDF", self.form_errors(response))

    def test_a_png_with_a_jpg_extension_is_rejected(self):
        """Real image, mismatched extension: say so rather than guess."""
        response = self.upload("evidence.jpg", self.PNG)

        self.assertEqual(response.status_code, 200)
        self.assertIn("extension", self.form_errors(response))

    def test_an_empty_file_is_rejected(self):
        response = self.upload("empty.jpg", b"")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.complaint.attachments.exists())

    # --- boundaries --------------------------------------------------

    def test_a_file_at_exactly_five_megabytes_is_accepted(self):
        exact = self.JPEG + b"\x00" * (MAX_BYTES - len(self.JPEG))
        self.assertEqual(len(exact), MAX_BYTES)

        response = self.upload("exact.jpg", exact)
        self.assertEqual(response.status_code, 302)

    def test_three_files_are_accepted(self):
        for index in range(3):
            response = self.upload(f"evidence{index}.jpg", self.JPEG)
            self.assertEqual(response.status_code, 302)

        self.assertEqual(self.complaint.attachments.count(), 3)

    # --- storage and access ------------------------------------------

    def test_stored_path_does_not_reuse_the_uploaded_name(self):
        self.upload("my-holiday-photo.jpg", self.JPEG)
        attachment = self.complaint.attachments.get()

        self.assertNotIn("my-holiday-photo", attachment.file.name)
        self.assertEqual(attachment.original_name, "my-holiday-photo.jpg")

    def test_two_files_of_the_same_name_do_not_collide(self):
        self.upload("photo.jpg", self.JPEG)
        self.upload("photo.jpg", self.JPEG)

        paths = {a.file.name for a in self.complaint.attachments.all()}
        self.assertEqual(len(paths), 2)

    def test_owner_can_download_their_evidence(self):
        self.upload("evidence.jpg", self.JPEG)
        attachment = self.complaint.attachments.get()

        response = self.client.get(
            f"/{self.complaint.reference_id}/attachments/"
            f"{attachment.pk}/"
        )
        self.assertEqual(response.status_code, 200)

    def test_another_complainant_cannot_download_it(self):
        """SCMS-SR-005: evidence is not reachable by guessing a URL."""
        self.upload("evidence.jpg", self.JPEG)
        attachment = self.complaint.attachments.get()

        self.client.force_login(self.other)
        response = self.client.get(
            f"/{self.complaint.reference_id}/attachments/"
            f"{attachment.pk}/"
        )
        self.assertEqual(response.status_code, 403)

    def test_another_complainant_cannot_attach_to_this_complaint(self):
        self.client.force_login(self.other)
        response = self.upload("evidence.jpg", self.JPEG)
        self.assertEqual(response.status_code, 403)


class StatusTrackingTests(BaseFixture):
    """SCMS-F-016 / TC-Track-01: the seven states and their history."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.sub_category = SubCategory.objects.create(
            name="Projector", code="projector", category=self.category,
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.agent = self.make_staff(
            "agent", Role.SUPPORT_AGENT, self.department,
        )
        self.admin = self.make_staff(
            "itadmin", Role.DEPARTMENT_ADMIN, self.department,
        )

        self.complaint = self.make_complaint(
            assigned_department=self.department,
            assigned_to=self.agent,
            sub_category=self.sub_category,
            location="Block C, Room 302",
            status=Complaint.Status.ASSIGNED,
        )

    def make_staff(self, username, role, department):
        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = role
        profile.department = department
        profile.save()
        return user

    def detail_url(self):
        return f"/{self.complaint.reference_id}/"

    def status_url(self):
        return f"/{self.complaint.reference_id}/status/"

    # --- the state machine -------------------------------------------

    def test_every_srs_status_appears_in_the_state_machine(self):
        self.assertEqual(
            set(STATUS_TRANSITIONS),
            set(Complaint.Status.values),
        )

    def test_closed_is_terminal(self):
        self.assertEqual(
            STATUS_TRANSITIONS[Complaint.Status.CLOSED], (),
        )

    def test_legal_transition_is_recorded(self):
        transition_status(
            self.complaint,
            Complaint.Status.IN_PROGRESS,
            actor=self.agent,
        )

        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.IN_PROGRESS,
        )

        entry = self.complaint.status_history.first()
        self.assertEqual(entry.old_status, Complaint.Status.ASSIGNED)
        self.assertEqual(entry.new_status, Complaint.Status.IN_PROGRESS)
        self.assertEqual(entry.changed_by, self.agent)

    def test_illegal_transition_is_refused(self):
        """Assigned cannot jump straight to Pending Information."""
        with self.assertRaises(ValueError):
            transition_status(
                self.complaint,
                Complaint.Status.PENDING_INFORMATION,
                actor=self.agent,
            )

        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.ASSIGNED,
        )

    def test_transition_to_the_current_status_is_refused(self):
        with self.assertRaises(ValueError):
            transition_status(
                self.complaint,
                Complaint.Status.ASSIGNED,
                actor=self.agent,
            )

    def test_resolved_cannot_be_reached_generically(self):
        """SCMS-F-018 owns Resolved: it needs remarks and proof."""
        transition_status(
            self.complaint,
            Complaint.Status.IN_PROGRESS,
            actor=self.agent,
        )

        with self.assertRaises(ValueError):
            transition_status(
                self.complaint,
                Complaint.Status.RESOLVED,
                actor=self.agent,
            )

    def test_resolved_is_still_legal_in_the_state_machine(self):
        """Guarded is not the same as unreachable."""
        self.assertIn(
            Complaint.Status.RESOLVED,
            STATUS_TRANSITIONS[Complaint.Status.IN_PROGRESS],
        )

    def test_guarded_statuses_are_not_offered(self):
        transition_status(
            self.complaint,
            Complaint.Status.IN_PROGRESS,
            actor=self.agent,
        )

        offered = offerable_transitions(self.complaint)
        self.assertIn(Complaint.Status.PENDING_INFORMATION, offered)
        self.assertNotIn(Complaint.Status.RESOLVED, offered)

    def test_pending_information_returns_to_in_progress(self):
        transition_status(
            self.complaint, Complaint.Status.IN_PROGRESS, actor=self.agent,
        )
        transition_status(
            self.complaint,
            Complaint.Status.PENDING_INFORMATION,
            actor=self.agent,
        )
        transition_status(
            self.complaint, Complaint.Status.IN_PROGRESS, actor=self.agent,
        )

        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.IN_PROGRESS,
        )
        self.assertEqual(self.complaint.status_history.count(), 3)

    # --- the tracking page -------------------------------------------

    def test_complainant_sees_the_current_status(self):
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Assigned")

    def test_tracking_page_shows_the_category_name(self):
        """category is a FK, so get_category_display rendered blank."""
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(self.detail_url()), self.category.name,
        )

    def test_tracking_page_shows_sub_category_and_location(self):
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Projector")
        self.assertContains(response, "Block C, Room 302")

    def test_tracking_page_shows_dated_transition_history(self):
        transition_status(
            self.complaint,
            Complaint.Status.IN_PROGRESS,
            actor=self.agent,
            comment="Engineer scheduled for tomorrow.",
        )

        self.client.force_login(self.user)
        response = self.client.get(self.detail_url())

        self.assertContains(response, "In Progress")
        self.assertContains(response, "Engineer scheduled for tomorrow.")
        self.assertContains(response, "agent")

    def test_history_is_newest_first(self):
        transition_status(
            self.complaint, Complaint.Status.IN_PROGRESS, actor=self.agent,
        )
        transition_status(
            self.complaint,
            Complaint.Status.PENDING_INFORMATION,
            actor=self.agent,
        )

        entries = list(self.complaint.status_history.all())
        self.assertEqual(
            entries[0].new_status, Complaint.Status.PENDING_INFORMATION,
        )

    # --- the status view ---------------------------------------------

    def test_agent_can_move_the_complaint_along(self):
        self.client.force_login(self.agent)
        response = self.client.post(self.status_url(), {
            "status": Complaint.Status.IN_PROGRESS,
            "comment": "Started work",
        })

        self.assertEqual(response.status_code, 302)
        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.IN_PROGRESS,
        )

    def test_view_does_not_offer_an_illegal_move(self):
        self.client.force_login(self.agent)
        response = self.client.get(self.status_url())
        offered = dict(response.context["form"].fields["status"].choices)
        self.assertEqual(
            set(offered), {Complaint.Status.IN_PROGRESS},
        )

    def test_view_rejects_an_illegal_move_posted_directly(self):
        self.client.force_login(self.agent)
        response = self.client.post(self.status_url(), {
            "status": Complaint.Status.CLOSED,
            "comment": "",
        })

        self.assertEqual(response.status_code, 200)
        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.ASSIGNED,
        )

    def test_complainant_cannot_change_status(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(self.status_url()).status_code, 403,
        )


class CommentTests(BaseFixture):
    """SCMS-F-017 / TC-Track-02: both parties, and nobody else."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.estates = Department.objects.create(
            name="Estates", code="estates",
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.agent = self.make_staff(
            "agent", Role.SUPPORT_AGENT, self.department,
        )
        self.other_agent = self.make_staff(
            "estatesagent", Role.SUPPORT_AGENT, self.estates,
        )
        self.other_complainant = get_user_model().objects.create_user(
            username="other", password="not-a-real-password",
        )

        self.complaint = self.make_complaint(
            assigned_department=self.department,
            assigned_to=self.agent,
        )

    def make_staff(self, username, role, department):
        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = role
        profile.department = department
        profile.save()
        return user

    def url(self):
        return f"/{self.complaint.reference_id}/comment/"

    def detail_url(self):
        return f"/{self.complaint.reference_id}/"

    def post(self, body, parent=None):
        data = {"body": body}
        if parent is not None:
            data["parent"] = parent
        return self.client.post(self.url(), data)

    # --- posting -----------------------------------------------------

    def test_complainant_can_comment(self):
        self.client.force_login(self.user)
        response = self.post("The projector is still dead.")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.complaint.comments.count(), 1)

    def test_assigned_agent_can_comment(self):
        self.client.force_login(self.agent)
        self.post("An engineer is booked for Tuesday.")

        self.assertEqual(self.complaint.comments.count(), 1)

    def test_empty_comment_is_refused(self):
        self.client.force_login(self.user)
        self.post("   ")

        self.assertFalse(self.complaint.comments.exists())

    def test_overlong_comment_is_refused(self):
        self.client.force_login(self.user)
        self.post("x" * (ComplaintComment.BODY_LIMIT + 1))

        self.assertFalse(self.complaint.comments.exists())

    def test_body_is_stored_stripped(self):
        self.client.force_login(self.user)
        self.post("  padded  ")

        self.assertEqual(self.complaint.comments.get().body, "padded")

    # --- visibility, which is the acceptance criterion ---------------

    def test_both_parties_see_each_others_comments(self):
        self.client.force_login(self.user)
        self.post("From the complainant.")

        self.client.force_login(self.agent)
        self.post("From the agent.")

        for actor in (self.user, self.agent):
            self.client.force_login(actor)
            page = self.client.get(self.detail_url())
            self.assertContains(page, "From the complainant.")
            self.assertContains(page, "From the agent.")

    def test_another_complainant_cannot_read_the_thread(self):
        self.client.force_login(self.user)
        self.post("Private to this complaint.")

        self.client.force_login(self.other_complainant)
        self.assertEqual(
            self.client.get(self.detail_url()).status_code, 403,
        )

    def test_another_complainant_cannot_post(self):
        self.client.force_login(self.other_complainant)
        response = self.post("Butting in.")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.complaint.comments.exists())

    def test_agent_from_another_department_cannot_post(self):
        self.client.force_login(self.other_agent)
        response = self.post("Not my complaint.")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.complaint.comments.exists())

    def test_anonymous_is_sent_to_login(self):
        response = self.post("Anonymous.")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    # --- threading ---------------------------------------------------

    def test_reply_attaches_to_its_parent(self):
        self.client.force_login(self.user)
        self.post("Original question.")
        parent = self.complaint.comments.get()

        self.client.force_login(self.agent)
        self.post("Answer.", parent=parent.pk)

        reply = self.complaint.comments.exclude(pk=parent.pk).get()
        self.assertEqual(reply.parent, parent)
        self.assertEqual(list(parent.replies.all()), [reply])

    def test_reply_to_a_reply_is_refused(self):
        """One level deep: deeper nesting helps nobody read it."""
        parent = add_comment(self.complaint, self.user, "Original.")
        reply = add_comment(
            self.complaint, self.agent, "Answer.", parent=parent,
        )

        with self.assertRaises(ValueError):
            add_comment(
                self.complaint, self.user, "Nested.", parent=reply,
            )

    def test_reply_cannot_cross_complaints(self):
        """A parent id from another complaint must not resolve."""
        other = self.make_complaint(assigned_department=self.department)
        elsewhere = add_comment(other, self.user, "Over here.")

        self.client.force_login(self.user)
        response = self.post("Smuggled.", parent=elsewhere.pk)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(other.comments.count(), 1)

    def test_service_refuses_a_parent_from_another_complaint(self):
        other = self.make_complaint(assigned_department=self.department)
        elsewhere = add_comment(other, self.user, "Over here.")

        with self.assertRaises(ValueError):
            add_comment(
                self.complaint, self.user, "Smuggled.", parent=elsewhere,
            )

    def test_thread_is_oldest_first_with_replies_grouped(self):
        first = add_comment(self.complaint, self.user, "First.")
        add_comment(self.complaint, self.agent, "Reply.", parent=first)
        add_comment(self.complaint, self.user, "Second.")

        thread = list(comment_thread(self.complaint))
        self.assertEqual([c.body for c in thread], ["First.", "Second."])
        self.assertEqual(
            [r.body for r in thread[0].replies.all()], ["Reply."],
        )


class RoleAdminTests(BaseFixture):
    """Roles must be administrable, not shell-only (SCMS-F-003)."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.Role = Role
        self.superuser = get_user_model().objects.create_superuser(
            username="root",
            email="root@example.com",
            password="not-a-real-password",
        )
        self.client.force_login(self.superuser)

    def test_user_admin_page_loads(self):
        response = self.client.get(
            f"/admin/auth/user/{self.user.pk}/change/",
        )
        self.assertEqual(response.status_code, 200)

    def test_user_admin_offers_the_role_inline(self):
        response = self.client.get(
            f"/admin/auth/user/{self.user.pk}/change/",
        )
        self.assertContains(response, "profile-0-role")

    def test_role_can_be_changed_through_the_admin(self):
        """The gap this closes: previously only a shell could do it."""
        response = self.client.post(
            f"/admin/auth/user/{self.user.pk}/change/",
            {
                "username": self.user.username,
                "email": self.user.email,
                "first_name": "",
                "last_name": "",
                "is_active": "on",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "00:00:00",
                "profile-TOTAL_FORMS": "1",
                "profile-INITIAL_FORMS": "1",
                "profile-MIN_NUM_FORMS": "0",
                "profile-MAX_NUM_FORMS": "1",
                "profile-0-id": str(self.user.profile.pk),
                "profile-0-user": str(self.user.pk),
                "profile-0-role": self.Role.SUPPORT_AGENT,
                "profile-0-department": str(self.department.pk),
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.role, self.Role.SUPPORT_AGENT,
        )
        self.assertEqual(
            self.user.profile.department, self.department,
        )

    def test_profiles_are_listed_on_their_own(self):
        response = self.client.get("/admin/complaints/userprofile/")
        self.assertEqual(response.status_code, 200)

    def test_user_list_shows_the_scms_role(self):
        profile = self.user.profile
        profile.role = self.Role.DEPARTMENT_ADMIN
        profile.save()

        response = self.client.get("/admin/auth/user/")
        self.assertContains(response, "Department Admin")


class ResolutionTests(BaseFixture):
    """SCMS-F-018 / TC-Res-01: remarks and proof, or no resolution."""

    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    EXE = b"MZ\x90\x00" + b"\x00" * 64
    GOOD_REMARKS = (
        "Replaced the failed projector lamp module and confirmed "
        "the unit powers on and displays correctly."
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.agent = get_user_model().objects.create_user(
            username="agent", password="not-a-real-password",
        )
        profile = self.agent.profile
        profile.role = Role.SUPPORT_AGENT
        profile.department = self.department
        profile.save()

        self.complaint = self.make_complaint(
            assigned_department=self.department,
            assigned_to=self.agent,
            status=Complaint.Status.IN_PROGRESS,
        )
        self.client.force_login(self.agent)

    def url(self):
        return f"/{self.complaint.reference_id}/resolve/"

    def proof(self, name="fixed.jpg", content=None):
        return SimpleUploadedFile(
            name, content or self.JPEG, "image/jpeg",
        )

    def post(self, remarks=None, proof=True):
        data = {"remarks": self.GOOD_REMARKS if remarks is None else remarks}
        if proof:
            data["proof"] = self.proof()
        return self.client.post(self.url(), data)

    def assert_not_resolved(self):
        self.complaint.refresh_from_db()
        self.assertNotEqual(
            self.complaint.status, Complaint.Status.RESOLVED,
        )
        self.assertEqual(self.complaint.resolution_remarks, "")
        self.assertIsNone(self.complaint.resolved_at)

    # --- the happy path ----------------------------------------------

    def test_remarks_and_proof_resolve_the_complaint(self):
        response = self.post()

        self.assertEqual(response.status_code, 302)
        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.RESOLVED,
        )
        self.assertEqual(
            self.complaint.resolution_remarks, self.GOOD_REMARKS,
        )
        self.assertIsNotNone(self.complaint.resolved_at)

    def test_proof_is_stored_as_proof_not_evidence(self):
        self.post()

        attachment = self.complaint.attachments.get()
        self.assertEqual(attachment.kind, AttachmentKind.PROOF)
        self.assertEqual(attachment.uploaded_by, self.agent)

    def test_resolution_is_recorded_in_the_audit_trail(self):
        self.post()

        entry = self.complaint.status_history.first()
        self.assertEqual(entry.new_status, Complaint.Status.RESOLVED)
        self.assertEqual(entry.changed_by, self.agent)
        self.assertIn("Replaced the failed projector", entry.comment)

    # --- the refusals the criterion names -----------------------------

    def test_no_remarks_is_refused(self):
        self.post(remarks="")
        self.assert_not_resolved()

    def test_short_remarks_are_refused(self):
        self.post(remarks="Fixed it.")
        self.assert_not_resolved()

    def test_remarks_just_under_the_limit_are_refused(self):
        self.post(remarks="x" * (RESOLUTION_REMARKS_MIN - 1))
        self.assert_not_resolved()

    def test_remarks_at_exactly_the_limit_are_accepted(self):
        response = self.post(remarks="x" * RESOLUTION_REMARKS_MIN)

        self.assertEqual(response.status_code, 302)
        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.RESOLVED,
        )

    def test_whitespace_padding_does_not_count_towards_the_minimum(self):
        self.post(remarks="   " + "x" * 10 + "   " * 20)
        self.assert_not_resolved()

    def test_no_proof_is_refused(self):
        self.post(proof=False)
        self.assert_not_resolved()

    def test_an_executable_as_proof_is_refused(self):
        self.client.post(self.url(), {
            "remarks": self.GOOD_REMARKS,
            "proof": self.proof("payload.jpg", self.EXE),
        })
        self.assert_not_resolved()
        self.assertFalse(self.complaint.attachments.exists())

    # --- the state machine --------------------------------------------

    def test_an_open_complaint_cannot_be_resolved(self):
        self.complaint.status = Complaint.Status.OPEN
        self.complaint.save(update_fields=["status"])

        self.post()
        self.assert_not_resolved()

    def test_service_refuses_resolution_from_a_wrong_status(self):
        self.complaint.status = Complaint.Status.ASSIGNED
        self.complaint.save(update_fields=["status"])

        with self.assertRaises(ValueError):
            resolve_complaint(
                self.complaint,
                actor=self.agent,
                remarks=self.GOOD_REMARKS,
                proof_uploads=[self.proof()],
            )

    def test_generic_status_change_still_cannot_reach_resolved(self):
        """The guard from T8-23 is what makes this flow the only way."""
        with self.assertRaises(ValueError):
            transition_status(
                self.complaint,
                Complaint.Status.RESOLVED,
                actor=self.agent,
            )

    # --- the allowance question ----------------------------------------

    def test_proof_does_not_consume_the_complainant_evidence_limit(self):
        """Three pieces of evidence must not block resolution."""
        for index in range(3):
            upload = SimpleUploadedFile(
                f"evidence{index}.jpg", self.JPEG, "image/jpeg",
            )
            ComplaintAttachment.objects.create(
                complaint=self.complaint,
                kind=AttachmentKind.EVIDENCE,
                file=upload,
                original_name=upload.name,
                content_type="image/jpeg",
                size_bytes=upload.size,
                uploaded_by=self.user,
            )

        response = self.post()

        self.assertEqual(response.status_code, 302)
        self.complaint.refresh_from_db()
        self.assertEqual(
            self.complaint.status, Complaint.Status.RESOLVED,
        )

    # --- access --------------------------------------------------------

    def test_complainant_cannot_resolve_their_own_complaint(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)


class MasterDataTests(BaseFixture):
    """SCMS-F-023 / TC-Admin-01: configuration, not code.

    Each case changes a master-data row and shows the next complaint
    behaving differently, with no restart and nothing redeployed.
    """

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.sub_category = SubCategory.objects.create(
            name="Projector", code="projector", category=self.category,
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.superuser = get_user_model().objects.create_superuser(
            username="root",
            email="root@example.com",
            password="not-a-real-password",
        )

    def lodging_form(self):
        self.client.force_login(self.user)
        return self.client.get("/new/").context["form"]

    def lodge(self, category=None, sub_category=None):
        self.client.force_login(self.user)
        response = self.client.post("/new/", {
            "title": "Projector not working",
            "category": (category or self.category).pk,
            "sub_category": (sub_category or self.sub_category).pk,
            "location": "Block C, Room 302",
            "description": "The projector in room 302 does not switch on.",
        })
        self.assertEqual(response.status_code, 302)
        return Complaint.objects.latest("created_at")

    # --- categories ---------------------------------------------------

    def test_a_new_category_is_immediately_selectable(self):
        fresh = Category.objects.create(
            name="Hostel", code="hostel", department=self.department,
        )
        self.assertIn(
            fresh, self.lodging_form().fields["category"].queryset,
        )

    def test_a_deactivated_category_disappears_from_the_form(self):
        self.category.is_active = False
        self.category.save()

        self.assertNotIn(
            self.category,
            self.lodging_form().fields["category"].queryset,
        )

    def test_deactivating_a_category_leaves_existing_complaints_alone(self):
        lodged = self.lodge()

        self.category.is_active = False
        self.category.save()

        lodged.refresh_from_db()
        self.assertEqual(lodged.category, self.category)

    # --- sub-categories -------------------------------------------------

    def test_a_deactivated_sub_category_disappears(self):
        self.sub_category.is_active = False
        self.sub_category.save()

        self.assertNotIn(
            self.sub_category,
            self.lodging_form().fields["sub_category"].queryset,
        )

    def test_sub_category_of_a_dead_category_disappears(self):
        """Otherwise it lists a choice no category can pair with."""
        self.category.is_active = False
        self.category.save()

        self.assertNotIn(
            self.sub_category,
            self.lodging_form().fields["sub_category"].queryset,
        )

    # --- SLA and the priority matrix -------------------------------------

    def test_changing_the_sla_changes_the_next_deadline(self):
        self.category.sla_hours = 4
        self.category.save()

        complaint = self.lodge()
        gap = complaint.sla_due_at - complaint.created_at

        self.assertAlmostEqual(gap.total_seconds(), 4 * 3600, delta=10)

    def test_changing_the_matrix_changes_the_next_priority(self):
        self.category.default_priority = Complaint.Priority.CRITICAL
        self.category.save()

        self.assertEqual(
            self.lodge().priority, Complaint.Priority.CRITICAL,
        )

    # --- department deactivation -----------------------------------------

    def admin_post_department(self, department, is_active):
        self.client.force_login(self.superuser)
        return self.client.post(
            f"/admin/complaints/department/{department.pk}/change/",
            {
                "name": department.name,
                "code": department.code,
                "description": department.description,
                **({"is_active": "on"} if is_active else {}),
                "_save": "Save",
            },
        )

    def test_department_in_use_cannot_be_deactivated(self):
        """The silent failure this prevents: complaints with no queue."""
        response = self.admin_post_department(self.department, False)

        self.assertEqual(response.status_code, 200)
        self.department.refresh_from_db()
        self.assertTrue(self.department.is_active)

    def test_the_refusal_names_the_blocking_category(self):
        response = self.admin_post_department(self.department, False)
        self.assertContains(response, self.category.name)

    def test_department_can_be_deactivated_once_nothing_routes_to_it(self):
        self.category.is_active = False
        self.category.save()

        response = self.admin_post_department(self.department, False)

        self.assertEqual(response.status_code, 302)
        self.department.refresh_from_db()
        self.assertFalse(self.department.is_active)

    def test_an_unused_department_can_be_deactivated(self):
        spare = Department.objects.create(name="Spare", code="spare")

        self.admin_post_department(spare, False)

        spare.refresh_from_db()
        self.assertFalse(spare.is_active)

    # --- the admin screens exist -----------------------------------------

    def test_master_data_screens_are_reachable(self):
        self.client.force_login(self.superuser)

        for model in ("department", "category", "subcategory"):
            response = self.client.get(f"/admin/complaints/{model}/")
            self.assertEqual(
                response.status_code, 200, f"{model} list did not load",
            )


class SearchTests(BaseFixture):
    """SCMS-F-028 / TC-Rep-03: each filter, and combinations."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.estates = Department.objects.create(
            name="Estates", code="estates",
        )
        self.facilities = Category.objects.create(
            name="Facilities",
            code="facilities",
            department=self.estates,
            sla_hours=72,
        )

        self.sys_admin = self.make_staff("root", Role.SYSTEM_ADMIN, None)
        self.it_admin = self.make_staff(
            "itadmin", Role.DEPARTMENT_ADMIN, self.department,
        )

        self.projector = self.make_complaint(
            title="Projector not working",
            description="The projector in room 302 does not switch on.",
            location="Block C, Room 302",
            assigned_department=self.department,
            status=Complaint.Status.OPEN,
        )
        self.tap = self.make_complaint(
            title="Leaking tap",
            description="Water pooling under the basin all morning.",
            location="Block A, Washroom",
            category=self.facilities,
            assigned_department=self.estates,
            status=Complaint.Status.ASSIGNED,
        )

    def make_staff(self, username, role, department):
        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = role
        profile.department = department
        profile.save()
        return user

    def search(self, user=None, **params):
        self.client.force_login(user or self.sys_admin)
        response = self.client.get("/queue/", params)
        self.assertEqual(response.status_code, 200)
        return response

    def refs(self, response):
        return {c.reference_id for c in response.context["complaints"]}

    # --- each filter on its own ---------------------------------------

    def test_no_filters_returns_everything_in_scope(self):
        self.assertEqual(
            self.refs(self.search()),
            {self.projector.reference_id, self.tap.reference_id},
        )

    def test_filter_by_reference_id(self):
        found = self.refs(
            self.search(reference_id=self.projector.reference_id),
        )
        self.assertEqual(found, {self.projector.reference_id})

    def test_filter_by_status(self):
        found = self.refs(self.search(status=Complaint.Status.ASSIGNED))
        self.assertEqual(found, {self.tap.reference_id})

    def test_filter_by_category(self):
        found = self.refs(self.search(category=self.facilities.pk))
        self.assertEqual(found, {self.tap.reference_id})

    def test_filter_by_department(self):
        found = self.refs(self.search(department=self.estates.pk))
        self.assertEqual(found, {self.tap.reference_id})

    def test_keyword_matches_the_title(self):
        found = self.refs(self.search(q="projector"))
        self.assertEqual(found, {self.projector.reference_id})

    def test_keyword_matches_the_description(self):
        found = self.refs(self.search(q="pooling"))
        self.assertEqual(found, {self.tap.reference_id})

    def test_keyword_matches_the_location(self):
        found = self.refs(self.search(q="Washroom"))
        self.assertEqual(found, {self.tap.reference_id})

    def test_keyword_is_case_insensitive(self):
        self.assertEqual(
            self.refs(self.search(q="PROJECTOR")),
            {self.projector.reference_id},
        )

    def test_keyword_with_no_match_returns_nothing(self):
        self.assertEqual(self.refs(self.search(q="helicopter")), set())

    # --- date range ----------------------------------------------------

    def test_date_range_includes_today(self):
        today = timezone.localdate().isoformat()
        found = self.refs(
            self.search(lodged_from=today, lodged_to=today),
        )
        self.assertEqual(len(found), 2)

    def test_date_range_before_today_excludes_everything(self):
        past = (timezone.localdate() - timedelta(days=5)).isoformat()
        self.assertEqual(
            self.refs(self.search(lodged_from=past, lodged_to=past)),
            set(),
        )

    def test_a_backwards_date_range_is_rejected(self):
        """Swapping them silently would give right-looking results."""
        today = timezone.localdate()
        response = self.search(
            lodged_from=today.isoformat(),
            lodged_to=(today - timedelta(days=1)).isoformat(),
        )
        self.assertIn("lodged_to", response.context["search"].errors)

    # --- combinations ---------------------------------------------------

    def test_filters_combine_as_and_not_or(self):
        found = self.refs(
            self.search(
                q="projector", status=Complaint.Status.ASSIGNED,
            ),
        )
        self.assertEqual(found, set())

    def test_a_matching_combination_returns_the_row(self):
        found = self.refs(
            self.search(q="tap", department=self.estates.pk),
        )
        self.assertEqual(found, {self.tap.reference_id})

    # --- the rule that matters: search cannot widen scope ----------------

    def test_department_admin_cannot_search_outside_their_department(self):
        """SCMS-SR-003 must hold through the search, not around it."""
        found = self.refs(
            self.search(self.it_admin, department=self.estates.pk),
        )
        self.assertEqual(found, set())

    def test_department_admin_search_still_finds_their_own(self):
        found = self.refs(self.search(self.it_admin, q="projector"))
        self.assertEqual(found, {self.projector.reference_id})

    def test_keyword_cannot_surface_another_departments_complaint(self):
        found = self.refs(self.search(self.it_admin, q="tap"))
        self.assertEqual(found, set())

    def test_complainant_cannot_reach_the_queue_at_all(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/queue/").status_code, 403)


class AuditTrailCompletenessTests(BaseFixture):
    """SCMS-F-027 / TC-Audit-01: every change, fully attributed.

    AuditTrailTests above proves rows cannot be edited or deleted.
    This proves every flow actually writes one, and that it carries
    the actor, the source IP and the before and after values the
    requirement names.
    """

    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    REMARKS = (
        "Replaced the failed projector lamp module and confirmed "
        "the unit powers on and displays correctly."
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.sub_category = SubCategory.objects.create(
            name="Projector", code="projector", category=self.category,
        )

        profile = self.user.profile
        profile.role = Role.COMPLAINANT
        profile.save()

        self.agent = self.make_staff(
            "agent", Role.SUPPORT_AGENT, self.department,
        )
        self.other_agent = self.make_staff(
            "agent2", Role.SUPPORT_AGENT, self.department,
        )
        self.admin = self.make_staff(
            "itadmin", Role.DEPARTMENT_ADMIN, self.department,
        )

    def make_staff(self, username, role, department):
        user = get_user_model().objects.create_user(
            username=username, password="not-a-real-password",
        )
        profile = user.profile
        profile.role = role
        profile.department = department
        profile.save()
        return user

    def lodge(self):
        self.client.force_login(self.user)
        response = self.client.post("/new/", {
            "title": "Projector not working",
            "category": self.category.pk,
            "sub_category": self.sub_category.pk,
            "location": "Block C, Room 302",
            "description": "The projector in room 302 does not switch on.",
        })
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        return Complaint.objects.latest("created_at")

    def assert_complete(self, entry, field, old, new):
        """Every part SCMS-F-027 names must be present."""
        self.assertEqual(entry.field_name, field)
        self.assertEqual(entry.old_value, old)
        self.assertEqual(entry.new_value, new)
        self.assertIsNotNone(entry.changed_by, "no actor recorded")
        self.assertIsNotNone(entry.created_at, "no timestamp")
        self.assertIsNotNone(entry.source_ip, "no source IP recorded")

    # --- every flow writes a row ----------------------------------------

    def test_lodging_is_audited(self):
        """This was the one path recording no source IP at all."""
        complaint = self.lodge()
        entry = complaint.status_history.get()

        self.assert_complete(entry, "status", "", "Open")
        self.assertEqual(entry.changed_by, self.user)

    def test_assignment_records_the_owner_change(self):
        complaint = self.lodge()

        self.client.force_login(self.admin)
        self.client.post(
            f"/{complaint.reference_id}/assign/",
            {"agent": self.agent.pk, "reason": ""},
        )

        entry = complaint.status_history.first()
        self.assert_complete(entry, "assigned_to", "", "agent")

    def test_reassignment_records_both_owners(self):
        complaint = self.lodge()
        assign_complaint(complaint, self.agent, actor=self.admin)

        self.client.force_login(self.admin)
        self.client.post(
            f"/{complaint.reference_id}/assign/",
            {"agent": self.other_agent.pk, "reason": "On leave"},
        )

        entry = complaint.status_history.first()
        self.assert_complete(entry, "assigned_to", "agent", "agent2")

    def test_priority_override_records_both_values(self):
        complaint = self.lodge()

        self.client.force_login(self.admin)
        self.client.post(
            f"/{complaint.reference_id}/priority/",
            {
                "priority": Complaint.Priority.CRITICAL,
                "reason": "Affects an entire floor",
            },
        )

        entry = complaint.status_history.first()
        self.assert_complete(entry, "priority", "High", "Critical")

    def test_status_change_records_both_statuses(self):
        complaint = self.lodge()
        assign_complaint(complaint, self.agent, actor=self.admin)

        self.client.force_login(self.agent)
        self.client.post(
            f"/{complaint.reference_id}/status/",
            {"status": Complaint.Status.IN_PROGRESS, "comment": ""},
        )

        entry = complaint.status_history.first()
        self.assert_complete(entry, "status", "Assigned", "In Progress")

    def test_resolution_records_the_status_move(self):
        complaint = self.lodge()
        assign_complaint(complaint, self.agent, actor=self.admin)
        transition_status(
            complaint, Complaint.Status.IN_PROGRESS, actor=self.agent,
        )

        self.client.force_login(self.agent)
        self.client.post(
            f"/{complaint.reference_id}/resolve/",
            {
                "remarks": self.REMARKS,
                "proof": SimpleUploadedFile(
                    "fixed.jpg", self.JPEG, "image/jpeg",
                ),
            },
        )

        entry = complaint.status_history.first()
        self.assert_complete(entry, "status", "In Progress", "Resolved")

    # --- the trail as a whole --------------------------------------------

    def test_a_full_lifecycle_leaves_an_unbroken_trail(self):
        complaint = self.lodge()

        self.client.force_login(self.admin)
        self.client.post(
            f"/{complaint.reference_id}/assign/",
            {"agent": self.agent.pk, "reason": ""},
        )
        self.client.post(
            f"/{complaint.reference_id}/priority/",
            {
                "priority": Complaint.Priority.CRITICAL,
                "reason": "Affects an entire floor",
            },
        )

        self.client.force_login(self.agent)
        self.client.post(
            f"/{complaint.reference_id}/status/",
            {"status": Complaint.Status.IN_PROGRESS, "comment": ""},
        )
        self.client.post(
            f"/{complaint.reference_id}/resolve/",
            {
                "remarks": self.REMARKS,
                "proof": SimpleUploadedFile(
                    "fixed.jpg", self.JPEG, "image/jpeg",
                ),
            },
        )

        entries = list(complaint.status_history.order_by("created_at"))

        self.assertEqual(
            [e.field_name for e in entries],
            ["status", "assigned_to", "priority", "status", "status"],
        )

        for entry in entries:
            self.assertIsNotNone(entry.changed_by)
            self.assertIsNotNone(entry.source_ip)

    def test_no_flow_writes_a_row_without_an_actor(self):
        complaint = self.lodge()
        assign_complaint(complaint, self.agent, actor=self.admin)

        self.assertFalse(
            complaint.status_history.filter(changed_by__isnull=True)
            .exists()
        )

    # --- append-only, across the new flows --------------------------------

    def test_an_audited_row_cannot_be_edited_afterwards(self):
        complaint = self.lodge()
        entry = complaint.status_history.get()

        entry.new_value = "Tampered"

        with self.assertRaises(ValueError):
            entry.save()

    def test_an_audited_row_cannot_be_deleted(self):
        complaint = self.lodge()

        with self.assertRaises(ValueError):
            complaint.status_history.get().delete()

    def test_the_trail_survives_a_queryset_delete_attempt(self):
        """A bulk delete bypasses Model.delete, so check the count."""
        complaint = self.lodge()
        before = complaint.status_history.count()

        try:
            complaint.status_history.all().delete()
        except Exception:
            pass

        self.assertEqual(complaint.status_history.count(), before)


class PasswordHashingTests(TestCase):
    """SCMS-SR-002 / TC-Sec-02: Argon2id, and never in the clear.

    The acceptance criterion is stated as two checks an assessor would
    run by hand: inspect the database and see only hashes, then grep
    the logs for the password and find nothing. Both are asserted here
    so they hold on every run rather than on the day someone looks.
    """

    LOGIN_URL = "/accounts/login/"
    PASSWORD = "not-a-real-password"
    ARGON2ID_PREFIX = "argon2$argon2id$"

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username="asha",
            email="asha@example.com",
            password=self.PASSWORD,
        )

    def stored_hash(self):
        return self.User.objects.get(pk=self.user.pk).password

    def test_configured_hasher_is_argon2(self):
        """Guards the order in settings, not just today's behaviour."""
        self.assertEqual(
            settings.PASSWORD_HASHERS[0],
            "django.contrib.auth.hashers.Argon2PasswordHasher",
        )

    def test_new_password_is_stored_as_argon2id(self):
        # The variant matters: SCMS-SR-002 names Argon2id, and argon2i
        # or argon2d would both satisfy a looser check for "argon2".
        self.assertTrue(
            self.stored_hash().startswith(self.ARGON2ID_PREFIX),
            f"stored as {self.stored_hash()[:24]}, not Argon2id",
        )

    def test_raw_password_does_not_appear_in_the_record(self):
        """The database-inspection half of TC-Sec-02."""
        for field in (self.stored_hash(), repr(self.user.__dict__)):
            self.assertNotIn(self.PASSWORD, field)

    def test_password_is_not_written_to_any_log(self):
        """The log-grep half of TC-Sec-02.

        The first version of this test captured the root logger across
        a login and asserted the password appeared in none of the
        records. It passed, but for no reason: the login path emits no
        log records at all, so the loop ran zero times. A test that
        cannot fail proves nothing.

        It now enables SQL logging, which is both what a log grep on a
        running system would actually hit and a direct check that the
        raw password never travels as far as the database. The record
        count is asserted first so this can never go hollow again.
        """
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        queries = logging.getLogger("django.db.backends")
        previous_level = queries.level
        queries.addHandler(handler)
        queries.setLevel(logging.DEBUG)

        try:
            # django.db.backends logs statements only while DEBUG is on.
            with override_settings(DEBUG=True):
                self.User.objects.create_user(
                    username="logcheck",
                    email="logcheck@example.com",
                    password=self.PASSWORD,
                )
                self.client.post(
                    self.LOGIN_URL,
                    {
                        "username": "logcheck@example.com",
                        "password": self.PASSWORD,
                    },
                )
        finally:
            queries.removeHandler(handler)
            queries.setLevel(previous_level)

        self.assertGreater(
            len(records), 0, "nothing was logged, so nothing was checked",
        )

        for record in records:
            self.assertNotIn(
                self.PASSWORD,
                record.getMessage(),
                f"password reached the {record.name} log",
            )

    def test_password_is_not_echoed_back_in_the_response(self):
        response = self.client.post(
            self.LOGIN_URL,
            {"username": "asha@example.com", "password": "wrong-one"},
        )

        self.assertNotIn(b"wrong-one", response.content)

    def test_account_hashed_before_the_change_still_authenticates(self):
        """Accounts created under PBKDF2 must not be locked out."""
        legacy = make_password(self.PASSWORD, hasher="pbkdf2_sha256")
        self.User.objects.filter(pk=self.user.pk).update(password=legacy)

        self.assertTrue(self.stored_hash().startswith("pbkdf2_sha256$"))

        self.client.post(
            self.LOGIN_URL,
            {"username": "asha@example.com", "password": self.PASSWORD},
        )

        self.assertEqual(
            self.client.session.get("_auth_user_id"), str(self.user.pk),
        )

    def test_legacy_hash_is_upgraded_on_a_correct_login(self):
        """Why no password reset is needed to satisfy SCMS-SR-002.

        Django re-hashes with the first configured hasher when a login
        succeeds against an older one, so existing accounts migrate as
        their owners return.
        """
        legacy = make_password(self.PASSWORD, hasher="pbkdf2_sha256")
        self.User.objects.filter(pk=self.user.pk).update(password=legacy)

        self.client.post(
            self.LOGIN_URL,
            {"username": "asha@example.com", "password": self.PASSWORD},
        )

        self.assertTrue(
            self.stored_hash().startswith(self.ARGON2ID_PREFIX),
            "legacy hash was not upgraded on a correct login",
        )

    def test_wrong_password_does_not_upgrade_the_hash(self):
        """A failed attempt must not touch the stored credential."""
        legacy = make_password(self.PASSWORD, hasher="pbkdf2_sha256")
        self.User.objects.filter(pk=self.user.pk).update(password=legacy)

        self.client.post(
            self.LOGIN_URL,
            {"username": "asha@example.com", "password": "wrong-one"},
        )

        self.assertEqual(self.stored_hash(), legacy)


class AccountLockoutTests(TestCase):
    """SCMS-F-005 / TC-Auth-05: lock after 5 consecutive failures."""

    LOGIN_URL = "/accounts/login/"
    PASSWORD = "not-a-real-password"

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username="asha",
            email="asha@example.com",
            password=self.PASSWORD,
        )
        self.other = self.User.objects.create_user(
            username="bala",
            email="bala@example.com",
            password=self.PASSWORD,
        )

    def attempt(self, password, email="asha@example.com"):
        return self.client.post(
            self.LOGIN_URL, {"username": email, "password": password},
        )

    def fail_times(self, count, email="asha@example.com"):
        for _ in range(count):
            self.attempt("wrong-password", email=email)

    def profile(self, user=None):
        return UserProfile.objects.get(user=user or self.user)

    def logged_in(self):
        return self.client.session.get("_auth_user_id")

    def test_five_failures_lock_the_account(self):
        self.fail_times(LOCKOUT_THRESHOLD)

        self.assertIsNotNone(self.profile().locked_until)

    def test_four_failures_do_not_lock(self):
        """The threshold is 5, so 4 must still leave the door open."""
        self.fail_times(LOCKOUT_THRESHOLD - 1)

        self.assertIsNone(self.profile().locked_until)

        self.attempt(self.PASSWORD)

        self.assertEqual(self.logged_in(), str(self.user.pk))

    def test_correct_password_is_refused_while_locked(self):
        """The point of the lock: knowing the password is not enough."""
        self.fail_times(LOCKOUT_THRESHOLD)

        self.attempt(self.PASSWORD)

        self.assertIsNone(self.logged_in())

    def test_blocked_attempt_says_why(self):
        self.fail_times(LOCKOUT_THRESHOLD)

        response = self.attempt(self.PASSWORD)
        body = response.content.decode()

        self.assertIn("locked", body)
        # "incorrect password" would be actively misleading to someone
        # who has since remembered the right one. The login template
        # used to print exactly that for any failure, so a locked user
        # was given two contradictory reasons at once.
        self.assertNotIn("password is incorrect", body)

    def test_a_wrong_password_still_says_so(self):
        """Guards the template change made for the test above.

        Removing the hardcoded failure message must not leave an
        ordinary wrong password with no explanation at all.
        """
        response = self.attempt("wrong-password")

        self.assertIn("incorrect", response.content.decode())

    def test_locking_records_a_security_event(self):
        self.fail_times(LOCKOUT_THRESHOLD)

        event = SecurityEvent.objects.filter(
            event=SecurityEventType.ACCOUNT_LOCKED,
        ).get()

        self.assertEqual(event.user, self.user)
        self.assertEqual(event.email_attempted, "asha@example.com")
        self.assertIsNotNone(event.source_ip, "no source IP recorded")
        self.assertIn("15 minutes", event.detail)

    def test_only_one_lock_event_per_lock(self):
        """Further failures must not each write another lock row."""
        self.fail_times(LOCKOUT_THRESHOLD + 3)

        self.assertEqual(
            SecurityEvent.objects.filter(
                event=SecurityEventType.ACCOUNT_LOCKED,
            ).count(),
            1,
        )

    def test_attempts_during_a_lock_are_recorded(self):
        """What distinguishes a forgotten password from an attack."""
        self.fail_times(LOCKOUT_THRESHOLD)
        self.attempt("wrong-password")
        self.attempt(self.PASSWORD)

        self.assertEqual(
            SecurityEvent.objects.filter(
                event=SecurityEventType.LOGIN_BLOCKED,
            ).count(),
            2,
        )

    def test_attempts_during_a_lock_do_not_extend_it(self):
        """Otherwise an attacker could keep an account locked forever."""
        self.fail_times(LOCKOUT_THRESHOLD)
        locked_until = self.profile().locked_until

        self.fail_times(3)

        self.assertEqual(self.profile().locked_until, locked_until)

    def test_login_succeeds_once_the_lock_expires(self):
        self.fail_times(LOCKOUT_THRESHOLD)

        # An expired lock reads as unlocked with nothing to clear it,
        # so no scheduled job is needed for the lock to lift.
        UserProfile.objects.filter(user=self.user).update(
            locked_until=timezone.now() - timedelta(seconds=1),
        )

        self.attempt(self.PASSWORD)

        self.assertEqual(self.logged_in(), str(self.user.pk))

    def test_successful_login_clears_the_failure_count(self):
        """'Consecutive' is the operative word in SCMS-F-005."""
        self.fail_times(LOCKOUT_THRESHOLD - 1)
        self.attempt(self.PASSWORD)

        self.assertEqual(self.profile().failed_login_attempts, 0)

        self.client.logout()
        self.fail_times(LOCKOUT_THRESHOLD - 1)

        self.assertIsNone(self.profile().locked_until)

    def test_lockout_is_per_account(self):
        self.fail_times(LOCKOUT_THRESHOLD)

        self.assertIsNone(self.profile(self.other).locked_until)

        self.attempt(self.PASSWORD, email="bala@example.com")

        self.assertEqual(self.logged_in(), str(self.other.pk))

    def test_unknown_email_records_no_event(self):
        """Nothing to lock, and no row naming an account that is not."""
        self.fail_times(LOCKOUT_THRESHOLD, email="nobody@example.com")

        self.assertEqual(SecurityEvent.objects.count(), 0)

    def test_security_events_cannot_be_altered_or_deleted(self):
        self.fail_times(LOCKOUT_THRESHOLD)
        event = SecurityEvent.objects.get(
            event=SecurityEventType.ACCOUNT_LOCKED,
        )

        with self.assertRaises(ValueError):
            event.detail = "nothing happened"
            event.save()

        with self.assertRaises(ValueError):
            event.delete()

        # The queryset routes bypass Model.save and Model.delete, so
        # they need their own guard (the same bug found in T8-34).
        with self.assertRaises(ValueError):
            SecurityEvent.objects.all().delete()

        with self.assertRaises(ValueError):
            SecurityEvent.objects.all().update(detail="nothing happened")


@skipUnless(
    connection.vendor == "postgresql",
    "SCMS-SR-006 is enforced on PostgreSQL, the deployment target; "
    "migration 0011 is a no-op on SQLite",
)
class DatabaseLevelAuditGuardTests(BaseFixture):
    """SCMS-SR-006 / TC-Sec-06: the trail resists the database itself.

    T8-34 made these tables append-only in the application. That
    binds code going through Django and nothing else: anything with
    the database credentials could still rewrite the trail, which is
    exactly what an audit log exists to survive.

    These tests go around the ORM entirely and issue raw SQL, which
    is what an attacker or a careless script would do. They run only
    on PostgreSQL, so they are proven by CI rather than by a local
    SQLite run -- which is a large part of why CI was moved onto
    PostgreSQL in T8-99.
    """

    def setUp(self):
        super().setUp()
        self.complaint = self.make_complaint()
        self.entry = ComplaintStatusHistory.objects.create(
            complaint=self.complaint,
            old_status="",
            new_status=Complaint.Status.OPEN,
            field_name="status",
            old_value="",
            new_value="Open",
            changed_by=self.user,
            source_ip="127.0.0.1",
            comment="Complaint lodged.",
        )
        self.event = SecurityEvent.objects.create(
            event=SecurityEventType.ACCOUNT_LOCKED,
            user=self.user,
            email_attempted="asha@example.com",
            source_ip="127.0.0.1",
            detail="Locked for 15 minutes.",
        )

    def run_sql(self, statement, params=None):
        """Execute raw SQL inside its own atomic block.

        A statement the database refuses marks the surrounding
        transaction as failed on PostgreSQL, so without the nested
        block every later query in the test would fail too and the
        failure would look like something else.
        """
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement, params or [])

    def test_complaint_audit_row_cannot_be_updated_by_raw_sql(self):
        with self.assertRaises(DatabaseError) as caught:
            self.run_sql(
                "UPDATE complaints_complaintstatushistory "
                "SET new_value = %s WHERE id = %s",
                ["Closed", self.entry.pk],
            )

        self.assertIn("SCMS-SR-006", str(caught.exception))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.new_value, "Open")

    def test_complaint_audit_row_cannot_be_deleted_by_raw_sql(self):
        with self.assertRaises(DatabaseError) as caught:
            self.run_sql(
                "DELETE FROM complaints_complaintstatushistory "
                "WHERE id = %s",
                [self.entry.pk],
            )

        self.assertIn("SCMS-SR-006", str(caught.exception))
        self.assertTrue(
            ComplaintStatusHistory.objects.filter(pk=self.entry.pk).exists(),
        )

    def test_whole_complaint_trail_cannot_be_wiped(self):
        """The unqualified statement an attacker would actually run."""
        with self.assertRaises(DatabaseError):
            self.run_sql("DELETE FROM complaints_complaintstatushistory")

        self.assertTrue(ComplaintStatusHistory.objects.exists())

    def test_security_event_cannot_be_updated_by_raw_sql(self):
        with self.assertRaises(DatabaseError) as caught:
            self.run_sql(
                "UPDATE complaints_securityevent SET detail = %s "
                "WHERE id = %s",
                ["nothing happened", self.event.pk],
            )

        self.assertIn("SCMS-SR-006", str(caught.exception))

    def test_security_event_cannot_be_deleted_by_raw_sql(self):
        with self.assertRaises(DatabaseError) as caught:
            self.run_sql(
                "DELETE FROM complaints_securityevent WHERE id = %s",
                [self.event.pk],
            )

        self.assertIn("SCMS-SR-006", str(caught.exception))

    def test_rows_can_still_be_appended(self):
        """Append-only must still mean append.

        A guard that made the tables read-only would pass every test
        above and break the audit trail completely, so the writable
        half is asserted too.
        """
        ComplaintStatusHistory.objects.create(
            complaint=self.complaint,
            old_status=Complaint.Status.OPEN,
            new_status=Complaint.Status.ASSIGNED,
            field_name="status",
            old_value="Open",
            new_value="Assigned",
            changed_by=self.user,
            source_ip="127.0.0.1",
            comment="Assigned.",
        )
        SecurityEvent.objects.create(
            event=SecurityEventType.LOGIN_BLOCKED,
            user=self.user,
            email_attempted="asha@example.com",
            source_ip="127.0.0.1",
            detail="Login attempted while the account was locked.",
        )

        self.assertEqual(
            ComplaintStatusHistory.objects.filter(
                complaint=self.complaint,
            ).count(),
            2,
        )
        self.assertEqual(SecurityEvent.objects.count(), 2)

    def test_ordinary_tables_are_still_writable(self):
        """The guard must be on the audit tables and nowhere else."""
        self.run_sql(
            "UPDATE complaints_complaint SET title = %s WHERE id = %s",
            ["Edited directly", self.complaint.pk],
        )

        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.title, "Edited directly")


class RoleFixture(BaseFixture):
    """Users across the four roles for authorisation tests."""

    def setUp(self):
        super().setUp()
        from .roles import Role

        self.other_department = Department.objects.create(
            name="Facilities", code="facilities",
        )
        self.User = get_user_model()

        def make(username, role, department=None):
            user = self.User.objects.create_user(
                username=username, password="not-a-real-password",
            )
            profile = user.profile
            profile.role = role
            profile.department = department
            profile.save()
            return user

        self.other_complainant = make("other", Role.COMPLAINANT)
        self.agent = make("agent", Role.SUPPORT_AGENT, self.department)
        self.other_agent = make(
            "otheragent", Role.SUPPORT_AGENT, self.other_department,
        )
        self.dept_admin = make(
            "deptadmin", Role.DEPARTMENT_ADMIN, self.department,
        )
        self.other_dept_admin = make(
            "otherdeptadmin", Role.DEPARTMENT_ADMIN, self.other_department,
        )
        self.sys_admin = make("sysadmin", Role.SYSTEM_ADMIN)

        self.complaint = self.make_complaint(
            assigned_department=self.department,
            assigned_to=self.agent,
        )


class ProfileTests(RoleFixture):
    """SCMS-F-003: every user gets a role."""

    def test_new_user_defaults_to_complainant(self):
        from .roles import Role
        fresh = self.User.objects.create_user(
            username="fresh", password="not-a-real-password",
        )
        self.assertEqual(fresh.profile.role, Role.COMPLAINANT)

    def test_role_and_department_persist(self):
        from .roles import Role
        self.assertEqual(self.agent.profile.role, Role.SUPPORT_AGENT)
        self.assertEqual(self.agent.profile.department, self.department)


class ObjectLevelAccessTests(RoleFixture):
    """SCMS-SR-003 / TC-Sec-03: deny-by-default, object level."""

    def test_owner_can_view(self):
        from .permissions import can_view_complaint
        self.assertTrue(can_view_complaint(self.user, self.complaint))

    def test_other_complainant_cannot_view(self):
        from .permissions import can_view_complaint
        self.assertFalse(
            can_view_complaint(self.other_complainant, self.complaint)
        )

    def test_assigned_agent_can_view(self):
        from .permissions import can_view_complaint
        self.assertTrue(can_view_complaint(self.agent, self.complaint))

    def test_agent_from_another_department_cannot_view(self):
        from .permissions import can_view_complaint
        self.assertFalse(
            can_view_complaint(self.other_agent, self.complaint)
        )

    def test_department_admin_sees_own_department_only(self):
        from .permissions import can_view_complaint
        self.assertTrue(can_view_complaint(self.dept_admin, self.complaint))
        self.assertFalse(
            can_view_complaint(self.other_dept_admin, self.complaint)
        )

    def test_system_admin_sees_everything(self):
        from .permissions import can_view_complaint
        self.assertTrue(can_view_complaint(self.sys_admin, self.complaint))

    def test_complainant_cannot_work_own_complaint(self):
        from .permissions import can_work_complaint
        self.assertFalse(can_work_complaint(self.user, self.complaint))

    def test_agent_can_work_assigned_complaint(self):
        from .permissions import can_work_complaint
        self.assertTrue(can_work_complaint(self.agent, self.complaint))

    def test_user_without_profile_is_denied(self):
        from .permissions import can_view_complaint
        orphan = self.User.objects.create_user(
            username="orphan", password="not-a-real-password",
        )
        orphan.profile.delete()
        orphan.refresh_from_db()
        self.assertFalse(can_view_complaint(orphan, self.complaint))


class SecureEvidenceStorageTests(BaseFixture):
    """SCMS-SR-005 / TC-Sec-05: how evidence is stored and served.

    Most of this was built in T8-15 and never asserted as security
    behaviour. The acceptance criterion names two checks -- an
    uploaded .php file is rejected, and a stored file cannot be
    executed through a direct URL -- and both are here, alongside
    the storage rules the requirement lists.

    One clause of SCMS-SR-005 is NOT met: uploads are not scanned
    for malware, because no scanner is available to this project.
    test_no_malware_scanner_is_configured_by_default asserts that
    plainly, so the gap stays visible instead of being discovered
    by whoever trusts the requirement later.
    """

    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    PHP = b"<?php system($_GET['c']); ?>"
    EXE = b"MZ\x90\x00" + b"\x00" * 64

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.complaint = self.make_complaint(
            assigned_department=self.department,
        )
        self.client.force_login(self.user)

    def upload(self, name, content):
        return SimpleUploadedFile(name, content)

    def post_attachment(self, name, content):
        return self.client.post(
            f"/{self.complaint.reference_id}/attach/",
            {"file": self.upload(name, content)},
        )

    def stored_attachment(self):
        """Store a valid attachment and return it."""
        self.post_attachment("evidence.jpg", self.JPEG)

        return self.complaint.attachments.get()

    # --- the two acceptance checks ---------------------------------

    def test_a_php_file_is_rejected(self):
        from .attachments import AttachmentRejected, validate_upload

        with self.assertRaises(AttachmentRejected) as caught:
            validate_upload(self.upload("shell.php", self.PHP))

        self.assertIn("not a JPG, PNG or PDF", str(caught.exception))
        self.assertEqual(self.complaint.attachments.count(), 0)

    def test_php_disguised_as_an_image_is_rejected(self):
        """Renaming it is the obvious next thing an attacker tries."""
        from .attachments import AttachmentRejected, validate_upload

        with self.assertRaises(AttachmentRejected):
            validate_upload(self.upload("shell.jpg", self.PHP))

    def test_an_image_renamed_to_php_is_rejected(self):
        """Real JPEG bytes must not buy a .php name on disk."""
        from .attachments import AttachmentRejected, validate_upload

        with self.assertRaises(AttachmentRejected) as caught:
            validate_upload(self.upload("payload.php", self.JPEG))

        self.assertIn("extension", str(caught.exception))

    def test_an_executable_is_rejected(self):
        from .attachments import AttachmentRejected, validate_upload

        with self.assertRaises(AttachmentRejected):
            validate_upload(self.upload("virus.jpg", self.EXE))

    def test_a_stored_file_is_not_reachable_by_url(self):
        """The second acceptance check, from both directions."""
        from django.urls import get_resolver
        from django.views.static import serve

        attachment = self.stored_attachment()

        # The guarantee is not that MEDIA_URL is unset -- Django 5.2
        # defaults it to "/" whether or not anything serves it. It is
        # that no route hands out files from MEDIA_ROOT, so a stored
        # file has no URL to request at all.
        serving = [
            pattern
            for pattern in get_resolver().url_patterns
            if getattr(pattern, "callback", None) is serve
        ]

        self.assertEqual(
            serving, [], "a route serves files straight from disk",
        )

        for candidate in (
            f"/media/{attachment.file.name}",
            f"/{attachment.file.name}",
        ):
            with self.subTest(url=candidate):
                self.assertEqual(
                    self.client.get(candidate).status_code, 404,
                )

    # --- the storage rules the requirement lists -------------------

    def test_the_stored_name_is_random_not_the_uploaded_one(self):
        attachment = self.stored_attachment()
        stored = Path(attachment.file.name).stem

        self.assertNotIn("evidence", attachment.file.name)
        self.assertEqual(len(stored), 32, "not a uuid4 hex name")
        self.assertEqual(attachment.original_name, "evidence.jpg")

    def test_uploads_are_stored_outside_the_source_tree(self):
        attachment = self.stored_attachment()
        stored = Path(settings.MEDIA_ROOT) / attachment.file.name

        self.assertTrue(stored.exists())
        self.assertNotIn(
            "complaints/templates", str(stored),
            "an upload landed somewhere it could be served from",
        )

    def test_the_declared_type_is_the_detected_one(self):
        """Not the one the client sent, which it chooses freely."""
        response = self.client.post(
            f"/{self.complaint.reference_id}/attach/",
            {"file": SimpleUploadedFile(
                "evidence.jpg", self.JPEG, content_type="text/html",
            )},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.complaint.attachments.get().content_type, "image/jpeg",
        )

    # --- how it is served ------------------------------------------

    def test_download_is_served_as_an_attachment(self):
        attachment = self.stored_attachment()

        response = self.client.get(
            f"/{self.complaint.reference_id}/attachments/"
            f"{attachment.pk}/",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "attachment", response["Content-Disposition"],
        )

    def test_download_forbids_content_type_sniffing(self):
        attachment = self.stored_attachment()

        response = self.client.get(
            f"/{self.complaint.reference_id}/attachments/"
            f"{attachment.pk}/",
        )

        self.assertEqual(
            response["X-Content-Type-Options"], "nosniff",
        )

    # --- the malware clause ----------------------------------------

    def test_no_malware_scanner_is_configured_by_default(self):
        """SCMS-SR-005 is not fully met, and says so here.

        A scanner is an external service and none is available to
        this project. Asserting the gap keeps it visible: if someone
        later wires one up, this test fails and asks them to say so.
        """
        from .attachments import get_scanner

        self.assertIsNone(
            get_scanner(),
            "a scanner is configured; update SCMS-SR-005's status",
        )

    def test_a_configured_scanner_sees_every_upload(self):
        from .attachments import validate_upload

        with override_settings(
            ATTACHMENT_MALWARE_SCANNER=(
                "complaints.tests._scanner_that_records"
            ),
        ):
            _SCANNER_CALLS.clear()
            validate_upload(self.upload("evidence.jpg", self.JPEG))

        self.assertEqual(_SCANNER_CALLS, ["evidence.jpg"])

    def test_a_flagged_upload_is_refused(self):
        from .attachments import AttachmentRejected, validate_upload

        with override_settings(
            ATTACHMENT_MALWARE_SCANNER=(
                "complaints.tests._scanner_that_flags"
            ),
        ):
            with self.assertRaises(AttachmentRejected) as caught:
                validate_upload(self.upload("evidence.jpg", self.JPEG))

        self.assertIn("malware scanner", str(caught.exception))
        self.assertIn("not been stored", str(caught.exception))

    def test_the_scanner_does_not_consume_the_upload(self):
        """A scanner that reads the file must not leave it empty."""
        from .attachments import validate_upload

        upload = self.upload("evidence.jpg", self.JPEG)

        with override_settings(
            ATTACHMENT_MALWARE_SCANNER=(
                "complaints.tests._scanner_that_reads_everything"
            ),
        ):
            validate_upload(upload)

        self.assertEqual(upload.read(), self.JPEG)


class UrlTamperingSweepTests(RoleFixture):
    """SCMS-SR-003 / TC-Sec-03 across every complaint-scoped route.

    The acceptance criterion is one sentence -- changing the
    complaint ID in the URL to another user's complaint returns 403
    -- and it was previously demonstrated on the detail view alone.
    One route passing says nothing about the other seven, and an
    authorisation hole only needs one.

    So every route carrying a reference_id is swept, and
    test_every_complaint_route_is_swept fails if a new one is added
    without being listed here. That is the part that keeps working
    after this sprint: a route added later with no object-level
    check cannot pass CI quietly.
    """

    #: name -> (HTTP method, POST data)
    ROUTES = {
        "complaint_detail": ("get", None),
        "complaint_comment_add": ("post", {"body": "Any comment."}),
        "complaint_attachment_add": ("post", {}),
        "complaint_attachment_download": ("get", None),
        "complaint_assign": ("post", {"assigned_to": "1"}),
        "complaint_priority": ("post", {"priority": "HIGH",
                                        "reason": "Any reason at all."}),
        "complaint_status": ("post", {"status": "IN_PROGRESS"}),
        "complaint_resolve": ("post", {"remarks": "x" * 60}),
    }

    def path_for(self, name):
        reference = self.complaint.reference_id

        if name == "complaint_attachment_download":
            return f"/{reference}/attachments/1/"

        suffix = {
            "complaint_detail": "",
            "complaint_comment_add": "comment/",
            "complaint_attachment_add": "attach/",
            "complaint_assign": "assign/",
            "complaint_priority": "priority/",
            "complaint_status": "status/",
            "complaint_resolve": "resolve/",
        }[name]

        return f"/{reference}/{suffix}"

    def request(self, name):
        method, data = self.ROUTES[name]
        send = getattr(self.client, method)

        return send(self.path_for(name), data) if data else send(
            self.path_for(name),
        )

    def assert_all_denied(self, user, who):
        for name in self.ROUTES:
            with self.subTest(route=name, user=who):
                self.client.force_login(user)
                response = self.request(name)
                self.assertEqual(
                    response.status_code,
                    403,
                    f"{who} reached {name} with status "
                    f"{response.status_code}",
                )
                self.client.logout()

    def test_every_complaint_route_is_swept(self):
        """Fails when a new complaint route is added unswept.

        Without this, the sweep below silently stops being a sweep
        the first time someone adds a route.
        """
        from . import urls

        routed = {
            pattern.name
            for pattern in urls.urlpatterns
            if "reference_id" in str(pattern.pattern)
        }

        # Without this, a discovery that found nothing would make
        # the comparison below trivially true and the sweep hollow.
        self.assertGreaterEqual(
            len(routed), 8, "route discovery found almost nothing",
        )

        self.assertEqual(
            routed - set(self.ROUTES),
            set(),
            "a complaint route is not covered by the tampering sweep",
        )

    def test_the_right_department_admin_is_allowed_through(self):
        """The positive control for the denial tests above.

        If the staff routes were broken or unreachable for everyone,
        every denial test would pass while proving nothing about
        authorisation. This asserts the same routes do work for the
        Department Admin who owns the complaint, so the denials are
        about the object and not about the route.
        """
        self.client.force_login(self.dept_admin)

        for name in ("complaint_detail", "complaint_assign",
                     "complaint_priority"):
            with self.subTest(route=name):
                response = self.client.get(self.path_for(name))
                self.assertEqual(
                    response.status_code,
                    200,
                    f"{name} refused the admin who should have access",
                )

    def test_a_stranger_is_denied_every_route(self):
        """Another complainant, who owns nothing here."""
        self.assert_all_denied(self.other_complainant, "other complainant")

    def test_an_agent_from_another_department_is_denied_every_route(self):
        self.assert_all_denied(self.other_agent, "agent, wrong department")

    def test_an_admin_of_another_department_is_denied_every_route(self):
        """The sharpest case: the role is right, the object is not.

        A Department Admin passes every role check on the staff
        routes, so only the object-level check stands between them
        and another department's complaint.
        """
        self.assert_all_denied(
            self.other_dept_admin, "department admin, wrong department",
        )

    def test_anonymous_is_sent_to_login_not_shown_a_403(self):
        """Deny-by-default must still be usable when logged out."""
        for name in self.ROUTES:
            with self.subTest(route=name):
                response = self.request(name)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response["Location"])

    def test_the_owner_cannot_use_the_staff_routes(self):
        """Reading your own complaint is not working on it."""
        staff_only = (
            "complaint_assign",
            "complaint_priority",
            "complaint_status",
            "complaint_resolve",
        )

        for name in staff_only:
            with self.subTest(route=name):
                self.client.force_login(self.user)
                self.assertEqual(self.request(name).status_code, 403)
                self.client.logout()


class ViewAccessTests(RoleFixture):
    """The URL-tampering case from TC-Sec-03, through the view."""

    def url(self):
        return f"/{self.complaint.reference_id}/"

    def test_owner_gets_200(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_other_complainant_gets_403(self):
        self.client.force_login(self.other_complainant)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_agent_from_another_department_gets_403(self):
        self.client.force_login(self.other_agent)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_staff_cannot_lodge_complaints(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get("/new/").status_code, 403)

    def test_complainant_cannot_open_staff_queue(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/queue/").status_code, 403)

    def test_agent_queue_shows_only_assigned(self):
        self.client.force_login(self.other_agent)
        response = self.client.get("/queue/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.complaint.reference_id)
