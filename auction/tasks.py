# tasks.py
from celery import shared_task
from django.conf import settings
from auction.models import Event
from auction.scripts.auction_formatter import AuctionFormatter
from auction.utils import config_manager

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