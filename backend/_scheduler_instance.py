# backend/_scheduler_instance.py
"""APScheduler singleton accessible by main.py and routers without circular imports."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

