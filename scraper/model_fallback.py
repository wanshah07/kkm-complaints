"""
Automatic model fallback (Wan, 26 Nov 2026: "for model API use in the system, make the system change model fallback auto if
current model is off").

Until now each provider had ONE model. When it was retired, overloaded or refused for lack of access, every review failed and
`evaluator.review()` fell back to the regex rulebook, so a sheet could fill with rules-only rows. A chain of models fixes that:
the configured model first, then the fallbacks in order. A model that is "off" is skipped, for a while, so a dead model costs one
failed call, not one per post, and the first model is tried again once its cool-down is over (it may simply have come back).

What counts as "off" (is_off):
  permanent  404 / model not found / does not exist / deprecated / retired / 403 no access to the model
             -> skipped for the rest of the process (6 hours)
  transient  429, 408, 5xx, 529 overloaded, connection errors, timeouts
             -> skipped for 5 minutes
  neither    401 (a bad key is the same for every model), 400 about the request itself, a bad reply: raised as before, because
             another model would fail the same way and the caller already handles it.

The chain is a comma list in an environment variable, so it is a repository variable, not a code change.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

log = logging.getLogger("kkm.models")

T = TypeVar("T")

PERMANENT_SECONDS = 6 * 3600
TRANSIENT_SECONDS = 300

_NOT_FOUND = re.compile(
    r"model[_ ]?not[_ ]?found|model .{0,60}(does not exist|not found|not available|is not supported|has been (deprecated|retired))"
    r"|(unknown|invalid|unsupported) model|no such model|deprecated|retired|decommission|no available (channel|provider)"
    r"|do(es)? not have access to .{0,40}model|not (authorized|allowed) .{0,30}model", re.I)
_OVERLOAD = re.compile(r"overloaded|over capacity|temporarily unavailable|service unavailable|try again later|rate.?limit"
                       r"|too many requests|capacity|upstream", re.I)
_TRANSIENT_CLASSES = ("apiconnectionerror", "apitimeouterror", "timeout", "connecterror", "readtimeout", "connectionerror",
                      "remoteprotocolerror", "internalservererror", "serviceunavailable", "overloadederror", "ratelimiterror")


def chain(primary: str, fallbacks: str = "", defaults: Tuple[str, ...] = ()) -> List[str]:
    """[primary, fallback, ...] with blanks and repeats dropped. `fallbacks` (a comma or space list) wins over `defaults`."""
    raw = [p for p in re.split(r"[,\s;]+", fallbacks or "") if p]
    out: List[str] = []
    for m in [primary] + (raw if raw else list(defaults)):
        m = (m or "").strip()
        if m and m not in out:
            out.append(m)
    return out


def is_off(exc: BaseException) -> Optional[str]:
    """'permanent', 'transient' or None for the exception a provider SDK raised."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    msg = str(exc or "")
    cls = type(exc).__name__.lower()
    if status == 401:
        return None
    if status in (404, 410):
        return "permanent"
    if status == 403:
        return "permanent" if _NOT_FOUND.search(msg) or "model" in msg.lower() else None
    if status == 400 or status == 422:
        return "permanent" if _NOT_FOUND.search(msg) else None
    if status in (408, 409, 425, 429, 500, 502, 503, 504, 529) or (isinstance(status, int) and status >= 500):
        return "permanent" if _NOT_FOUND.search(msg) and status != 429 else "transient"
    if any(c in cls for c in _TRANSIENT_CLASSES):
        return "transient"
    if status is None:
        if _NOT_FOUND.search(msg):
            return "permanent"
        if _OVERLOAD.search(msg):
            return "transient"
    return None


class Board:
    """Which models are off right now. One per process, keyed by provider + model."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.off_until: Dict[str, float] = {}
        self.why: Dict[str, str] = {}

    def mark(self, key: str, kind: str, why: str) -> None:
        secs = PERMANENT_SECONDS if kind == "permanent" else TRANSIENT_SECONDS
        self.off_until[key] = self.clock() + secs
        self.why[key] = why[:160]

    def is_off(self, key: str) -> bool:
        return self.off_until.get(key, 0) > self.clock()

    def order(self, keys: List[str]) -> List[str]:
        """The keys to try, in order: the ones that are on. If every one is off, only the first to come back, once."""
        live = [k for k in keys if not self.is_off(k)]
        if live:
            return live
        return [min(keys, key=lambda k: self.off_until.get(k, 0))] if keys else []


BOARD = Board()


def run(provider: str, models: List[str], call: Callable[[str], T], board: Optional[Board] = None) -> T:
    """
    call(model) for each model in turn until one answers. A model that is off is marked and the next one is tried; any other
    exception is raised at once. If every model is off the last off-error is raised, so the caller sees a provider failure
    exactly as it did before there was a chain.
    """
    board = board or BOARD
    models = [m for m in models if m]
    if not models:
        raise RuntimeError(f"{provider}: no model configured")
    first = models[0]
    last: Optional[BaseException] = None
    for m in board.order([f"{provider}|{x}" for x in models]):
        model = m.split("|", 1)[1]
        try:
            result = call(model)
        except Exception as e:
            kind = is_off(e)
            if not kind:
                raise
            board.mark(m, kind, str(e))
            log.warning("model %s on %s is off (%s): %s - trying the next model", model, provider, kind, str(e)[:140])
            last = e
            continue
        if model != first:
            log.warning("model %s answered instead of %s on %s (%s)", model, first, provider,
                        board.why.get(f"{provider}|{first}", "off"))
        return result
    assert last is not None
    raise last


def served_note(asked: str, served: str) -> str:
    return "" if asked == served else f"model fallback: asked {asked}, served by {served}"
