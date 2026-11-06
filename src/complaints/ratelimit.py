"""Rate limits on public endpoints (SCMS-SR-007).

A fixed-window counter in Django's cache. Fixed rather than
sliding because the requirement is stated as a count per window
("5 per hour", "10 per 5 minutes") and a fixed window is the
thing a reader can check by hand. The known cost is the boundary:
someone can make 5 submissions at 10:59 and 5 more at 11:00. For
a limit whose purpose is to blunt automated abuse rather than to
meter a paid API, that is acceptable, and saying so is better
than implying a precision the code does not have.

TWO CLAUSES OF SCMS-SR-007 ARE NOT MET HERE, both because they
depend on work that is still in the backlog:

* "per mobile number" -- there is no mobile number on an account.
  Registration with a verified mobile is SCMS-F-001 (T8-8), which
  is not built. The submission limit is applied per account
  instead, which is the same protection for a registered user and
  no protection against someone creating accounts.
* "CAPTCHA on guest lodging" -- guest lodging is SCMS-F-009
  (T8-16), also not built. There is no guest path to protect yet.

DEPLOYMENT: the default cache is per-process local memory, so
with more than one application instance each gets its own
counters and the effective limit multiplies. A shared cache
(Redis, memcached) is required for the limits to hold across
instances, and that is the same scaling work SCMS-NF-003 needs.
"""

from django.conf import settings
from django.core.cache import cache

#: Complaint submissions allowed per account per window.
SUBMISSION_LIMIT = 5
SUBMISSION_WINDOW = 60 * 60

#: Login attempts allowed per IP address per window.
LOGIN_LIMIT = 10
LOGIN_WINDOW = 5 * 60


def _enabled():
    """Off in tests that do not opt in, on everywhere else.

    Without this every test that posts a form twice would start
    tripping limits, and the limit would be tested by accident in
    places that are not about limits at all.
    """
    return getattr(settings, "RATE_LIMITS_ENABLED", True)


def register(key, limit, window):
    """Count one attempt against a key. True if it is over the limit.

    The counter is created with the window as its lifetime, so the
    window expires on its own and nothing has to sweep it.
    """
    if not _enabled():
        return False

    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1

    return count > limit


def submission_key(user):
    return f"scms:submissions:{user.pk}"


def login_key(ip):
    return f"scms:logins:{ip or 'unknown'}"


def too_many_submissions(user):
    """SCMS-SR-007: the 6th submission in an hour is throttled."""
    return register(
        submission_key(user), SUBMISSION_LIMIT, SUBMISSION_WINDOW,
    )


def too_many_logins(ip):
    """SCMS-SR-007: the 11th attempt in 5 minutes is throttled."""
    return register(login_key(ip), LOGIN_LIMIT, LOGIN_WINDOW)
