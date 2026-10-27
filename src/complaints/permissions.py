"""Authorisation for SCMS (SCMS-F-003, SCMS-SR-003).

Two layers, both deny-by-default:

* ``role_required`` guards a view by role.
* ``can_view_complaint`` / ``can_work_complaint`` are the
  object-level checks that stop one user reaching another
  user's complaint by changing the URL.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required

from .roles import Role, get_department, get_role, has_role


def role_required(*roles):
    """Allow only the listed roles; everyone else gets HTTP 403.

    Wraps login_required, so an anonymous user is sent to the
    login page rather than shown a 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            if not has_role(request.user, *roles):
                raise PermissionDenied(
                    "Your role does not permit this action."
                )

            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def can_view_complaint(user, complaint):
    """Who may read a complaint.

    The complainant who lodged it, the agent assigned to it, a
    Department Admin of the owning department, or a System
    Admin. Nobody else — in particular, not another complainant
    and not an agent from a different department.
    """
    role = get_role(user)

    if role is None:
        return False

    if role == Role.SYSTEM_ADMIN:
        return True

    if complaint.submitted_by_id == user.id:
        return True

    if role == Role.SUPPORT_AGENT:
        return complaint.assigned_to_id == user.id

    if role == Role.DEPARTMENT_ADMIN:
        department = get_department(user)

        return (
            department is not None
            and complaint.assigned_department_id == department.id
        )

    return False


def can_work_complaint(user, complaint):
    """Who may change a complaint's state.

    Staff only. A complainant can read their own complaint but
    cannot move it through the workflow.
    """
    role = get_role(user)

    if role is None or role == Role.COMPLAINANT:
        return False

    return can_view_complaint(user, complaint)


def require_complaint_access(complaint, user, write=False):
    """Raise PermissionDenied unless the user may access this complaint."""
    allowed = (
        can_work_complaint(user, complaint)
        if write
        else can_view_complaint(user, complaint)
    )

    if not allowed:
        raise PermissionDenied(
            "You do not have access to this complaint."
        )
