import os
import threading
import re
import time
import csv
import requests
import json
import logging
import traceback
from io import StringIO
from playwright.sync_api import sync_playwright, expect
from datetime import datetime
from urllib.parse import urljoin
from django.core.wsgi import get_wsgi_application
from django.db import transaction

logger = logging.getLogger(__name__)

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auction_webapp.settings")
application = get_wsgi_application()

from auction.models import Event, VoidedTransaction
from auction.utils import config_manager

config_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'config.json')

AIRTABLE_URL = lambda base_id, table_id: f'https://api.airtable.com/v0/{base_id}/{table_id}'

def void_unpaid_main(event_id, upload_choice, warehouse):
    logger.info(f"Starting void_unpaid_main for event_id: {event_id}, upload_choice: {upload_choice}, warehouse: {warehouse}")
    config_manager.load_config(config_path)
    config_manager.set_active_warehouse(warehouse)
    
    should_stop = threading.Event()

    logger.info("Calling start_playwright_process")
    start_playwright_process(event_id, upload_choice, should_stop)
    logger.info("Finished void_unpaid_main")

def login(page, username, password):
    try:
        page.wait_for_selector("#username", state="visible", timeout=15000)
        page.wait_for_selector("#password", state="visible", timeout=15000)
    except:
        print("Login form not found. The page might not have loaded correctly.")
        print(f"Current URL: {page.url}")
        return False

    page.fill("#username", username)
    page.fill("#password", password)
    
    page.press("#password", "Enter")

    time.sleep(5)
    
    try:
        page.wait_for_url(lambda url: "LogOn" not in url, timeout=30000)
        print(f"Login successful. Current URL: {page.url}")
        return True
    except:
        print(f"Login might have failed. Current URL: {page.url}")
        return False

def check_login_status(page):
    try:
        page.wait_for_selector("text=Sign Out", timeout=10000)
        return True
    except:
        current_url = page.url
        if "bid.702auctions.com" in current_url and not current_url.endswith("/Account/LogOn"):
            return True
        return False

def should_continue(should_stop, gui_callback, message):
    if should_stop.is_set():
        gui_callback(message)
        return False
    return True

