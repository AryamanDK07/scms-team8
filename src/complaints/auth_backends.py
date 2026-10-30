"""E-mail based authentication for SCMS (SCMS-F-002).

The SRS requires users to sign in with their e-mail address rather
than a username. Django's default ``ModelBackend`` looks accounts up
by username, so this backend looks them up by e-mail instead.

``django.contrib.auth.models.User`` does not enforce a unique
e-mail address. If two accounts somehow share one, this backend
refuses both rather than guessing which was meant, so that one user
can never be authenticated into another user's account.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from . import security


class EmailBackend(ModelBackend):
    """Authenticate on e-mail address and password.

    The credential arrives in the ``username`` argument because that
    is the name Django's login machinery uses for the first field; it
    is treated here as an e-mail address.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()

        email = username or kwargs.get(UserModel.USERNAME_FIELD)

        if email is None or password is None:
            return None

        matches = list(
            UserModel.objects.filter(email__iexact=email.strip())
        )

        # Run the hasher even when no account matches, so that an
        # unknown e-mail takes as long to reject as a known one and
        # cannot be identified by response timing.
        if len(matches) != 1:
            UserModel().set_password(password)
            return None

        user = matches[0]
        source_ip = request.META.get("REMOTE_ADDR") if request else None

        # Brute-force lockout (SCMS-F-005). Checked before the
        # password so that attempts made during a lock neither
        # succeed with the right password nor extend the lock with
        # the wrong one; the lock runs for a fixed fifteen minutes
        # from the failure that caused it.
        if security.is_locked(user):
            security.register_blocked_attempt(user, email, source_ip)
            return None

        # user_can_authenticate() rejects inactive accounts, which is
        # what makes deactivation in SCMS-F-024 take effect at login.
        if user.check_password(password):
            if not self.user_can_authenticate(user):
                return None

            security.register_successful_login(user)

            return user

        security.register_failed_login(user, email, source_ip)

        return None
