"""Role definitions for SCMS (SCMS-F-003).

The four roles named in the SRS. Role membership lives on
UserProfile; this module holds the vocabulary and the small
helpers that the permission layer builds on.
"""

from django.db import models


class Role(models.TextChoices):
    COMPLAINANT = "COMPLAINANT", "Complainant"
    SUPPORT_AGENT = "SUPPORT_AGENT", "Support Agent"
    DEPARTMENT_ADMIN = "DEPARTMENT_ADMIN", "Department Admin"
    SYSTEM_ADMIN = "SYSTEM_ADMIN", "System Admin"


#: Roles that may act on complaints belonging to a department.
STAFF_ROLES = (
    Role.SUPPORT_AGENT,
    Role.DEPARTMENT_ADMIN,
    Role.SYSTEM_ADMIN,
)


def get_role(user):
    """Return the user's role, or None if they have no profile.

    Deny-by-default (SCMS-SR-003): callers treat None as "no
    permissions", so a user without a profile can do nothing
    rather than inheriting a default role.
    """
    if not user or not user.is_authenticated:
        return None

    profile = getattr(user, "profile", None)

    return profile.role if profile else None


def get_department(user):
    """Return the department a staff user belongs to, or None."""
    profile = getattr(user, "profile", None)

    return profile.department if profile else None


def has_role(user, *roles):
    """Whether the user holds any of the given roles."""
    current = get_role(user)

    return current is not None and current in roles
