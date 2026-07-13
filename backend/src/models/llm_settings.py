"""In-app LLM configuration (#163).

A single row (id == 1) holding the OpenAI-compatible endpoint the AI features use. The API key is
stored **encrypted** at rest (Fernet, key in the data dir — see ``src.llm``); the rest is plain.
When no row exists, config falls back to the ``SCRYME_LLM_*`` environment variables.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class LLMSettings(Base):
    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    base_url: Mapped[str] = mapped_column(String(512), default="", server_default="")
    api_key_enc: Mapped[str | None] = mapped_column(Text)  # Fernet token, or None
    chat_model: Mapped[str] = mapped_column(String(128), default="", server_default="")
    embed_model: Mapped[str] = mapped_column(String(128), default="", server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
