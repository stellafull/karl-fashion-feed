"""
Scheduler Service
- 每天8点采集时尚资讯 入库 更新RAG
"""

import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from config import milvus_uri, milvus_token