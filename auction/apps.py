from django.apps import AppConfig
from auction.utils import config_manager

class AuctionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'auction'

    def ready(self):
        config_manager.load_config()
        config_manager.set_active_warehouse("Maule Warehouse")