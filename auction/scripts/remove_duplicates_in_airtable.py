import os
from pyairtable import Api
from pyairtable import Table
import random
import math
from auction.utils import config_manager
import sys
import json

def get_valid_auctions():
    try:
        with open("events.json", "r") as file:
            events = json.load(file)
            return [event["event_id"] for event in events]
    except FileNotFoundError:
        print("events.json file not found.")
        return []
    except json.JSONDecodeError:
        print("Error decoding events.json file.")
        return []

def remove_duplicates_main(auction_number, target_msrp, warehouse_name):
    def gui_callback(message):
        print(message)

    def should_stop():
        return False

    def callback():
        print("Remove duplicates process completed.")

    valid_auctions = get_valid_auctions()
    if auction_number not in valid_auctions:
        print(f"Auction {auction_number} is not in the events.json file. Aborting process.")
        return

    run_remove_dups(auction_number, gui_callback, should_stop, callback, target_msrp, warehouse_name)

if __name__ == "__main__":
    remove_duplicates_main("sample_auction_number", 1000, "sample_warehouse_name")

def update_record_if_needed(record, auction_number, table):
    """Updates the record if it needs an update based on its auction listing status."""
    fields_to_update = get_fields_to_update(record, auction_number)
    if fields_to_update:
        table.update(record['id'], fields_to_update, typecast=True)
        return True
    return False

def get_fields_to_update(record, auction_number):
    """Determines the fields to update based on the record's auction listing status."""
    fields = record['fields']
    auctions = fields.get('Auctions', [])

    # Check if auction number already exists
    if auction_number not in auctions:
        auctions.append(auction_number)
        return {'Auctions': auctions}
    return {}

def update_records_in_airtable(auction_number, gui_callback, should_stop, callback, target_msrp, table, view_name):
    """Main function to update records in Airtable based on the auction number."""
    try:
        # Fetch records with specified fields only to optimize performance
        records = table.all(view=view_name, fields=['Product Name', 'Auctions', 'MSRP'])

        groups = {}
        for record in records:
            # Group by product name, skip if missing
            product_name = record['fields'].get('Product Name')
            if product_name:
                groups.setdefault(product_name, []).append(record)

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

        gui_callback(f"Auction {auction_number} has been added to {update_count} items with total MSRP of ${total_msrp_reached}.")
    except Exception as e:
        gui_callback(f"Error occurred: {e}")
    finally:
        callback()  # Re-enable UI components or similar post-processing

def run_remove_dups(auction_number, gui_callback, should_stop, callback, target_msrp, warehouse_name):
    # Get valid auctions from events.json
    valid_auctions = get_valid_auctions()
    
    if auction_number not in valid_auctions:
        gui_callback(f"Auction {auction_number} is not in the events.json file. Aborting process.")
        callback()
        return

    # Load configuration from config.json for the selected warehouse
    config_path = 'config.json'  # Adjust the path as needed
    config_manager.load_config(config_path, warehouse_name)

    # Retrieve configuration variables
    AIRTABLE_TOKEN = config_manager.get_global_var('airtable_api_key')
    AIRTABLE_INVENTORY_BASE_ID = config_manager.get_global_var('airtable_inventory_base_id')
    AIRTABLE_INVENTORY_TABLE_ID = config_manager.get_global_var('airtable_inventory_table_id')
    AIRTABLE_REMOVE_DUPS_VIEW = config_manager.get_global_var('airtable_remove_dups_view')

    # Initialize Table
    table = Table(AIRTABLE_TOKEN, AIRTABLE_INVENTORY_BASE_ID, AIRTABLE_INVENTORY_TABLE_ID)

    # Run the update process
    update_records_in_airtable(auction_number, gui_callback, should_stop, callback, target_msrp, table, AIRTABLE_REMOVE_DUPS_VIEW)

if __name__ == '__main__':
    if len(sys.argv) != 7:
        print("Usage: python remove_duplicates_in_airtable.py <auction_number> <gui_callback> <should_stop> <callback> <target_msrp> <warehouse_name>")
        sys.exit(1)
    auction_number = sys.argv[1]
    gui_callback = sys.argv[2]
    should_stop = sys.argv[3]
    callback = sys.argv[4]
    target_msrp = float(sys.argv[5])
    warehouse_name = sys.argv[6]
    run_remove_dups(auction_number, gui_callback, should_stop, callback, target_msrp, warehouse_name)