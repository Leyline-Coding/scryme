"""The `collection_card` table: the single (implicit) user's owned cards.

Each row is a distinct stack of a printing distinguished by finish/condition/language/binder,
so merge strategies can increment quantities or replace stacks deterministically.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class Stack():
    scryfall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cards.scryfall_id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    finish: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")  # normal | foil | etched
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")


    def get_unique_stack_key(self) -> str:
        """ Currently, scryfall_id is not a unique identifier of a card print. It captures the
        oracle id the set name and collector's number--so the general card design--but it does
        not capture foil, etched, or language variants.
        
        This function generates, consistently, a unique string that truely uniquely identifies all the
        caharchteristics of a card printing.
        
        The primary use case for this is for exact card matching across stacks (collecotr_card, deck_card)
        """
        return Stack._get_unique_stack_key(self.scryfall_id,self.finish,self.language)
    
    @classmethod
    def _get_unique_stack_key(cls, scryfall_id: uuid.UUID | str, finish: str, language: str) -> str:
        """ Can be invoked directly to build the stack manually outside of an explicit model instance. """
        return str(scryfall_id)+'_'+finish+'_'+language
    
    @classmethod
    def get_group_by_unique_columns(cls) -> list:
        """ When group_by for this object intends to group by unique stack, run this function, e.g.
        .group_by(*ModelName.get_group_by_unique_columns())
        """
        return [cls.scryfall_id, cls.finish, cls.language]

