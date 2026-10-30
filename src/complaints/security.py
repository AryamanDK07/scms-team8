"""Brute-force lockout for authentication (SCMS-F-005).

Five consecutive failed logins lock an account for fifteen minutes
and write a SecurityEvent. A correct password resets the count, so
the threshold counts a run of failures rather than failures ever.

A deliberate limitation worth stating. Locking by account means
someone who knows a colleague's e-mail address can lock them out on
purpose by failing five times. That is the behaviour SCMS-F-005
asks for, and it is the usual trade: the alternative, not locking,
leaves the password itself as the only barrier. The IP-based limits
in SCMS-SR-007 (T8-49) are the half that frustrates an attacker
without giving them a way to deny service, which is why the two
belong together rather than either alone.
"""

from datetime import timedelta
from math import ceil

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import SecurityEvent, SecurityEventType

#: Consecutive failures that trigger a lock (SCMS-F-005).
LOCKOUT_THRESHOLD = 5

#: How long the account stays locked once the threshold is reached.
LOCKOUT_DURATION = timedelta(minutes=15)


def _profile(user):
    return getattr(user, "profile", None)


def is_locked(user, now=None):
    """Whether this account is currently locked.

    An expired lock reads as unlocked without anything having to
    clear it, so no scheduled job is needed for the lock to lift.
    """
    profile = _profile(user)

    if profile is None or profile.locked_until is None:
        return False

    return profile.locked_until > (now or timezone.now())


def lockout_minutes_remaining(user, now=None):
    """Whole minutes left on the lock, rounded up, at least 1."""
    profile = _profile(user)

    if profile is None or profile.locked_until is None:
        return 0

    seconds = (profile.locked_until - (now or timezone.now())).total_seconds()

    if seconds <= 0:
        return 0

    return max(1, ceil(seconds / 60))


def find_user_by_email(email):
    """Return the single account for an e-mail address, or None.

    Mirrors EmailBackend: a duplicated address resolves to nobody
    rather than to a guess.
    """
    if not email:
        return None

    UserModel = get_user_model()

    matches = list(
        UserModel.objects.filter(email__iexact=email.strip())[:2]
    )

    return matches[0] if len(matches) == 1 else None


def register_failed_login(user, email="", source_ip=None):
    """Count a failed attempt and lock the account at the threshold.

    Returns True if this attempt caused the lock.

    The read and the write are in one transaction with the row
    locked, so two attempts arriving together cannot both read the
    same count and leave the account one failure short of locking.
    """
    profile = _profile(user)

    if profile is None:
        return False

    with transaction.atomic():
        from .models import UserProfile

        locked_row = (
            UserProfile.objects.select_for_update().get(pk=profile.pk)
        )

        locked_row.failed_login_attempts += 1
        newly_locked = (
            locked_row.failed_login_attempts >= LOCKOUT_THRESHOLD
            and (
                locked_row.locked_until is None
                or locked_row.locked_until <= timezone.now()
            )
        )

        if newly_locked:
            locked_row.locked_until = timezone.now() + LOCKOUT_DURATION

        locked_row.save(
            update_fields=["failed_login_attempts", "locked_until"],
        )

        if newly_locked:
            SecurityEvent.objects.create(
                event=SecurityEventType.ACCOUNT_LOCKED,
                user=user,
                email_attempted=email or user.email,
                source_ip=source_ip,
                detail=(
                    f"Locked for "
                    f"{int(LOCKOUT_DURATION.total_seconds() // 60)} minutes "
                    f"after {locked_row.failed_login_attempts} consecutive "
                    f"failed login attempts."
                ),
            )

    profile.refresh_from_db(
        fields=["failed_login_attempts", "locked_until"],
    )

    return newly_locked


def register_blocked_attempt(user, email="", source_ip=None):
    """Record an attempt made while the account is already locked.

    Written separately from the lock itself so the trail shows
    whether anyone kept trying, which is what distinguishes a
    forgotten password from an attack.
    """
    SecurityEvent.objects.create(
        event=SecurityEventType.LOGIN_BLOCKED,
        user=user,
        email_attempted=email or user.email,
        source_ip=source_ip,
        detail="Login attempted while the account was locked.",
    )


def register_successful_login(user):
    """Clear the failure count after a correct password."""
    profile = _profile(user)

    if profile is None:
        return

    if profile.failed_login_attempts == 0 and profile.locked_until is None:
        return

    profile.failed_login_attempts = 0
    profile.locked_until = None
    profile.save(update_fields=["failed_login_attempts", "locked_until"])
