import re
import shutil
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .attachments import MAX_BYTES
from .models import (
    Category,
    Complaint,
    ComplaintAttachment,
    ComplaintStatusHistory,
    Department,
    SubCategory,
)
from .services import (
    assign_complaint,
    calculate_sla_deadline,
    can_reopen,
    eligible_agents,
    override_priority,
    priority_for,
)


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
