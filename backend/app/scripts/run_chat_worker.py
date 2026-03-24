"""Chat worker process for consuming queued assistant messages."""

from __future__ import annotations

import asyncio
import time

from backend.app.core.database import SessionLocal
from backend.app.service.chat_worker_service import ChatWorkerService


async def run_worker():
    """Run the chat worker loop."""
    worker = ChatWorkerService()

    print("Chat worker started. Polling for queued messages...")

    while True:
        db = SessionLocal()
        try:
            processed = await worker.process_one_message(db)

            if processed:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Processed one message")
            else:
                # No messages available, sleep for 1 second
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Worker error: {e}")
            await asyncio.sleep(1)

        finally:
            db.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
