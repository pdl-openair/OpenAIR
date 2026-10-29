"""Token usage ledger — append-only per-call token accounting (S03-14-02).

Provider-neutral: every AI call records provider/model as first-class
dimensions; quota math only looks at token counts. Postgres is the durable
store; a JSONL dev fallback mirrors the interface when
``OPENAIR_PERSISTENCE_BACKEND != postgres``.

Write failures must never break the business call — callers wrap
``record_usage`` in try/except (ledger is a satellite system).
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, TypedDict, Union

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings
from llm_core.token_usage import from_wire_usage
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

TokenLedgerBackend = Union["TokenLedgerRepository", "JsonTokenLedgerRepository"]

VALID_SOURCES = (
    "workbench_chat",      # /tasks/{id}/agent/chat[/stream] (inline or container worker)
    "execute_skill",       # /tasks/{id}/execute
    "research_start",      # research-start Planner
    "durable_dispatch",    # durable specialist dispatch (openai_responses / durable skill)
    "structured_skill",    # llm_structured_runner (OpenTrajectory CLI/runtime-guard path)
    "durable_child",       # durable reasoning sub-call under a parent adapter (execute_new_durable_llm_child)
    "durable_skill_agent", # skill_script internal LLM agent loop (skills/shared/durable_llm_agent)
    "durable_adapter_llm", # python_adapter skill-internal LLM (formula-crossover RunPlan/crossover)
)

VALID_KEY_SOURCES = ("platform", "user")


class TokenUsageRecord(TypedDict, total=False):
    ledger_id: str
    user_id: str
    workspace_id: str | None
    task_id: str | None
    call_id: str | None
    source: str
    provider: str | None
    model: str | None
    # platform = OpenAIR-provided keys (enterprise); user = BYOK.
    key_source: str
    status: str  # success | failed | retried
    attempt: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated: bool
    usage_detail: dict[str, Any] | None


_INSERT_SQL = """
INSERT INTO openair_token_usage_ledger (
    ledger_id, user_id, workspace_id, task_id, call_id, source, provider, model, key_source,
    status, attempt, input_tokens, output_tokens, total_tokens, estimated, usage_detail
) VALUES (
    %(ledger_id)s, %(user_id)s, %(workspace_id)s, %(task_id)s, %(call_id)s,
    %(source)s, %(provider)s, %(model)s, %(key_source)s, %(status)s, %(attempt)s,
    %(input_tokens)s, %(output_tokens)s, %(total_tokens)s, %(estimated)s, %(usage_detail)s
)
"""


def _new_ledger_id() -> str:
    return f"led_{uuid.uuid4().hex[:16]}"


def _normalize(record: TokenUsageRecord) -> TokenUsageRecord:
    input_tokens = int(record.get("input_tokens") or 0)
    output_tokens = int(record.get("output_tokens") or 0)
    total = record.get("total_tokens")
    key_source = str(record.get("key_source") or "platform").strip().lower() or "platform"
    if key_source not in VALID_KEY_SOURCES:
        raise ValueError(f"invalid ledger key_source: {key_source!r}")
    normalized = TokenUsageRecord(
        ledger_id=record.get("ledger_id") or _new_ledger_id(),
        user_id=record["user_id"],
        workspace_id=record.get("workspace_id"),
        task_id=record.get("task_id"),
        call_id=record.get("call_id"),
        source=record.get("source") or "workbench_chat",
        provider=record.get("provider"),
        model=record.get("model"),
        key_source=key_source,
        status=record.get("status") or "success",
        attempt=int(record.get("attempt") or 1),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(total) if total is not None else input_tokens + output_tokens,
        estimated=bool(record.get("estimated") or False),
        usage_detail=record.get("usage_detail"),
    )
    if normalized["status"] not in ("success", "failed", "retried"):
        raise ValueError(f"invalid ledger status: {normalized['status']!r}")
    return normalized


class TokenLedgerRepository:
    """Postgres-backed append-only ledger."""

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_usage(self, record: TokenUsageRecord) -> dict[str, Any]:
        entry = _normalize(record)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_SQL,
                    {
                        **entry,
                        "usage_detail": Jsonb(entry["usage_detail"]) if entry.get("usage_detail") else None,
                    },
                )
        return dict(entry)

    def list_usage(
        self,
        user_id: str,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List a user's ledger entries, newest first.

        Cursor format: ``"<created_at_iso>|<ledger_id>"`` of the last seen row.
        """
        cursor_ts: datetime | None = None
        cursor_id: str | None = None
        if cursor:
            cursor_ts_raw, _, cursor_id = cursor.partition("|")
            try:
                cursor_ts = datetime.fromisoformat(cursor_ts_raw.replace("Z", "+00:00"))
            except ValueError:
                cursor_ts, cursor_id = None, None

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_token_usage_ledger
                     WHERE user_id = %(user_id)s
                       AND (%(from_ts)s::timestamptz IS NULL OR created_at >= %(from_ts)s)
                       AND (%(to_ts)s::timestamptz IS NULL OR created_at <= %(to_ts)s)
                       AND (
                            %(cursor_ts)s::timestamptz IS NULL
                            OR (created_at, ledger_id) < (%(cursor_ts)s, %(cursor_id)s)
                       )
                     ORDER BY created_at DESC, ledger_id DESC
                     LIMIT %(limit)s
                    """,
                    {
                        "user_id": user_id,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                        "cursor_ts": cursor_ts,
                        "cursor_id": cursor_id,
                        "limit": limit + 1,
                    },
                )
                rows = cur.fetchall()
        return _page(rows, limit)

    def sum_usage(self, user_id: str, *, from_ts: datetime | None = None, to_ts: datetime | None = None) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(total_tokens), 0) AS total
                      FROM openair_token_usage_ledger
                     WHERE user_id = %(user_id)s
                       AND (%(from_ts)s::timestamptz IS NULL OR created_at >= %(from_ts)s)
                       AND (%(to_ts)s::timestamptz IS NULL OR created_at <= %(to_ts)s)
                    """,
                    {"user_id": user_id, "from_ts": from_ts, "to_ts": to_ts},
                )
                row = cur.fetchone()
        return int(row["total"]) if row else 0

    def sum_task_usage(self, user_id: str, task_id: str, *, workspace_id: str | None = None) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(total_tokens), 0) AS total
                      FROM openair_token_usage_ledger
                     WHERE user_id = %(user_id)s
                       AND task_id = %(task_id)s
                       AND (%(workspace_id)s::text IS NULL OR workspace_id::text = %(workspace_id)s::text)
                    """,
                    {"user_id": user_id, "task_id": task_id, "workspace_id": workspace_id},
                )
                row = cur.fetchone()
        return int(row["total"]) if row else 0

    def summary_by_source(self, user_id: str, *, from_ts: datetime | None = None) -> list[dict[str, Any]]:
        """Aggregate tokens grouped by source (admin consumption summary)."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source,
                           COUNT(*) AS calls,
                           COALESCE(SUM(input_tokens), 0) AS input_tokens,
                           COALESCE(SUM(output_tokens), 0) AS output_tokens,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens
                      FROM openair_token_usage_ledger
                     WHERE user_id = %(user_id)s
                       AND (%(from_ts)s::timestamptz IS NULL OR created_at >= %(from_ts)s)
                     GROUP BY source
                     ORDER BY total_tokens DESC
                    """,
                    {"user_id": user_id, "from_ts": from_ts},
                )
                return cur.fetchall()

    def summary_by_model(self, user_id: str, *, from_ts: datetime | None = None) -> list[dict[str, Any]]:
        """Aggregate tokens grouped by model/provider/key_source (admin detail)."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(model, '') AS model,
                           COALESCE(provider, '') AS provider,
                           COALESCE(key_source, 'platform') AS key_source,
                           COUNT(*) AS calls,
                           COALESCE(SUM(input_tokens), 0) AS input_tokens,
                           COALESCE(SUM(output_tokens), 0) AS output_tokens,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens
                      FROM openair_token_usage_ledger
                     WHERE user_id = %(user_id)s
                       AND (%(from_ts)s::timestamptz IS NULL OR created_at >= %(from_ts)s)
                     GROUP BY 1, 2, 3
                     ORDER BY total_tokens DESC
                    """,
                    {"user_id": user_id, "from_ts": from_ts},
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "model": str(row.get("model") or "") or None,
                    "provider": str(row.get("provider") or "") or None,
                    "key_source": str(row.get("key_source") or "platform"),
                    "calls": int(row.get("calls") or 0),
                    "input_tokens": int(row.get("input_tokens") or 0),
                    "output_tokens": int(row.get("output_tokens") or 0),
                    "total_tokens": int(row.get("total_tokens") or 0),
                }
            )
        return out

    def timeseries(
        self,
        user_id: str,
        *,
        granularity: str,
        from_ts: datetime,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Bucketed token totals for peak/valley overview (UTC)."""
        trunc = {"day": "day", "week": "week", "month": "month"}.get(granularity)
        if trunc is None:
            raise ValueError(f"invalid timeseries granularity: {granularity!r}")
        to_ts = to_ts or datetime.now(timezone.utc)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT date_trunc(%(gran)s, created_at AT TIME ZONE 'UTC') AS period_start,
                           COUNT(*) AS calls,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens
                      FROM openair_token_usage_ledger
                     WHERE user_id = %(user_id)s
                       AND created_at >= %(from_ts)s
                       AND created_at <= %(to_ts)s
                     GROUP BY 1
                     ORDER BY 1 ASC
                    """,
                    {
                        "gran": trunc,
                        "user_id": user_id,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                    },
                )
                rows = cur.fetchall()
        return [
            {
                "period_start": row["period_start"],
                "calls": int(row.get("calls") or 0),
                "total_tokens": int(row.get("total_tokens") or 0),
            }
            for row in rows
        ]


class JsonTokenLedgerRepository:
    """JSONL dev fallback: one append-only file per user under data/ledger/."""

    def __init__(self, ledger_dir: Path) -> None:
        self._dir = ledger_dir

    def bootstrap(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, user_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in user_id)
        return self._dir / f"{safe}.jsonl"

    def record_usage(self, record: TokenUsageRecord) -> dict[str, Any]:
        entry = dict(_normalize(record))
        entry["created_at"] = datetime.now(timezone.utc).isoformat()
        path = self._path_for(entry["user_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def _read_all(self, user_id: str) -> list[dict[str, Any]]:
        path = self._path_for(user_id)
        if not path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping corrupt ledger line in %s", path)
                continue
            if isinstance(row, dict):
                entries.append(row)
        return entries

    def list_usage(
        self,
        user_id: str,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        entries = self._read_all(user_id)
        entries = [e for e in entries if _in_range(e.get("created_at"), from_ts, to_ts)]
        entries.sort(key=lambda e: (str(e.get("created_at") or ""), str(e.get("ledger_id") or "")), reverse=True)
        if cursor:
            cursor_ts_raw, _, cursor_id = cursor.partition("|")
            entries = [
                e
                for e in entries
                if (str(e.get("created_at") or ""), str(e.get("ledger_id") or "")) < (cursor_ts_raw, cursor_id)
            ]
        return _page(entries, limit)

    def sum_usage(self, user_id: str, *, from_ts: datetime | None = None, to_ts: datetime | None = None) -> int:
        return sum(
            int(e.get("total_tokens") or 0)
            for e in self._read_all(user_id)
            if _in_range(e.get("created_at"), from_ts, to_ts)
        )

    def sum_task_usage(self, user_id: str, task_id: str, *, workspace_id: str | None = None) -> int:
        return sum(
            int(e.get("total_tokens") or 0)
            for e in self._read_all(user_id)
            if e.get("task_id") == task_id
            and (workspace_id is None or e.get("workspace_id") == workspace_id)
        )

    def summary_by_source(self, user_id: str, *, from_ts: datetime | None = None) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for e in self._read_all(user_id):
            if not _in_range(e.get("created_at"), from_ts, None):
                continue
            source = str(e.get("source") or "unknown")
            bucket = buckets.setdefault(
                source, {"source": source, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += int(e.get("input_tokens") or 0)
            bucket["output_tokens"] += int(e.get("output_tokens") or 0)
            bucket["total_tokens"] += int(e.get("total_tokens") or 0)
        return sorted(buckets.values(), key=lambda b: b["total_tokens"], reverse=True)

    def summary_by_model(self, user_id: str, *, from_ts: datetime | None = None) -> list[dict[str, Any]]:
        buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
        for e in self._read_all(user_id):
            if not _in_range(e.get("created_at"), from_ts, None):
                continue
            model = str(e.get("model") or "")
            provider = str(e.get("provider") or "")
            key_source = str(e.get("key_source") or "platform")
            key = (model, provider, key_source)
            bucket = buckets.setdefault(
                key,
                {
                    "model": model or None,
                    "provider": provider or None,
                    "key_source": key_source,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += int(e.get("input_tokens") or 0)
            bucket["output_tokens"] += int(e.get("output_tokens") or 0)
            bucket["total_tokens"] += int(e.get("total_tokens") or 0)
        return sorted(buckets.values(), key=lambda b: b["total_tokens"], reverse=True)

    def timeseries(
        self,
        user_id: str,
        *,
        granularity: str,
        from_ts: datetime,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        to_ts = to_ts or datetime.now(timezone.utc)
        buckets: dict[datetime, dict[str, Any]] = {}
        for e in self._read_all(user_id):
            if not _in_range(e.get("created_at"), from_ts, to_ts):
                continue
            created = e.get("created_at")
            if isinstance(created, str):
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif isinstance(created, datetime):
                created_dt = created
            else:
                continue
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            period = _bucket_start(created_dt.astimezone(timezone.utc), granularity)
            bucket = buckets.setdefault(period, {"period_start": period, "calls": 0, "total_tokens": 0})
            bucket["calls"] += 1
            bucket["total_tokens"] += int(e.get("total_tokens") or 0)
        return [buckets[k] for k in sorted(buckets.keys())]


def _bucket_start(ts: datetime, granularity: str) -> datetime:
    utc = ts.astimezone(timezone.utc)
    if granularity == "day":
        return utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "week":
        day = utc.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())  # Monday UTC
    if granularity == "month":
        return utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"invalid timeseries granularity: {granularity!r}")


def build_timeseries_bundle(
    *,
    granularity: str,
    raw_points: list[dict[str, Any]],
    from_ts: datetime,
    to_ts: datetime | None = None,
) -> dict[str, Any]:
    """Fill continuous buckets and compute peak / trough / average."""
    to_ts = to_ts or datetime.now(timezone.utc)
    from_ts = from_ts.astimezone(timezone.utc)
    to_ts = to_ts.astimezone(timezone.utc)
    by_start: dict[str, dict[str, Any]] = {}
    for row in raw_points:
        start = row.get("period_start")
        if isinstance(start, datetime):
            start_dt = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
            start_dt = start_dt.astimezone(timezone.utc)
        elif isinstance(start, str):
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            else:
                start_dt = start_dt.astimezone(timezone.utc)
        else:
            continue
        start_dt = _bucket_start(start_dt, granularity)
        key = start_dt.isoformat()
        by_start[key] = {
            "period_start": start_dt,
            "calls": int(row.get("calls") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
        }

    points: list[dict[str, Any]] = []
    cursor = _bucket_start(from_ts, granularity)
    end = _bucket_start(to_ts, granularity)
    while cursor <= end:
        key = cursor.isoformat()
        hit = by_start.get(key)
        tokens = int(hit["total_tokens"]) if hit else 0
        calls = int(hit["calls"]) if hit else 0
        points.append(
            {
                "periodStart": key,
                "periodLabel": _period_label(cursor, granularity),
                "calls": calls,
                "totalTokens": tokens,
            }
        )
        if granularity == "day":
            cursor = cursor + timedelta(days=1)
        elif granularity == "week":
            cursor = cursor + timedelta(days=7)
        else:
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)

    peak_tokens = 0
    peak_period: str | None = None
    trough_tokens = 0
    trough_period: str | None = None
    active = [p for p in points if p["calls"] > 0]
    if active:
        peak = max(active, key=lambda p: p["totalTokens"])
        trough = min(active, key=lambda p: p["totalTokens"])
        peak_tokens = int(peak["totalTokens"])
        peak_period = str(peak["periodLabel"])
        trough_tokens = int(trough["totalTokens"])
        trough_period = str(trough["periodLabel"])
    avg = (sum(p["totalTokens"] for p in points) / len(points)) if points else 0.0
    return {
        "granularity": granularity,
        "points": points,
        "peakTokens": peak_tokens,
        "peakPeriod": peak_period,
        "troughTokens": trough_tokens,
        "troughPeriod": trough_period,
        "avgTokens": round(avg, 2),
    }


def _period_label(start: datetime, granularity: str) -> str:
    if granularity == "day":
        return start.strftime("%Y-%m-%d")
    if granularity == "week":
        end = start + timedelta(days=6)
        return f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%m-%d')}"
    return start.strftime("%Y-%m")


def _in_range(created_at: Any, from_ts: datetime | None, to_ts: datetime | None) -> bool:
    if from_ts is None and to_ts is None:
        return True
    if created_at is None:
        return False
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if from_ts is not None and created_at < from_ts:
        return False
    if to_ts is not None and created_at > to_ts:
        return False
    return True


def _page(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], str | None]:
    """Slice limit+1 rows into a page + next cursor."""
    page = rows[:limit]
    next_cursor: str | None = None
    if len(rows) > limit and page:
        last = page[-1]
        created = last.get("created_at")
        created_iso = created.isoformat() if isinstance(created, datetime) else str(created or "")
        next_cursor = f"{created_iso}|{last.get('ledger_id')}"
    return page, next_cursor


def get_token_ledger_repository() -> TokenLedgerBackend:
    """Return the ledger backend for the active persistence mode."""
    if settings.persistence_backend == "postgres":
        return TokenLedgerRepository()
    if settings.is_production:
        raise RuntimeError(
            "JSON token ledger is not supported in production; "
            "set OPENAIR_PERSISTENCE_BACKEND=postgres"
        )
    repo = JsonTokenLedgerRepository(ledger_dir=repo_root() / "data" / "ledger")
    repo.bootstrap()
    return repo


def record_usage_safe(record: TokenUsageRecord) -> dict[str, Any] | None:
    """Best-effort ledger write — never breaks the business call (S03-14-02 §6.3)."""
    try:
        return get_token_ledger_repository().record_usage(record)
    except Exception:
        logger.exception(
            "token ledger write failed (user=%s source=%s total=%s)",
            record.get("user_id"),
            record.get("source"),
            record.get("total_tokens"),
        )
        return None


def record_call_usage(
    *,
    user_id: str,
    source: str,
    usage_wire: dict[str, Any] | None,
    workspace_id: str | None = None,
    task_id: str | None = None,
    call_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    key_source: str = "platform",
    status: str = "success",
    attempt: int = 1,
) -> dict[str, Any] | None:
    """Convenience: record a ledger entry from a wire-usage payload.

    Maps the failure/estimation rules from S03-14-01 §5: a None/zero usage on a
    failed call records a 0-token ``failed`` row (no deduction); a real usage on
    a failed call is still recorded (and deducted) so every token is accountable.
    Returns the recorded entry or None if usage was absent AND status is success
    (nothing to bookkeep) or the write failed.
    """
    if not user_id:
        return None
    usage = from_wire_usage(usage_wire) if usage_wire else None
    if usage is None:
        if status == "success":
            # Successful call with no observable usage (e.g. replay/mock): nothing to bookkeep.
            return None
        # Failed call before any usage: record a 0-token row for traceability.
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated": False, "raw": None}

    return record_usage_safe(
        TokenUsageRecord(  # type: ignore[typeddict-item]
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            call_id=call_id,
            source=source,
            provider=provider,
            model=model,
            key_source=key_source,
            status=status,
            attempt=attempt,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            estimated=bool(usage.get("estimated")),
            usage_detail=usage.get("raw") if isinstance(usage.get("raw"), dict) else None,
        )
    )


def meter_call(
    *,
    user_id: str,
    source: str,
    usage_wire: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    task_id: str | None = None,
    call_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    key_source: str = "platform",
    status: str = "success",
) -> None:
    """The single metering mechanism (U-04): record one call to the ledger AND
    deduct quota, paired.

    Collapses the ``record_call_usage`` + ``settle`` boilerplate that was
    duplicated across the per-site write points (agent_service, research_start,
    durable_skill_runtime, research_dispatch_worker). Deduction follows
    S03-14-01 §5 / ``record_call_usage``: whenever real usage was observed,
    settle those tokens (including business-failed calls that still burned
    provider tokens). Failed calls with no observable usage still get a
    0-token traceability row via ``record_call_usage``.

    Best-effort — never raises into the business path.
    """
    if not user_id:
        return
    try:
        record_call_usage(
            user_id=user_id,
            source=source,
            usage_wire=usage_wire,
            workspace_id=workspace_id,
            task_id=task_id,
            call_id=call_id,
            provider=provider,
            model=model,
            key_source=key_source,
            status=status,
        )
        if usage_wire:
            from llm_core.token_usage import from_wire_usage
            from app.modules.quota_service import get_quota_service

            normalized = from_wire_usage(usage_wire)
            if normalized is not None and int(normalized["total_tokens"]) > 0:
                get_quota_service().settle(user_id, int(normalized["total_tokens"]))
    except Exception:  # noqa: BLE001 — ledger/quota are satellite systems
        logger.warning("meter_call failed user=%s source=%s", user_id, source, exc_info=True)


def flush_observed_usage(
    *,
    user_id: str,
    source: str,
    bucket: list[dict[str, Any]],
    workspace_id: str | None = None,
    task_id: str | None = None,
) -> None:
    """Flush a ``begin_usage_collection`` bucket through ``meter_call`` (U-04)."""
    if not user_id or not bucket:
        return
    for item in bucket:
        if not isinstance(item, dict):
            continue
        meter_call(
            user_id=user_id,
            source=source,
            usage_wire=item.get("usage") if isinstance(item.get("usage"), dict) else None,
            workspace_id=workspace_id,
            task_id=task_id,
            provider=item.get("provider") if isinstance(item.get("provider"), str) else None,
            model=item.get("model") if isinstance(item.get("model"), str) else None,
            status="success",
        )
