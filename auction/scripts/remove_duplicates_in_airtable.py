import os
from pyairtable import Api
from pyairtable import Table
import traceback
import random
import math
from auction.utils import config_manager
from django.conf import settings
from auction.utils.config_manager import get_warehouse_var, get_global_var, set_active_warehouse
from auction.utils.redis_utils import RedisTaskStatus
import sys
import logging
from django.core.wsgi import get_wsgi_application
import time
from celery import shared_task

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auction_webapp.settings")
application = get_wsgi_application()

from auction.models import Event

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'config.json')
config_manager.load_config(config_path)

def get_valid_auctions(selected_warehouse):
    logger.debug(f"get_valid_auctions called with selected_warehouse: {selected_warehouse}")

    try:
        valid_auctions = Event.objects.filter(warehouse=selected_warehouse).values_list('event_id', flat=True)
        logger.debug(f"Found {len(valid_auctions)} valid auctions for warehouse {selected_warehouse}: {list(valid_auctions)}")
        return list(valid_auctions)
    except Exception as e:
        logger.error(f"An error occurred while querying events: {str(e)}")
        logger.exception("Full traceback:")
        return []

@shared_task(bind=True)
def remove_duplicates_task(self, auction_number, target_msrp, warehouse_name):
    task_id = self.request.id
    logger.info(f"Starting remove duplicates process for auction {auction_number}")
    self.update_state(state="STARTED", meta={'status': f"Starting remove duplicates process for auction {auction_number}"})
    
    try:
        valid_auctions = get_valid_auctions(warehouse_name)
        if auction_number not in valid_auctions:
            logger.warning(f"Auction {auction_number} is not a valid auction for {warehouse_name}. Aborting process.")
            self.update_state(state="FAILURE", meta={'status': f"Auction {auction_number} is not valid for {warehouse_name}"})
            return

        run_remove_dups(self, auction_number, target_msrp, warehouse_name)
        
    except Exception as e:
        error_message = f"An error occurred in remove_duplicates_task: {str(e)}"
        logger.error(error_message)
        logger.exception("Full traceback:")
        self.update_state(state="FAILURE", meta={'status': error_message})
        raise

def update_record_if_needed(record, auction_number, table):
    """Updates the record if it needs an update based on its auction listing status."""
    fields_to_update = get_fields_to_update(record, auction_number)
    if fields_to_update:
        print(f"Updating record {record['id']} with fields: {fields_to_update}")
        table.update(record['id'], fields_to_update, typecast=True)
        return True
    print(f"No update needed for record {record['id']}")
    return False

def get_fields_to_update(record, auction_number):
    """Determines the fields to update based on the record's auction listing status."""
    fields = record['fields']
    auctions = fields.get('Auctions', [])

    if auction_number not in auctions:
        auctions.append(auction_number)
        print(f"Adding auction {auction_number} to record")
        return {'Auctions': auctions}
    print(f"Auction {auction_number} already exists in record")
    return {}

