"""Serialize one command's execution and receipt processing without new lifecycle state."""

from contextlib import contextmanager
from hashlib import sha256
from typing import Any


@contextmanager
def claim_internal_processing(repository: Any, call_id: str):
    """Use a connection-scoped PostgreSQL advisory lock, never a long Task transaction.

    Repository._connect owns and closes a fresh connection. Its closure releases
    the session lock, including after exceptions or process loss. Contention is
    pending feedback; it grants no submission or duplicate recovery authority.
    Physical I/O still requires the original Dispatch fence at each boundary.
    """
    key = int.from_bytes(
        sha256(f"openair:internal-call-processing:{call_id}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s) AS claimed", (key,))
            claimed = cursor.fetchone()["claimed"] is True
        # No transaction is held across Skill/Provider or artifact storage I/O.
        connection.commit()
        yield claimed
