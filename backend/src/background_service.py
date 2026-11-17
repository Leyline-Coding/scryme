"""General Background task system 
"""

import asyncio
import structlog
from contextlib import asynccontextmanager

from src.db import SessionLocal
from src.scryfall.images import ImageCache
from src.scryfall.client import ScryfallClient
from src.models import Card, CollectionCard

log = structlog.get_logger()


# 1. Shared Queue & Strong Reference Store
jobs: dict[str, dict] = {}
background_tasks_set: set[asyncio.Task] = set()
# Add semaphore to control concurrent downloads (to respect rate limits)
download_semaphore: asyncio.Semaphore | None = None

def init_download_semaphore(max_concurrent: int = 5):
    """Initialize the semaphore to control concurrent downloads."""
    global download_semaphore
    if download_semaphore is None:
        download_semaphore = asyncio.Semaphore(max_concurrent)

def queue_service(job_id:str, job_type:str, *args, **kwargs):
    
    jobs[job_id] = {"status": "queued", "result": None}
    log.info("scryme.job_queued", key=job_id)
    # Start task in the background immediately
    task = asyncio.create_task(worker(job_id, job_type, *args, **kwargs))
    background_tasks_set.add(task)
    task.add_done_callback(background_tasks_set.discard)

async def _download_worker(job_id: str, card_scryfall_ids: list[str], size: str) -> str:
    """Worker that processes the download queue."""
    jobs[job_id]["total_cards"] = len(card_scryfall_ids)
    jobs[job_id]["processed_cards"] = 0
    
    # Use semaphore to limit concurrent downloads
    if download_semaphore is None: init_download_semaphore()
    if download_semaphore is None: raise Exception('Failed to init semaphore')
    
    async with SessionLocal() as session:
        cache = ImageCache()
        
        # Process cards sequentially but with concurrent downloads (through semaphore)
        for scryfall_id in card_scryfall_ids:
            async with download_semaphore:  # Acquire semaphore
                try:
                    # Get card from database
                    card = await session.get(Card, scryfall_id)
                    
                    if card and not cache.is_cached(scryfall_id, size):
                        async with ScryfallClient() as client:
                            status = await cache.ensure(card, client, size)
                            
                            # Update card image_status in DB
                            if card is not None:
                                card.image_status = status
                                await session.commit()
                                
                    jobs[job_id]["processed_cards"] += 1
                    
                except Exception as e:
                    log.error("Error downloading image for card", scryfall_id, str(e))
                    # Continue with other cards instead of failing the whole batch
                    
    return f"Processed {jobs[job_id]['processed_cards']} cards"

async def worker(job_id: str, job_type: str, *args, **kwargs):
    """Executes your actual task and updates the job dictionary when done."""
    async with SessionLocal() as session:
      log.info("scryme.job_start", key=job_id)
      jobs[job_id]["status"] = "processing"
      try:
          if(job_type=='ai_chat'):
              from src.llm import ai_deck_chat_response_worker
              message_id = await ai_deck_chat_response_worker(session, *args, **kwargs)
              result = "DeckMessage #"+str(message_id)
          elif(job_type=='nl_to_q'):
              from src.llm import nlq_worker
              nlq_id = await nlq_worker(session, *args, **kwargs)
              result = "NLQ #"+str(nlq_id)
          elif(job_type=='backfill-image'):
              # This is the new functionality for image backfilling
              card_scryfall_ids = args[0] if args else []
              size = kwargs.get('size', 'normal')
              await _download_worker(job_id, card_scryfall_ids, size)
              # For backfill-image jobs, we set result based on what was processed
              processed = jobs[job_id].get('processed_cards', 0) if 'processed_cards' in jobs[job_id] else 0
              result = f"Processed {processed} cards"

          # Save result and mark complete
          jobs[job_id]["status"] = "completed"
          jobs[job_id]["result"] = f"Processed: "+result
      except Exception as e:
          jobs[job_id]["status"] = "failed"
          jobs[job_id]["result"] = str(e)

      log.info("scryme.job_finished", key=job_id, status=jobs[job_id]['status'], result=jobs[job_id]['result'])

def get_job_status(job_id: str) -> dict | None:
    """Get status of a queued job."""
    return jobs.get(job_id)

def get_all_jobs() -> dict:
    """Get status of all queued jobs."""
    return jobs.copy()