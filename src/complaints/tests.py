import re
from datetime import datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from .models import (
    Category,
    Complaint,
    ComplaintStatusHistory,
    Department,
    SubCategory,
)
from .services import calculate_sla_deadline, can_reopen


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
