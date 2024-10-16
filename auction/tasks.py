from celery import shared_task
from datetime import datetime
from django.conf import settings
from auction.models import Event
from auction.scripts.auction_formatter import AuctionFormatter
from auction.scripts.create_auction import format_date, get_image, create_auction, save_event_to_database, create_auction_main
from auction.utils import config_manager
from playwright.sync_api import sync_playwright
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_auction_formatter_task(self, auction_id, selected_warehouse, starting_price):
    config_manager.set_active_warehouse(selected_warehouse)
    event = Event.objects.get(event_id=auction_id)
    
    def gui_callback(message):
        self.update_state(state='PROGRESS', meta={'status': message})
    
    formatter = AuctionFormatter(
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
    return create_auction_main(self, auction_title, ending_date, selected_warehouse)