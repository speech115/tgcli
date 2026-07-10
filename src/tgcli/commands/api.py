"""Raw API policy helpers.

Raw request construction and dispatch land in later Phase 2B tasks.  This
module deliberately contains only the fail-closed method classifier.
"""


READ_VERBS = ("get", "search", "check", "resolve")


def is_read_method(name: str) -> bool:
    """Return whether a TL method's final segment has an allowlisted read verb."""
    method = name.rsplit(".", maxsplit=1)[-1]
    return method.startswith(READ_VERBS)
