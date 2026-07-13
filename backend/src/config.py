"""Application settings, loaded from environment variables.

Single-user deployment: there is one implicit collection per running instance, so
there is no authentication or per-user configuration here.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRYME_", env_file=".env", extra="ignore")

    environment: str = "development"
    debug: bool = False

    # PostgreSQL (async driver). Set SCRYME_DATABASE_URL directly, or provide the parts below and
    # the URL is assembled with the password URL-encoded (so passwords may contain @ : / etc.).
    database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "scryme"
    db_password: str = "scryme"
    db_name: str = "scryme"

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        if not self.database_url:
            password = quote(self.db_password, safe="")
            self.database_url = (
                f"postgresql+asyncpg://{self.db_user}:{password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        return self

    # Where downloaded Scryfall bulk files and cached card images live on disk.
    data_dir: Path = Path("/data")
    image_cache_dir: Path = Path("/data/images")

    # Scryfall good-citizenship settings (see https://scryfall.com/docs/api).
    # User-Agent MUST identify this app; Accept MUST be set. Keep < 10 req/s.
    scryfall_api_base: str = "https://api.scryfall.com"
    scryfall_user_agent: str = f"scryme/{__version__} (+https://github.com/Leyline-Coding/scryme)"
    scryfall_accept: str = "application/json;q=0.9,*/*;q=0.8"
    scryfall_min_request_interval: float = 0.1  # seconds between requests (<= 10/s)
    # Don't re-download bulk data more often than this many hours (Scryfall asks >= 24h cache).
    bulk_refresh_min_hours: int = 24

    # Read-only demo mode disables uploads/mutations (used by the public sandbox).
    read_only: bool = False

    # Default display currency for current-value prices (usd | eur). Per-visitor override via the
    # `scryme_currency` cookie set from the currency picker.
    default_currency: str = "usd"

    # On-disk backups. Point `backup_dir` at a folder (e.g. a Dropbox/Drive-synced one) to enable
    # "Back up now", scheduled backups, and restore from disk. `backup_keep` bounds retention;
    # `backup_interval_hours` > 0 enables a scheduled backup (0 disables it).
    backup_dir: Path | None = None
    backup_keep: int = 14
    backup_interval_hours: int = 0
    # If set, on-disk/scheduled backups are encrypted with this passphrase (restore needs it too).
    backup_passphrase: str = ""

    # JSON API: when set, every /api/* request must send this token (Authorization: Bearer <token>
    # or X-API-Key). Empty = open (fine for a single-user localhost instance).
    api_token: str = ""

    # Desktop LAN sharing. When true (the desktop app sets SCRYME_LAN_GUARD=1 and binds 0.0.0.0),
    # non-loopback requests are blocked unless LAN sharing is enabled — and then optionally gated by
    # an access code. Off by default, so the Docker/self-host deployment is unaffected.
    lan_guard: bool = False

    # AI features: point at any OpenAI-API-compatible endpoint — OpenAI, OpenRouter, or a local
    # Ollama / LM Studio server (e.g. http://localhost:11434/v1). Empty base URL disables AI. The
    # embed model is used for semantic card similarity (#176). #163 layers in-app config on top.
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_chat_model: str = "llama3.1"
    llm_embed_model: str = "nomic-embed-text"


@lru_cache
def get_settings() -> Settings:
    return Settings()