async def update_records_in_airtable(self, auction_number, target_msrp, table, view_name):
    try:
        logger.info(f"Starting to update records for auction {auction_number}")
        self.update_state(state="PROGRESS", meta={'status': f"Fetching records for auction {auction_number}"})
        
        records = table.all(view=view_name, fields=['Product Name', 'Auctions', 'MSRP'])
        logger.info(f"Fetched {len(records)} records from Airtable")
        self.update_state(state="PROGRESS", meta={'status': f"Fetched {len(records)} records from Airtable"})

        groups = {}
        for record in records:
            product_name = record['fields'].get('Product Name')
            if product_name:
                groups.setdefault(product_name, []).append(record)

        logger.info(f"Grouped records into {len(groups)} unique product names")
        self.update_state(state="PROGRESS", meta={'status': f"Grouped records into {len(groups)} unique product names"})

        update_count, total_msrp_reached = 0, 0
        total_groups = len(groups)

        # Calculate progress interval based on total groups
        # If less than 10 groups, update progress for each group
        progress_interval = max(1, total_groups // 10) if total_groups > 0 else 1

        for i, product_name in enumerate(random.sample(list(groups.keys()), total_groups)):
            if total_msrp_reached >= target_msrp:
                break

            records_to_update = sorted(
                (r for r in groups[product_name] if auction_number not in r['fields'].get('Auctions', [])),
                key=lambda r: r['fields'].get('Auction Count', 0)
            )[:math.ceil(len(groups[product_name]) / 2)]

            for record in records_to_update:
                if total_msrp_reached >= target_msrp:
                    break
                if update_record_if_needed(record, auction_number, table):
                    update_count += 1
                    total_msrp_reached += record['fields'].get('MSRP', 0)
            
            # Update progress at regular intervals or for each item if small number
            if i % progress_interval == 0 or i == total_groups - 1:
                progress = int((i + 1) / total_groups * 100)
                logger.info(f"Processed {i + 1}/{total_groups} groups ({progress}%)")
                self.update_state(state="PROGRESS", meta={
                    'status': f"Processed {progress}% of groups",
                    'current': i + 1,
                    'total': total_groups,
                    'update_count': update_count,
                    'total_msrp_reached': total_msrp_reached
                })

        final_message = f"Added auction {auction_number} to {update_count} items. Total MSRP: ${total_msrp_reached:.2f}"
        logger.info(final_message)
        self.update_state(state="SUCCESS", meta={'status': final_message})
        
    except Exception as e:
        error_message = f"Error occurred in update_records_in_airtable: {str(e)}"
        logger.error(error_message)
        logger.exception("Full traceback:")
        self.update_state(state="FAILURE", meta={'status': error_message})
        raise

def run_remove_dups(self, auction_number, target_msrp, warehouse_name):
    logger.info(f"Running remove_dups for auction {auction_number} in {warehouse_name}")
    self.update_state(state="PROGRESS", meta={'status': f"Initializing remove_dups for auction {auction_number}"})
    
    try:
        config_manager.set_active_warehouse(warehouse_name)

        AIRTABLE_TOKEN = config_manager.get_warehouse_var('airtable_api_key')
        AIRTABLE_INVENTORY_BASE_ID = config_manager.get_warehouse_var('airtable_inventory_base_id')
        AIRTABLE_INVENTORY_TABLE_ID = config_manager.get_warehouse_var('airtable_inventory_table_id')
        AIRTABLE_REMOVE_DUPS_VIEW = config_manager.get_warehouse_var('airtable_remove_dups_view')

        if not all([AIRTABLE_TOKEN, AIRTABLE_INVENTORY_BASE_ID, AIRTABLE_INVENTORY_TABLE_ID, AIRTABLE_REMOVE_DUPS_VIEW]):
            raise ValueError("Missing Airtable configuration. Please check your config.json file.")

        logger.info("Airtable configuration loaded successfully")
        self.update_state(state="PROGRESS", meta={'status': "Airtable configuration loaded"})

        table = Table(AIRTABLE_TOKEN, AIRTABLE_INVENTORY_BASE_ID, AIRTABLE_INVENTORY_TABLE_ID)
        logger.info("Airtable Table initialized successfully")
        self.update_state(state="PROGRESS", meta={'status': "Airtable Table initialized"})

        update_records_in_airtable(self, auction_number, target_msrp, table, AIRTABLE_REMOVE_DUPS_VIEW)
        
        logger.info("Remove duplicates process completed successfully.")
        self.update_state(state="SUCCESS", meta={'status': "Remove duplicates process completed successfully"})
        
    except Exception as e:
        error_message = f"An error occurred during the remove duplicates process: {str(e)}"
        logger.error(error_message)
        logger.exception("Full traceback:")
        self.update_state(state="FAILURE", meta={'status': error_message})
        raise
    