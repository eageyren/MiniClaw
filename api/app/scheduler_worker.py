"""Independent durable reminder scheduler; delivery channels are added in S11."""
import logging
import time
from datetime import datetime, timezone

from app.reminder_service import claim_due_batch
from app.reminder_delivery_service import process_reminder_deliveries

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("miniclaw.scheduler")

def main():
    logger.info("Reminder scheduler started")
    last_delivery_run = 0.0
    while True:
        try:
            count = claim_due_batch(datetime.now(timezone.utc))
            if count:
                logger.info("Recorded %d due reminder occurrence(s)", count)
            if time.monotonic() - last_delivery_run >= 15:
                process_reminder_deliveries(datetime.now(timezone.utc))
                last_delivery_run = time.monotonic()
        except Exception as exc:
            logger.warning("Scheduler iteration failed: %s", type(exc).__name__)
        time.sleep(1)

if __name__ == "__main__":
    main()
