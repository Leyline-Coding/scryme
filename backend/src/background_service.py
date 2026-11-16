"""General Background task system 
"""

import asyncio
from contextlib import asynccontextmanager
import logging

from src.db import SessionLocal
from src.scryfall.images import ImageCache
from src.scryfall.client import ScryfallClient
from src.models import Card, CollectionCard

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        logger.info("Initialized download semaphore with max concurrency: %d", max_concurrent)

def queue_service(job_id:str, job_type:str, *args, **kwargs):
    
    jobs[job_id] = {"status": "queued", "result": None}
    print('Job', job_id, jobs[job_id])
    # Start task in the background immediately
    task = asyncio.create_task(worker(job_id, job_type, *args, **kwargs))
    background_tasks_set.add(task)
    task.add_done_callback(background_tasks_set.discard)

async def _download_worker(job_id: str, card_scryfall_ids: list[str], size: str):
    """Worker that processes the download queue."""
    jobs[job_id]["status"] = "processing"
    jobs[job_id]["total_cards"] = len(card_scryfall_ids)
    jobs[job_id]["processed_cards"] = 0
    
    try:
        # Use semaphore to limit concurrent downloads
        if download_semaphore is None:
            init_download_semaphore()
        
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
                        logger.error("Error downloading image for card %s: %s", scryfall_id, str(e))
                        # Continue with other cards instead of failing the whole batch
                    
        # Save result and mark complete
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = f"Processed {jobs[job_id]['processed_cards']} cards"
        logger.info("Completed download job %s", job_id)
        
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["result"] = str(e)
        logger.error("Failed download job %s: %s", job_id, str(e))

async def worker(job_id: str, job_type: str, *args, **kwargs):
    """Executes your actual task and updates the job dictionary when done."""
    jobs[job_id]["status"] = "processing"
    async with SessionLocal() as session:
      print('Job', job_id, jobs[job_id])
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
              jobs[job_id]["result"] = result
              print('Job', job_id, jobs[job_id])
              return

          # Save result and mark complete
          jobs[job_id]["status"] = "completed"
          jobs[job_id]["result"] = f"Processed: "+result
          print('Job', job_id, jobs[job_id])
      except Exception as e:
          jobs[job_id]["status"] = "failed"
          jobs[job_id]["result"] = str(e)
          print('Job', job_id, jobs[job_id])

def get_job_status(job_id: str) -> dict | None:
    """Get status of a queued job."""
    return jobs.get(job_id)

def get_all_jobs() -> dict:
    """Get status of all queued jobs."""
    return jobs.copy()