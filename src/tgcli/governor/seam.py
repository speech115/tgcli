"""The governed seam: Telethon's private ``_call`` (ADR-0072 decision 2).

``__call__`` was rejected as the seam because media downloads and the
CDN-redirect path reach ``_call`` directly, bypassing it — the exact routes a
bulk archive run spends most of its requests on. Governing the public method
would have left the heaviest traffic ungoverned while looking complete.

The cost of that choice is a dependency on a private method, so it is paid
loudly rather than quietly: :func:`verify_seam` runs at client construction and
raises if Telethon's ``_call`` is gone or has been reshaped. A tgcli that
cannot govern must refuse to start, never run ungoverned and look fine.
"""

from __future__ import annotations

import inspect

from tgcli.errors import ConfigError

# Telethon 1.44's `UserMethods._call(self, sender, request, ordered=False,
# flood_sleep_threshold=None)`. The wrapper positionally forwards `sender` and
# `request` and needs `request` to identify the type it is governing, so those
# two names and their order are what must hold. Trailing keyword-only extras
# may come and go without breaking us.
REQUIRED_PARAMETERS = ("sender", "request")


def verify_seam(client_class: type) -> None:
    """Raise ``ConfigError`` unless ``_call`` is still the shape we wrap.

    Called at client construction, not at first RPC: a governor that fails
    open on its very first request is indistinguishable from no governor, and
    the incident this ADR answers was caused by exactly that kind of silent
    absence.
    """
    call = getattr(client_class, "_call", None)
    if call is None:
        raise ConfigError(
            "Telethon client has no _call: tgcli cannot govern Telegram "
            "requests (ADR-0072) and refuses to run ungoverned"
        )
    if not inspect.iscoroutinefunction(call):
        raise ConfigError(
            "Telethon _call is not a coroutine function: the request governor "
            "seam (ADR-0072) no longer matches this Telethon version"
        )
    parameters = list(inspect.signature(call).parameters)
    # `self` is present on the unbound function and absent on a bound method;
    # drop it so both forms compare the same.
    if parameters and parameters[0] == "self":
        parameters = parameters[1:]
    if tuple(parameters[: len(REQUIRED_PARAMETERS)]) != REQUIRED_PARAMETERS:
        raise ConfigError(
            "Telethon _call signature changed: expected leading parameters "
            f"{REQUIRED_PARAMETERS}, found {tuple(parameters)!r}. The request "
            "governor seam (ADR-0072) must be re-pinned before tgcli can run"
        )
