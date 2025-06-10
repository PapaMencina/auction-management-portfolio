"""
Void Unpaid Transactions Script
Environment-aware configuration:
- Detects Heroku deployment via DYNO environment variable
- Conservative timeouts/delays on Heroku: 30 min timeout, 5 retries
- Aggressive settings locally: 1 hour timeout, 10 retries
- Handles large volumes of transactions with appropriate delays
"""

import os
import threading
import re
import time
import csv
from auction.utils.redis_utils import RedisTaskStatus
import requests
import json
import logging
import traceback
from celery import shared_task, current_task
from io import StringIO
from playwright.sync_api import sync_playwright, expect
from datetime import datetime
from urllib.parse import urljoin
from django.core.wsgi import get_wsgi_application
from django.db import transaction
import tempfile
from asgiref.sync import sync_to_async
from playwright.async_api import async_playwright
import asyncio
import aiohttp

logger = logging.getLogger(__name__)

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auction_webapp.settings")
application = get_wsgi_application()

from auction.models import Event, VoidedTransaction
from auction.utils import config_manager

config_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'config.json')

AIRTABLE_URL = lambda base_id, table_id: f'https://api.airtable.com/v0/{base_id}/{table_id}'

@sync_to_async
def save_csv_to_database(event_id, csv_content):
    with transaction.atomic():
        event, created = Event.objects.get_or_create(event_id=event_id)
        VoidedTransaction.objects.create(event=event, csv_data=csv_content)

