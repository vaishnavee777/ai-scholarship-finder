"""Runtime configuration, read from environment variables (and .env)."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: int = 60
    mcp_command: str = "npx"
    mcp_args: tuple[str, ...] = ("-y", "@playwright/mcp@0.0.83", "--headless", "--isolated")
    mcp_output_dir: Path = PROJECT_ROOT / "data" / "mcp_output"
    db_path: Path = PROJECT_ROOT / "data" / "scholarships.db"
    max_pages_per_search: int = 8
    max_follow_links_per_page: int = 3
    max_search_queries: int = 4
    page_timeout_seconds: int = 25
    max_page_chars: int = 30_000
    max_retries: int = 2
    cache_ttl_hours: int = 24
    gemini_cache_ttl_hours: int = 24 * 7
    freshness_days: int = 14  # evidence older than this cannot support "application open"
    stale_source_days: int = 180  # eligibility sources older than this are UNVERIFIED
    trusted_provider_domains: tuple[str, ...] = ()
    extra_seed_urls: tuple[str, ...] = ()
    respect_robots: bool = True

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    args = os.getenv("MCP_SERVER_ARGS")
    defaults = Settings()
    db_path = Path(os.getenv("DB_PATH", str(defaults.db_path)))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    trusted = tuple(
        d.strip().lower() for d in os.getenv("TRUSTED_PROVIDER_DOMAINS", "").split(",") if d.strip()
    )
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", defaults.gemini_model),
        mcp_command=os.getenv("MCP_SERVER_COMMAND", defaults.mcp_command),
        mcp_args=tuple(shlex.split(args)) if args else defaults.mcp_args,
        db_path=db_path,
        max_pages_per_search=_int("MAX_PAGES_PER_SEARCH", defaults.max_pages_per_search),
        max_follow_links_per_page=_int("MAX_FOLLOW_LINKS_PER_PAGE", defaults.max_follow_links_per_page),
        page_timeout_seconds=_int("PAGE_TIMEOUT_SECONDS", defaults.page_timeout_seconds),
        cache_ttl_hours=_int("CACHE_TTL_HOURS", defaults.cache_ttl_hours),
        freshness_days=_int("FRESHNESS_DAYS", defaults.freshness_days),
        trusted_provider_domains=trusted,
        extra_seed_urls=tuple(u.strip() for u in os.getenv("EXTRA_SEED_URLS", "").split(",") if u.strip()),
    )
