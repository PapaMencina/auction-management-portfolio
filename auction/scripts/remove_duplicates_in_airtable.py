import os
from pyairtable import Api
from pyairtable import Table
import traceback
import threading
import random
import math
from auction.utils import config_manager
from django.conf import settings
from auction.utils.config_manager import get_warehouse_var, get_global_var, set_active_warehouse
import sys
import json
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'config.json')
config_manager.load_config(config_path)

def get_valid_auctions(selected_warehouse):
    logger.debug(f"get_valid_auctions called with selected_warehouse: {selected_warehouse}")

    try:
        # Set the active warehouse
        set_active_warehouse(selected_warehouse)

        # Get the base directory from Django settings
        base_dir = settings.BASE_DIR
        logger.debug(f"Base directory from Django settings: {base_dir}")

        # Construct the path to events.json
        events_json_path = os.path.join(base_dir, 'auction', 'resources', 'events.json')
        logger.debug(f"Constructed events.json path: {events_json_path}")

        if os.path.exists(events_json_path):
            logger.debug(f"events.json file found at {events_json_path}")
            if os.path.getsize(events_json_path) > 0:
                with open(events_json_path, "r") as file:
                    events = json.load(file)
                logger.debug(f"Loaded events: {events}")
                valid_auctions = [event["event_id"] for event in events if event.get("warehouse") == selected_warehouse]
                logger.debug(f"Found {len(valid_auctions)} valid auctions for warehouse {selected_warehouse}: {valid_auctions}")
                return valid_auctions
            else:
                logger.warning(f"events.json file is empty at {events_json_path}")
        else:
            logger.error(f"events.json file not found at {events_json_path}")
        return []
    except Exception as e:
        logger.error(f"An error occurred while processing events: {str(e)}")
        logger.exception("Full traceback:")
        return []

def remove_duplicates_main(auction_number, target_msrp, warehouse_name):
    def gui_callback(message):
        print(message)

    should_stop = threading.Event()  # Create the Event object here

    def callback():
        print("Remove duplicates process completed.")

    print(f"Starting remove duplicates process for auction {auction_number} with target MSRP ${target_msrp}")
    
    valid_auctions = get_valid_auctions(warehouse_name)
    if auction_number not in valid_auctions:
        print(f"Auction {auction_number} is not a valid auction for {warehouse_name}. Aborting process.")
        return

    run_remove_dups(auction_number, gui_callback, should_stop, callback, target_msrp, warehouse_name)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description="Remove duplicates in Airtable")
    parser.add_argument("auction_number", help="Auction number")
    parser.add_argument("target_msrp", type=float, help="Target MSRP")
    parser.add_argument("warehouse_name", help="Warehouse name")
    
    args = parser.parse_args()

    def gui_callback(message):
        print(message)

    should_stop = threading.Event()
    
    def callback():
        print("Remove duplicates process completed.")

    remove_duplicates_main(args.auction_number, args.target_msrp, args.warehouse_name)

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

    # Check if auction number already exists
    if auction_number not in auctions:
        auctions.append(auction_number)
        print(f"Adding auction {auction_number} to record")
        return {'Auctions': auctions}
    print(f"Auction {auction_number} already exists in record")
    return {}

def update_records_in_airtable(auction_number, gui_callback, should_stop, callback, target_msrp, table, view_name):
    """Main function to update records in Airtable based on the auction number."""
    try:
        gui_callback(f"Starting to update records for auction {auction_number}")
        
        # Fetch records with specified fields only to optimize performance
        records = table.all(view=view_name, fields=['Product Name', 'Auctions', 'MSRP'])
        gui_callback(f"Fetched {len(records)} records from Airtable")

        groups = {}
        for record in records:
            # Group by product name, skip if missing
            product_name = record['fields'].get('Product Name')
            if product_name:
                groups.setdefault(product_name, []).append(record)

        gui_callback(f"Grouped records into {len(groups)} unique product names")

        update_count, total_msrp_reached = 0, 0

        # Process groups in random order by converting dict_keys to a list for random.sample
        for product_name in random.sample(list(groups.keys()), len(groups)):
            if should_stop.is_set() or total_msrp_reached >= target_msrp:
                break

            # Sort by 'Auction Count' after filtering out records with the current auction number
            records_to_update = sorted(
                (r for r in groups[product_name] if auction_number not in r['fields'].get('Auctions', [])),
                key=lambda r: r['fields'].get('Auction Count', 0)
            )[:math.ceil(len(groups[product_name]) / 2)]

            for record in records_to_update:
                if should_stop.is_set() or total_msrp_reached >= target_msrp:
                    break
                if update_record_if_needed(record, auction_number, table):
                    update_count += 1
                    total_msrp_reached += record['fields'].get('MSRP', 0)
                    gui_callback(f"Updated record {record['id']} for product {product_name}")

        gui_callback(f"Auction {auction_number} has been added to {update_count} items with total MSRP of ${total_msrp_reached}.")
    except Exception as e:
        gui_callback(f"Error occurred: {e}")
        traceback.print_exc()
    finally:
        callback()  # Re-enable UI components or similar post-processing

def run_remove_dups(auction_number, gui_callback, should_stop, callback, target_msrp, warehouse_name):
    gui_callback(f"Running remove_dups for auction {auction_number} in {warehouse_name}")
    
    config_manager.set_active_warehouse(warehouse_name)

    AIRTABLE_TOKEN = config_manager.get_warehouse_var('airtable_api_key')
    AIRTABLE_INVENTORY_BASE_ID = config_manager.get_warehouse_var('airtable_inventory_base_id')
    AIRTABLE_INVENTORY_TABLE_ID = config_manager.get_warehouse_var('airtable_inventory_table_id')
    AIRTABLE_REMOVE_DUPS_VIEW = config_manager.get_warehouse_var('airtable_remove_dups_view')

    # Check if all required configuration variables are present
    if not all([AIRTABLE_TOKEN, AIRTABLE_INVENTORY_BASE_ID, AIRTABLE_INVENTORY_TABLE_ID, AIRTABLE_REMOVE_DUPS_VIEW]):
        error_msg = "Missing Airtable configuration. Please check your config.json file."
        gui_callback(error_msg)
        callback()
        return

    gui_callback(f"Airtable configuration: Token: {AIRTABLE_TOKEN[:5]}..., Base ID: {AIRTABLE_INVENTORY_BASE_ID}, Table ID: {AIRTABLE_INVENTORY_TABLE_ID}, View: {AIRTABLE_REMOVE_DUPS_VIEW}")

    # Initialize Table
    try:
        table = Table(AIRTABLE_TOKEN, AIRTABLE_INVENTORY_BASE_ID, AIRTABLE_INVENTORY_TABLE_ID)
        gui_callback("Airtable Table initialized successfully")
    except Exception as e:
        error_msg = f"Failed to initialize Airtable: {str(e)}"
        gui_callback(error_msg)
        callback()
        return

    # Run the update process
    try:
        update_records_in_airtable(auction_number, gui_callback, should_stop, callback, target_msrp, table, AIRTABLE_REMOVE_DUPS_VIEW)
    except Exception as e:
        gui_callback(f"An error occurred during the update process: {str(e)}")
        traceback.print_exc()
    finally:
        callback()