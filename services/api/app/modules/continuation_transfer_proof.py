"""ContinuationTransferProof — single authority for continuation responsibility transfer.

Whenever ``openair_dispatch_continuation`` is retained or completed, the service
must name one ``ContinuationResponsibilityProof`` and the repository must
re-validate that proof in the same CAS transaction.  This module is the public
contract alias; implementation lives in ``continuation_responsibility`` and the
research dispatch/repository boundaries.
"""

from __future__ import annotations

from app.modules.continuation_responsibility import (
    ContinuationOwnerKind,
    ContinuationOwnerRef,
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
)

ContinuationTransferProof = ContinuationResponsibilityProof
ContinuationTransferKind = ContinuationResponsibilityKind

__all__ = [
    "ContinuationOwnerKind",
    "ContinuationOwnerRef",
    "ContinuationTransferKind",
    "ContinuationTransferProof",
    "ContinuationResponsibilityKind",
    "ContinuationResponsibilityProof",
]
