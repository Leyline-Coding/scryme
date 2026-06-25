"""Normalized import row, the importer interface, and the format-detection registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import ClassVar, Protocol, runtime_checkable


class UnknownFormatError(ValueError):
    """Raised when no registered importer recognizes an uploaded file."""


@dataclass
class ImportRow:
    """One stack of cards from an export, normalized across source formats."""

    name: str
    quantity: int = 1
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    finish: str = "normal"  # normal | foil | etched
    condition: str | None = None
    language: str = "en"
    purchase_price: float | None = None
    binder_name: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ImportRow:
        return cls(**data)


@runtime_checkable
class Importer(Protocol):
    """A source-format parser. ``detect`` sniffs the file; ``parse`` yields ImportRows."""

    format_name: ClassVar[str]

    @classmethod
    def detect(cls, text: str) -> bool: ...

    @classmethod
    def parse(cls, text: str) -> list[ImportRow]: ...


# Registered parsers, checked in order. Populated by importer modules at import time.
_REGISTRY: list[type[Importer]] = []


def register(importer: type[Importer]) -> type[Importer]:
    _REGISTRY.append(importer)
    return importer


def detect_format(text: str) -> type[Importer] | None:
    """Return the first registered importer that recognizes ``text`` (or None)."""
    for importer in _REGISTRY:
        if importer.detect(text):
            return importer
    return None
