"""API call log + optional self-imposed daily limits (cost control)."""
from __future__ import annotations

from datetime import date

from . import db
from .config import DAILY_LIMITS


def log(provider: str, kind: str, success: bool, note: str = "", cost: float = 0.0,
        tokens_in: int = 0, tokens_out: int = 0) -> None:
    db.insert("api_usage", provider=provider, kind=kind, success=int(success),
              note=(note or "")[:500], cost=cost, tokens_in=tokens_in, tokens_out=tokens_out, created_at=db.now())


def count_today(provider: str) -> int:
    row = db.one("SELECT COUNT(*) AS n FROM api_usage WHERE provider=? AND created_at LIKE ?",
                 (provider, f"{date.today().isoformat()}%"))
    return int(row["n"]) if row else 0


def within_limit(provider: str) -> bool:
    """True if we may call the provider. Only enforces limits the user configured in .env."""
    limit = DAILY_LIMITS.get(provider, 0)
    return limit <= 0 or count_today(provider) < limit


def summary_today() -> list[dict]:
    return db.query(
        "SELECT provider, COUNT(*) AS calls, SUM(success) AS ok, SUM(cost) AS cost, "
        "SUM(tokens_in) AS tin, SUM(tokens_out) AS tout "
        "FROM api_usage WHERE created_at LIKE ? GROUP BY provider ORDER BY provider",
        (f"{date.today().isoformat()}%",))


def summary_all() -> list[dict]:
    return db.query(
        "SELECT provider, COUNT(*) AS calls, SUM(success) AS ok, SUM(cost) AS cost, "
        "SUM(tokens_in) AS tin, SUM(tokens_out) AS tout "
        "FROM api_usage GROUP BY provider ORDER BY provider")


def recent(limit: int = 30) -> list[dict]:
    return db.query("SELECT * FROM api_usage ORDER BY id DESC LIMIT ?", (limit,))