def export_csv(page, event_id, should_stop):
    if should_stop.is_set():
        logger.info("CSV export operation stopped by user.")
        return None

    logger.info("Starting CSV export...")
    
    try:
        logger.info("Waiting for download to start...")
        with page.expect_download(timeout=30000) as download_info:
            logger.info("Clicking ExportCSV button...")
            page.click("#ExportCSV")
        
        logger.info("Download started, getting download object...")
        download = download_info.value
        
        logger.info("Saving download content...")
        csv_content = download.save_as(StringIO()).getvalue()

        if should_stop.is_set():
            logger.info("CSV export operation stopped during download.")
            return None

        logger.info(f"CSV content length: {len(csv_content)}")

        # Save CSV content to database
        with transaction.atomic():
            logger.info(f"Saving CSV data for event {event_id} to database...")
            event, created = Event.objects.get_or_create(event_id=event_id)
            VoidedTransaction.objects.create(event=event, csv_data=csv_content)
        
        logger.info(f"CSV data for event {event_id} saved to database.")
        return csv_content
    except Exception as e:
        logger.error(f"Error exporting CSV: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        logger.error(f"Current page URL: {page.url}")
        logger.error(f"Page content: {page.content()}")
        return None

def upload_to_airtable(records_batches, headers, csv_filepath, should_stop):
    all_batches_successful = True

    for batch in records_batches:
        if should_stop.is_set():
            print("Upload to Airtable stopped by user.")
            return
        response = requests.post(AIRTABLE_URL(config_manager.get_warehouse_var('airtable_sales_base_id'),
                                              config_manager.get_warehouse_var('airtable_cancels_table_id')),
                                 json={"records": batch}, headers=headers)
        if response.status_code != 200:
            error_message = f"Failed to send data to Airtable: {response.status_code} {response.text}"
            print(error_message)
            print(f"Upload CSV Manually. CSV Filepath: {csv_filepath}")
            all_batches_successful = False
            break

    if all_batches_successful:
        print("Successfully Uploaded to Airtable")

def process_csv_for_airtable(csv_content):
    csv_file = StringIO(csv_content)
    reader = csv.DictReader(csv_file)
    records = [{"fields": record} for record in reader]
    return (records[i:i+10] for i in range(0, len(records), 10))

def send_to_airtable(upload_choice, csv_content, should_stop):
    if should_stop.is_set():
        print("Upload to Airtable stopped by user.")
        return
    if upload_choice == 1:
        print("Uploading data to Airtable...")
        records_batches = process_csv_for_airtable(csv_content)
        headers = {
            'Authorization': f'Bearer {config_manager.get_warehouse_var("airtable_api_key")}',
            'Content-Type': 'application/json'
        }
        upload_to_airtable(records_batches, headers, csv_content, should_stop)
    else:
        print("Upload to Airtable skipped.")

def void_unpaid_transactions(page, report_url, should_stop, timeout=1000, max_retries=5):
    print("Starting the voiding process for unpaid transactions...")
    start_time = time.time()
    count = 0
    retries = 0

    while not should_stop.is_set():
        if time.time() - start_time > timeout:
            print("Timeout reached, stopping voiding process.")
            break

        if retries >= max_retries:
            print("Maximum retries reached, stopping voiding process.")
            break

        try:
            handle_network_error(page, report_url)
            if are_transactions_voided(page):
                print(f"All {count} unpaid transactions have been voided.")
                break
            void_transaction(page)
            count += 1
            print(f"Voided {count} transactions...")
            retries = 0  # Reset retries after successful operation

        except Exception as e:
            handle_retry(page, report_url, e, retries)
            retries += 1

    print(f"Voiding process completed. Total transactions voided: {count}")

def handle_network_error(page, url):
    if page.locator("#main-frame-error").count() > 0:
        print("Network error detected. Reloading the page...")
        page.goto(url)
        page.wait_for_selector("#Time", state="visible", timeout=10000)
        time.sleep(2)
        print("Voiding Unpaid Transactions...")

def are_transactions_voided(page):
    return page.locator(".panel-body .no-history").count() > 0

def void_transaction(page):
    page.click("#ReportResults > div:nth-child(2) > div:nth-child(6) > a")
    time.sleep(2)
    page.click(".modal .btn.btn-danger")
    page.wait_for_selector(".modal.bootstrap-dialog.type-danger", state="hidden")
    time.sleep(2)

def handle_retry(page, url, exception, retries):
    print(f"Error during voiding process: {exception}. Retrying...")
    time.sleep(min(2 ** retries, 60))
    page.goto(url)
    print("Voiding Unpaid Transactions...")

def check_date(page):
    date_element = page.locator("#ReportResults > div:nth-child(1) > div:nth-child(1)")
    date_str = date_element.inner_text().strip()
    date_str = re.search(r'\d{2}/\d{2}/\d{4}', date_str).group()
    extracted_date = datetime.strptime(date_str, '%m/%d/%Y')
    today = datetime.today()
    delta_days = (today - extracted_date).days
    return delta_days < 4

def verify_base_url(page, base_url):
    try:
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        print(f"Base URL accessible: {page.url}")
        return True
    except Exception as e:
        print(f"Error accessing base URL: {str(e)}")
        return False

def start_playwright_process(event_id, upload_choice, should_stop):
    logger.info(f"Starting playwright process for event_id: {event_id}")
    csv_content = None
    login_url = config_manager.get_global_var('website_login_url')
    bid_home_page = config_manager.get_global_var('bid_home_page')
    report_url = f"{bid_home_page}/Account/EventSalesTransactionReport?EventID={event_id}&page=0&sort=DateTime&descending=True&dateStart=&dateEnd=&lotNumber=&description=&priceLow=&priceHigh=&quantity=&totalPriceLow=&totalPriceHigh=&invoiceID=&payer=&firstName=&lastName=&isPaid=2"
    logger.info(f"Report URL: {report_url}")
    
    try:
        with sync_playwright() as p:
            logger.info("Launching browser")
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = browser.new_context()
            page = context.new_page()
            
            logger.info("Initializing browser...")
            page.goto(login_url)

            username = config_manager.get_warehouse_var("bid_username")
            password = config_manager.get_warehouse_var("bid_password")

            if username is None or password is None:
                logger.error("Failed to retrieve login credentials from config.")
                return

            logger.info("Attempting login...")
            login_success = login(page, username, password)

            if not login_success:
                logger.error("Login failed. Aborting process.")
                return

            logger.info(f"Login successful. Current URL: {page.url}")
            
            page.wait_for_load_state("networkidle")

            logger.info("Navigating to report page...")
            page.goto(report_url)
            
            try:
                page.wait_for_selector("#ReportResults", state="visible", timeout=30000)
                logger.info(f"Report page loaded. Current URL: {page.url}")
            except:
                logger.error(f"Timeout waiting for report page. Current URL: {page.url}")
                
                if "Account/LogOn" in page.url:
                    logger.info("Redirected to login page. Session might have expired. Attempting to log in again...")
                    login_success = login(page, username, password)
                    if not login_success:
                        logger.error("Login failed. Aborting process.")
                        return
                    
                    logger.info("Navigating to report page after re-login...")
                    page.goto(report_url)
                    
                    try:
                        page.wait_for_selector("#ReportResults", state="visible", timeout=30000)
                        logger.info(f"Report page loaded after re-login. Current URL: {page.url}")
                    except:
                        logger.error(f"Failed to load report page after re-login. Current URL: {page.url}")
                        return

            if not check_login_status(page):
                logger.error("Not logged in on report page. Aborting process.")
                return

            logger.info("Starting to void unpaid transactions...")
            void_unpaid_transactions(page, report_url, should_stop)

            logger.info("Exporting CSV...")
            csv_content = export_csv(page, event_id, should_stop)

            if csv_content:
                logger.info("CSV exported successfully. Uploading to Airtable...")
                send_to_airtable(upload_choice, csv_content, should_stop)
            else:
                logger.error("CSV content not set due to an error. Skipping Upload to Airtable.")

    except Exception as e:
        logger.exception(f"An error occurred in start_playwright_process: {str(e)}")
        should_stop.set()
    finally:
        logger.info("Closing browser")
        if 'browser' in locals():
            browser.close()
        if csv_content:
            logger.info(f"Process completed. CSV data saved to database for event {event_id}.")
        else:
            logger.info("Process completed, but CSV data was not saved.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Void unpaid transactions")
    parser.add_argument("event_id", help="Event ID")
    parser.add_argument("upload_choice", type=int, choices=[0, 1], help="Upload choice (0: No upload, 1: Upload to Airtable)")
    parser.add_argument("warehouse", help="Warehouse name")
    
    args = parser.parse_args()

    void_unpaid_main(args.event_id, args.upload_choice, args.warehouse)