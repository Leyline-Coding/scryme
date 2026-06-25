"""Scryfall integration: API client, bulk-data ingestion, and the local image cache.

All outbound traffic goes through :class:`~src.scryfall.client.ScryfallClient`, which enforces
Scryfall's API policy (User-Agent + Accept headers, < 10 req/s, 429 backoff). See
https://scryfall.com/docs/api.
"""
