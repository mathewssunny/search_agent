import os
import logging
import sys
from main import book_activity_task
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TestBooking")

if __name__ == "__main__":
    target_date = "23/02/2026"
    print(f"Testing booking task for {target_date}...")
    result = book_activity_task(target_date=target_date)
    print(f"\nResult: {result}")
