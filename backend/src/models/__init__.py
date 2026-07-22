"""ORM models. Import every model here so Alembic autogenerate sees them."""

from src.models.binder import Binder, BinderCard
from src.models.box import Box
from src.models.card import Card
from src.models.checklist import Checklist, ChecklistItem
from src.models.collection import CollectionCard
from src.models.deck import Deck, DeckCard, DeckVersion
from src.models.deck_chat import DeckChatMessage
from src.models.embedding import CardEmbedding
from src.models.fx_rate import FxRate, FxRateHistory
from src.models.import_snapshot import ImportSnapshot
from src.models.ingest import IngestState
from src.models.llm_settings import LLMSettings
from src.models.price import CardPricePoint, PriceSnapshot
from src.models.price_target import PriceTarget
from src.models.rules_chunk import RulesChunk
from src.models.saved_search import SavedSearch
from src.models.set_release import SetRelease
from src.models.staging import ImportStaging
from src.models.wishlist import WishlistItem

__all__ = [
    "Binder", "BinderCard", "Box", "Card", "CardEmbedding", "CardPricePoint",
    "Checklist", "ChecklistItem", "CollectionCard", "FxRate", "FxRateHistory",
    "Deck", "DeckCard", "DeckVersion", "DeckChatMessage", "ImportSnapshot", "IngestState",
    "ImportStaging",
    "LLMSettings",
    "PriceSnapshot", "PriceTarget", "RulesChunk", "SavedSearch", "SetRelease", "WishlistItem",
]
