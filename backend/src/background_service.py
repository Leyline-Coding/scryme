"""General Background task system 
"""

import asyncio
from contextlib import asynccontextmanager
import logging

from src.db import SessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 1. Shared Queue & Strong Reference Store
jobs: dict[str, dict] = {}
background_tasks_set: set[asyncio.Task] = set()




def queue_service(job_id:str, job_type:str, *args, **kwargs):
    
    jobs[job_id] = {"status": "queued", "result": None}
    print('Job', jobs[job_id])
    # Start task in the background immediately
    task = asyncio.create_task(worker(job_id, job_type, *args, **kwargs))
    background_tasks_set.add(task)
    task.add_done_callback(background_tasks_set.discard)




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

          # Save result and mark complete
          jobs[job_id]["status"] = "completed"
          jobs[job_id]["result"] = f"Processed: "+result
          print('Job', job_id, jobs[job_id])
      except Exception as e:
          jobs[job_id]["status"] = "failed"
          jobs[job_id]["result"] = str(e)
          print('Job', job_id, jobs[job_id])