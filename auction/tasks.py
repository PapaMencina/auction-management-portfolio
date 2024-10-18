from celery import shared_task
from datetime import datetime
from django.conf import settings
from auction.models import Event
from auction.scripts.auction_formatter import auction_formatter_task
from auction.scripts.create_auction import format_date, get_image, create_auction, save_event_to_database, create_auction_main
from auction.utils import config_manager
from auction.scripts.void_unpaid_on_bid import start_playwright_process, void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import run_remove_dups, get_valid_auctions
import logging
import asyncio

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_auction_formatter_task(self, auction_id, selected_warehouse, starting_price):
    config_manager.set_active_warehouse(selected_warehouse)
    event = Event.objects.get(event_id=auction_id)
    
    def gui_callback(message):
        self.update_state(state='PROGRESS', meta={'status': message})
    
    formatter = auction_formatter_task(
        event=event,
        gui_callback=gui_callback,
        should_stop=None,
        callback=lambda: None,
        selected_warehouse=selected_warehouse,
        starting_price=starting_price,
        task_id=self.request.id
    )
    
    return formatter.run_auction_formatter()

@shared_task(bind=True)
def create_auction_task(self, auction_title, ending_date, selected_warehouse):
    task_id = self.request.id
    
    if isinstance(ending_date, str):
        try:
            ending_date = datetime.strptime(ending_date, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError("Invalid date format. Please use 'YYYY-MM-DD'.")
    elif isinstance(ending_date, datetime):
        ending_date = ending_date.date()
    else:
        raise TypeError("ending_date must be a string or datetime object")
    
    async def run_async_task():
        return await create_auction_main(auction_title, ending_date, selected_warehouse, task_id)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(run_async_task())
    finally:
        loop.close()

@shared_task(bind=True)
def void_unpaid_task(self, event_id, upload_choice, warehouse):
    task_id = self.request.id
    return void_unpaid_main(event_id, upload_choice, warehouse, task_id)

@shared_task(bind=True)
def remove_duplicates_task(self, auction_number, target_msrp, warehouse_name):
    task_id = self.request.id
    logger.info(f"Starting remove duplicates process for auction {auction_number}")
    self.update_state(state="STARTED", meta={'status': f"Starting remove duplicates process for auction {auction_number}"})
    
    valid_auctions = get_valid_auctions(warehouse_name)
    if auction_number not in valid_auctions:
        logger.warning(f"Auction {auction_number} is not a valid auction for {warehouse_name}. Aborting process.")
        self.update_state(state="FAILURE", meta={'status': f"Auction {auction_number} is not valid for {warehouse_name}"})
        return

    config_manager.set_active_warehouse(warehouse_name)
    run_remove_dups(self, auction_number, target_msrp, warehouse_name)