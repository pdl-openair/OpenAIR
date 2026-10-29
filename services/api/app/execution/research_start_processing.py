"""Failure ownership for ResearchStart; no retries or persistence policy here."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import PurePath
from typing import Any


class PlannerSemanticInputError(ValueError):
    """The Provider response cannot be assembled into executable planning input."""

    processing_stage = "planner_response"

    def __init__(self, message: str, *, error_code: str, field_paths: tuple[str, ...]) -> None:
        super().__init__(message)
        self.safe_error_code = error_code
        self.safe_field_paths = field_paths
        self.safe_repair_context: dict[str, Any] = {}


class PlannerProcessingError(RuntimeError):
    """Local processing failed; another model submission cannot repair this fact."""

    failure_code = "planner_processing_failed"

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__("Research planning could not complete local processing")
        locations: list[dict[str, Any]] = []
        trace = cause.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            locations.append({
                "file": PurePath(code.co_filename).name,
                "function": code.co_name,
                "line": trace.tb_lineno,
            })
            trace = trace.tb_next
        self.safe_processing_diagnostic = {
            "stage": stage,
            "errorType": type(cause).__name__,
            "locations": locations[-8:],
        }
        # Do not persist arbitrary exception messages or local variables: these
        # can contain credentials, input data, or an entire Pydantic payload.
        code = getattr(cause, "safe_error_code", None)
        if isinstance(code, str) and code:
            self.safe_processing_diagnostic["errorCode"] = code[:160]


@contextmanager
def local_planning_step(stage: str) -> Iterator[None]:
    """Keep classified Provider-response failures separate from implementation faults."""
    try:
        yield
    except (PlannerSemanticInputError, PlannerProcessingError):
        raise
    except Exception as exc:
        raise PlannerProcessingError(stage, exc) from None


ResponseObserver = Callable[[Any, bytes, Mapping[str, Any]], None]
_response_observer: ContextVar[ResponseObserver | None] = ContextVar(
    "research_start_response_observer", default=None,
)


@contextmanager
def observe_planning_response(observer: ResponseObserver) -> Iterator[None]:
    """Scope one already-fenced attempt's evidence sink without global callbacks."""
    token = _response_observer.set(observer)
    try:
        yield
    finally:
        _response_observer.reset(token)


def record_planning_response(prepared: Any, raw: bytes, metadata: Mapping[str, Any]) -> None:
    observer = _response_observer.get()
    if observer is not None and prepared.phase == "research_planner":
        observer(prepared, raw, metadata)


def read_provider_content(extract: Callable[[dict[str, Any]], Any], body: Mapping[str, Any]) -> Any:
    """Classify only the existing final-content extractor's declared errors.

    These extractors report absent, refused or reasoning-only output through
    ValueError/RuntimeError. No scientific validation or local assembly runs
    inside this boundary; unrelated programming errors remain local failures.
    """
    try:
        return extract(dict(body))
    except (ValueError, RuntimeError):
        error = PlannerSemanticInputError(
            "Provider did not return usable final content",
            error_code="provider_final_content_unavailable", field_paths=("/",),
        )
        error.processing_stage = "provider_content"
        raise error from None