async def export_csv(page, event_id):
    logger.info("Starting CSV export...")
    
    try:
        logger.info("Waiting for download to start...")
        async with page.expect_download(timeout=60000) as download_info:
            logger.info("Clicking ExportCSV button...")
            await page.click("#ExportCSV")
        
        logger.info("Download started, getting download object...")
        download = await download_info.value
        
        logger.info("Saving download content to temporary file...")
        with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as temp_file:
            temp_path = temp_file.name
            await download.save_as(temp_path)
        
        logger.info(f"Reading CSV content from temporary file: {temp_path}")
        with open(temp_path, 'r') as file:
            csv_content = file.read()

        logger.info("Removing temporary file...")
        os.unlink(temp_path)

        logger.info(f"CSV content length: {len(csv_content)}")
        logger.info(f"CSV content (first 500 characters): {csv_content[:500]}")

        # Save CSV content to database
        logger.info(f"Saving CSV data for event {event_id} to database...")
        await save_csv_to_database(event_id, csv_content)
        
        logger.info(f"CSV data for event {event_id} saved to database.")
        return csv_content
    except Exception as e:
        logger.error(f"Error exporting CSV: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        logger.error(f"Current page URL: {page.url}")
        logger.error(f"Page content: {await page.content()}")
        return None

@shared_task(bind=True)
def void_unpaid_main(self, event_id, upload_choice, warehouse):
    try:
        logger.info(f"Starting void_unpaid_main for event_id: {event_id}, upload_choice: {upload_choice}, warehouse: {warehouse}")
        self.update_state(state="STARTED", meta={'status': f"Starting void unpaid process for event {event_id}"})
        
        config_manager.load_config(config_path)
        config_manager.set_active_warehouse(warehouse)
        self.update_state(state="PROGRESS", meta={'status': f"Configured for warehouse: {warehouse}"})
        
        task_id = self.request.id
        RedisTaskStatus.set_status(task_id, "STARTED", f"Starting void unpaid process for event {event_id}")

        # Running async Playwright process
        try:
            asyncio.run(start_playwright_process(event_id, upload_choice, task_id))
        except Exception as e:
            logger.error(f"Error in start_playwright_process: {str(e)}")
            self.update_state(state="FAILURE", meta={'status': f"Error in void unpaid process: {str(e)}"})
            RedisTaskStatus.set_status(task_id, "ERROR", f"Error in void unpaid process: {str(e)}")
            raise

        logger.info("Finished void_unpaid_main successfully")
        self.update_state(state="SUCCESS", meta={'status': f"Void unpaid process completed for event {event_id}"})
        RedisTaskStatus.set_status(task_id, "COMPLETED", f"Void unpaid process completed for event {event_id}")

        return task_id

    except Exception as e:
        error_message = f"Unexpected error in void_unpaid_main: {str(e)}"
        logger.error(error_message)
        self.update_state(state="FAILURE", meta={'status': error_message})
        if hasattr(self, 'request'):
            RedisTaskStatus.set_status(self.request.id, "ERROR", error_message)
        raise

async def login(page, username, password):
    """Logs in to the auction site using provided credentials."""
    try:
        # Wait for and fill username field
        logger.info("Waiting for username field to be visible...")
        await page.wait_for_selector("#username", state="visible", timeout=30000)
        await page.fill("#username", username)
        
        # Wait for and fill password field
        logger.info("Waiting for password field to be visible...")
        await page.wait_for_selector("#password", state="visible", timeout=30000)
        await page.fill("#password", password)
        
        # Wait for and click the sign-in button
        logger.info("Waiting for sign-in button to be visible...")
        sign_in_button = await page.wait_for_selector('input[type="submit"][value="Sign In"]', state="visible", timeout=30000)
        if sign_in_button:
            await sign_in_button.click()
        else:
            logger.error("Sign in button not found")
            await page.screenshot(path='login_error_button_not_found.png')
            return False

        # Wait for navigation after clicking sign in
        await page.wait_for_load_state('networkidle', timeout=60000)
        
        # Check if login was successful
        if "logon" in page.url.lower() or "login" in page.url.lower():
            logger.error("Login failed. Still on login page.")
            await page.screenshot(path='login_error_still_on_login_page.png')
            return False
        
        logger.info(f"Login successful. Current URL: {page.url}")
        return True
    except Exception as e:
        logger.error(f"Login failed: {e}")
        logger.error(f"Current URL: {page.url}")
        await page.screenshot(path='login_error_exception.png')
        return False

async def check_login_status(page):
    try:
        await page.wait_for_selector("text=Sign Out", timeout=10000)
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

async def start_playwright_process(event_id, upload_choice, task_id):
    logger.info(f"Starting playwright process for event_id: {event_id}")
    
    # Log environment
    is_heroku = os.environ.get('DYNO') is not None
    logger.info(f"Running on {'Heroku' if is_heroku else 'local environment'}")
    
    csv_content = None
    login_url = config_manager.get_global_var('website_login_url')
    bid_home_page = config_manager.get_global_var('bid_home_page')
    report_url = f"{bid_home_page}/Account/EventSalesTransactionReport?EventID={event_id}&page=0&sort=DateTime&descending=True&dateStart=&dateEnd=&lotNumber=&description=&priceLow=&priceHigh=&quantity=&totalPriceLow=&totalPriceHigh=&invoiceID=&payer=&firstName=&lastName=&isPaid=2"
    logger.info(f"Report URL: {report_url}")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = await browser.new_context()
            page = await context.new_page()
            
            current_task.update_state(state="PROGRESS", meta={'status': "Logging in to the auction site"})
            RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Logging in to the auction site")
            
            await page.goto(login_url)
            username = config_manager.get_warehouse_var("bid_username")
            password = config_manager.get_warehouse_var("bid_password")
            if username is None or password is None:
                raise ValueError("Failed to retrieve login credentials from config.")
            
            login_success = await login(page, username, password)
            if not login_success:
                raise Exception("Login failed. Aborting process.")

            current_task.update_state(state="PROGRESS", meta={'status': "Navigating to report page"})
            RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Navigating to report page")
            await page.goto(report_url)
            try:
                await page.wait_for_selector("#ReportResults", state="visible", timeout=60000)
            except:
                if "Account/LogOn" in page.url:
                    current_task.update_state(state="PROGRESS", meta={'status': "Re-attempting login"})
                    RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Re-attempting login")
                    login_success = await login(page, username, password)
                    if not login_success:
                        raise Exception("Login failed on re-attempt. Aborting process.")
                    await page.goto(report_url)
                    await page.wait_for_selector("#ReportResults", state="visible", timeout=60000)
                else:
                    raise Exception(f"Failed to load report page. Current URL: {page.url}")

            if not await check_login_status(page):
                raise Exception("Not logged in on report page. Aborting process.")

            current_task.update_state(state="PROGRESS", meta={'status': "Exporting CSV"})
            RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Exporting CSV")
            csv_content = await export_csv(page, event_id)

            if csv_content:
                current_task.update_state(state="PROGRESS", meta={'status': "Uploading to Airtable"})
                RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Uploading to Airtable")
                await send_to_airtable(upload_choice, csv_content)
            else:
                raise Exception("CSV content not set due to an error. Skipping Upload to Airtable.")

            current_task.update_state(state="PROGRESS", meta={'status': "Voiding unpaid transactions"})
            RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Voiding unpaid transactions")
            await void_unpaid_transactions(page, report_url, task_id)

    except Exception as e:
        error_message = f"An error occurred in start_playwright_process: {str(e)}"
        logger.exception(error_message)
        current_task.update_state(state="FAILURE", meta={'status': error_message})
        RedisTaskStatus.set_status(task_id, "ERROR", error_message)
        raise
    finally:
        logger.info("Closing browser")
        if 'browser' in locals():
            await browser.close()
        if csv_content:
            success_message = f"Process completed. CSV data saved to database for event {event_id}."
            logger.info(success_message)
            current_task.update_state(state="SUCCESS", meta={'status': success_message})
            RedisTaskStatus.set_status(task_id, "COMPLETED", success_message)
        else:
            warning_message = "Process completed, but CSV data was not saved."
            logger.warning(warning_message)
            current_task.update_state(state="SUCCESS", meta={'status': warning_message})
            RedisTaskStatus.set_status(task_id, "COMPLETED", warning_message)

async def upload_to_airtable(records_batches, headers, csv_filepath):
    all_batches_successful = True
    batch_count = 0
    total_batches = len(records_batches)
    logger.info(f"Starting upload to Airtable. Total batches: {total_batches}")

    async with aiohttp.ClientSession() as session:
        for batch in records_batches:
            batch_count += 1
            try:
                logger.info(f"Uploading batch {batch_count}/{total_batches} to Airtable")
                async with session.post(AIRTABLE_URL(config_manager.get_warehouse_var('airtable_sales_base_id'),
                                                     config_manager.get_warehouse_var('airtable_cancels_table_id')),
                                        json={"records": batch}, headers=headers) as response:
                    if response.status != 200:
                        error_message = f"Failed to send batch {batch_count}/{total_batches} to Airtable: {response.status} {await response.text()}"
                        logger.error(error_message)
                        logger.error(f"Upload CSV Manually. CSV Filepath: {csv_filepath}")
                        all_batches_successful = False
                        break
                    else:
                        logger.info(f"Successfully uploaded batch {batch_count}/{total_batches} to Airtable")
            except Exception as e:
                logger.error(f"Exception occurred while uploading batch {batch_count}/{total_batches} to Airtable: {str(e)}")
                all_batches_successful = False
                break

    if all_batches_successful:
        logger.info(f"Successfully Uploaded all {batch_count} batches to Airtable")
    else:
        logger.warning(f"Upload to Airtable incomplete. {batch_count}/{total_batches} batches attempted.")

def process_csv_for_airtable(csv_content):
    # Define mapping of CSV column names to Airtable field names
    field_mapping = {
        "Lot Number": "Lot #",
        "Date/Time": "Date/Time",
        "Invoice #": "Invoice #",
        "Description": "Description",
        "Price": "Price",
        "Quantity": "Quantity",
        "Total": "Total",
        "Paid": "Paid",
        "Buyer ID": "Buyer ID",
        "Buyer": "Buyer",
        "Address": "Address",
        "First Name": "First Name",
        "Last Name": "Last Name",
        "MSRP": "MSRP",
        "UPC": "UPC",
        "Item Condition": "Item Condition",
        "Other Notes": "Other Notes",
        "Source": "Source",
        "Photo Taker": "Photo Taker",
        "Amazon ID": "Amazon ID",
        "Buyer Phone Number": "Buyer Phone Number",
        "Buyer Tax Exempt": "Buyer Tax Exempt",
        "Status": "Status"
    }
    
    csv_file = StringIO(csv_content)
    reader = csv.DictReader(csv_file)
    
    records = []
    for row in reader:
        # Create a new dict with mapped fields
        mapped_record = {}
        for key, value in row.items():
            # If we have a mapping for this field, use the mapped name
            airtable_field_name = field_mapping.get(key, key)
            mapped_record[airtable_field_name] = value
                
        records.append({"fields": mapped_record})
    
    logger.info(f"Total records processed from CSV: {len(records)}")
    # Create batches of 10 records each for Airtable's rate limits
    batches = list(records[i:i+10] for i in range(0, len(records), 10))
    logger.info(f"Number of batches created: {len(batches)}")
    return batches

async def send_to_airtable(upload_choice, csv_content):
    if upload_choice == 1:
        logger.info("Uploading data to Airtable...")
        records_batches = process_csv_for_airtable(csv_content)
        headers = {
            'Authorization': f'Bearer {config_manager.get_warehouse_var("airtable_api_key")}',
            'Content-Type': 'application/json'
        }
        await upload_to_airtable(records_batches, headers, csv_content)
    else:
        logger.info("Upload to Airtable skipped due to upload_choice.")

async def void_unpaid_transactions(page, report_url, task_id, timeout=None, max_retries=None):
    # Environment-based defaults
    is_heroku = os.environ.get('DYNO') is not None
    if timeout is None:
        timeout = 1800 if is_heroku else 3600  # 30 min on Heroku, 1 hour locally
    if max_retries is None:
        max_retries = 5 if is_heroku else 10
    print("Starting the voiding process for unpaid transactions...")
    RedisTaskStatus.set_status(task_id, "IN_PROGRESS", "Starting to void unpaid transactions")
    start_time = time.time()
    count = 0
    retries = 0

    while True:
        if time.time() - start_time > timeout:
            print("Timeout reached, stopping voiding process.")
            RedisTaskStatus.set_status(task_id, "COMPLETED", f"Timeout reached. Voided {count} transactions")
            break

        if retries >= max_retries:
            print("Maximum retries reached, stopping voiding process.")
            RedisTaskStatus.set_status(task_id, "COMPLETED", f"Max retries reached. Voided {count} transactions")
            break

        try:
            await handle_network_error(page, report_url)
            if await are_transactions_voided(page):
                print(f"All {count} unpaid transactions have been voided.")
                RedisTaskStatus.set_status(task_id, "COMPLETED", f"All {count} unpaid transactions voided")
                break
            await void_transaction(page)
            count += 1
            print(f"Voided {count} transactions...")
            RedisTaskStatus.set_status(task_id, "IN_PROGRESS", f"Voided {count} transactions")
            retries = 0  # Reset retries after successful operation

        except Exception as e:
            await handle_retry(page, report_url, e, retries)
            retries += 1
            RedisTaskStatus.set_status(task_id, "IN_PROGRESS", f"Retry {retries}/{max_retries}. {count} transactions voided so far")

    print(f"Voiding process completed. Total transactions voided: {count}")
    RedisTaskStatus.set_status(task_id, "COMPLETED", f"Voiding process completed. Total transactions voided: {count}")

async def handle_network_error(page, url):
    if await page.locator("#main-frame-error").count() > 0:
        print("Network error detected. Reloading the page...")
        await page.goto(url)
        await page.wait_for_selector("#Time", state="visible", timeout=10000)
        await asyncio.sleep(2)
        print("Voiding Unpaid Transactions...")

async def are_transactions_voided(page):
    return await page.locator(".panel-body .no-history").count() > 0

async def void_transaction(page):
    # Environment-based delays
    is_heroku = os.environ.get('DYNO') is not None
    delay = 2 if is_heroku else 3  # Conservative on Heroku, more relaxed locally
    
    await page.click("#ReportResults > div:nth-child(2) > div:nth-child(6) > a")
    await asyncio.sleep(delay)
    await page.click(".modal .btn.btn-danger")
    await page.wait_for_selector(".modal.bootstrap-dialog.type-danger", state="hidden")
    await asyncio.sleep(delay)

async def handle_retry(page, url, exception, retries):
    print(f"Error during voiding process: {exception}. Retrying...")
    await asyncio.sleep(min(2 ** retries, 60))
    await page.goto(url)
    print("Voiding Unpaid Transactions...")

async def check_date(page):
    date_element = await page.locator("#ReportResults > div:nth-child(1) > div:nth-child(1)")
    date_str = await date_element.inner_text()
    date_str = date_str.strip()
    date_str = re.search(r'\d{2}/\d{2}/\d{4}', date_str).group()
    extracted_date = datetime.strptime(date_str, '%m/%d/%Y')
    today = datetime.today()
    delta_days = (today - extracted_date).days
    return delta_days < 4

async def verify_base_url(page, base_url):
    try:
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")
        print(f"Base URL accessible: {page.url}")
        return True
    except Exception as e:
        print(f"Error accessing base URL: {str(e)}")
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Void unpaid transactions")
    parser.add_argument("event_id", help="Event ID")
    parser.add_argument("upload_choice", type=int, choices=[0, 1], help="Upload choice (0: No upload, 1: Upload to Airtable)")
    parser.add_argument("warehouse", help="Warehouse name")
    
    args = parser.parse_args()

    void_unpaid_main(args.event_id, args.upload_choice, args.warehouse)