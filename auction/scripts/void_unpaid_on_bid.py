import os
import threading
import re
import time
import csv
import requests
import json
import traceback
from playwright.sync_api import sync_playwright, expect
from datetime import datetime
from urllib.parse import urljoin
from auction.utils import config_manager
from auction.utils.progress_tracker import ProgressTracker, with_progress_tracking

config_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'config.json')

def get_resources_dir(folder):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    resources_dir = os.path.join(project_root, 'auction', 'resources', folder)
    os.makedirs(resources_dir, exist_ok=True)
    return resources_dir

DOWNLOAD_DIR = get_resources_dir('voided_csv')
AIRTABLE_URL = lambda base_id, table_id: f'https://api.airtable.com/v0/{base_id}/{table_id}'

@with_progress_tracking
def void_unpaid_main(auction_id, upload_choice, warehouse, update_progress):
    config_manager.load_config(config_path)
    config_manager.set_active_warehouse(warehouse)
    
    should_stop = threading.Event()

    def callback():
        update_progress(100, "Void unpaid process completed.")

    start_playwright_process(auction_id, upload_choice, update_progress, should_stop, callback)

def login(page, username, password, update_progress, should_stop):
    if not should_continue(should_stop, lambda msg: update_progress(20, msg), "Login operation stopped by user."):
        return False

    update_progress(22, "Waiting for login form...")
    try:
        page.wait_for_selector("#username", state="visible", timeout=15000)
        page.wait_for_selector("#password", state="visible", timeout=15000)
    except:
        update_progress(23, "Login form not found. The page might not have loaded correctly.")
        update_progress(24, f"Current URL: {page.url}")
        return False

    update_progress(25, "Entering credentials...")
    page.fill("#username", username)
    page.fill("#password", password)
    
    if not should_continue(should_stop, lambda msg: update_progress(27, msg), "Login operation stopped before finalizing."):
        return False

    update_progress(28, "Submitting login form...")
    page.press("#password", "Enter")

    time.sleep(5)
    
    update_progress(30, "Waiting for login to complete...")
    try:
        page.wait_for_url(lambda url: "LogOn" not in url, timeout=30000)
        update_progress(35, f"Login successful. Current URL: {page.url}")
        return True
    except:
        update_progress(35, f"Login might have failed. Current URL: {page.url}")
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

def export_csv(page, event_id, update_progress, should_stop):
    if not should_continue(should_stop, lambda msg: update_progress(None, msg), "CSV export operation stopped by user."):
        return None

    update_progress(None, "Exporting CSV...")
    filename = f"SalesTransactions_Event_{event_id}.csv"
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(file_path):
        update_progress(None, f"File {filename} already exists. Skipping download.")
        return file_path

    try:
        with page.expect_download() as download_info:
            page.click("#ExportCSV")
        download = download_info.value
        download.save_as(file_path)

        if not should_continue(should_stop, lambda msg: update_progress(None, msg), "CSV export operation stopped during download."):
            return None
        return file_path
    except Exception as e:
        update_progress(None, f"Error exporting CSV: {str(e)}")
        return None

def upload_to_airtable(records_batches, headers, csv_filepath, update_progress, should_stop):
    all_batches_successful = True

    for batch in records_batches:
        if not should_continue(should_stop, lambda msg: update_progress(None, msg), "Upload to Airtable stopped by user."):
            return
        response = requests.post(AIRTABLE_URL(config_manager.get_warehouse_var('airtable_sales_base_id'),
                                              config_manager.get_warehouse_var('airtable_cancels_table_id')),
                                 json={"records": batch}, headers=headers)
        if response.status_code != 200:
            error_message = f"Failed to send data to Airtable: {response.status_code} {response.text}"
            update_progress(None, error_message)            
            update_progress(None, f"Upload CSV Manually. CSV Filepath: {csv_filepath}")
            all_batches_successful = False
            break

    if all_batches_successful:
        update_progress(None, "Successfully Uploaded to Airtable")

def process_csv_for_airtable(csv_filepath):
    with open(csv_filepath, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        records = [{"fields": record} for record in reader] 
    return (records[i:i+10] for i in range(0, len(records), 10))

def send_to_airtable(upload_choice, csv_filepath, update_progress, should_stop):
    if not should_continue(should_stop, lambda msg: update_progress(None, msg), "Upload to Airtable stopped by user."):
        return
    if upload_choice == 1:
        update_progress(None, "Uploading data to Airtable...")
        records_batches = process_csv_for_airtable(csv_filepath)
        headers = {
            'Authorization': f'Bearer {config_manager.get_warehouse_var("airtable_api_key")}',
            'Content-Type': 'application/json'
        }
        upload_to_airtable(records_batches, headers, csv_filepath, lambda msg: update_progress(None, msg), should_stop)
    else:
        update_progress(None, "Upload to Airtable skipped.")

def void_unpaid_transactions(page, report_url, update_progress, should_stop, timeout=1000, max_retries=5):
    update_progress(60, "Starting the voiding process for unpaid transactions...")
    start_time = time.time()
    count = 0
    retries = 0

    while not should_stop.is_set():
        if time.time() - start_time > timeout:
            update_progress(75, "Timeout reached, stopping voiding process.")
            break

        if retries >= max_retries:
            update_progress(75, "Maximum retries reached, stopping voiding process.")
            break

        try:
            handle_network_error(page, report_url, lambda msg: update_progress(65, msg))
            if are_transactions_voided(page):
                update_progress(75, f"All {count} unpaid transactions have been voided.")
                break
            void_transaction(page)
            count += 1
            update_progress(65 + min(count, 10), f"Voided {count} transactions...")
            retries = 0  # Reset retries after successful operation

        except Exception as e:
            handle_retry(page, report_url, e, retries, lambda msg: update_progress(65, msg))
            retries += 1

    update_progress(75, f"Voiding process completed. Total transactions voided: {count}")

def handle_network_error(page, url, update_progress):
    if page.locator("#main-frame-error").count() > 0:
        update_progress("Network error detected. Reloading the page...")
        page.goto(url)
        page.wait_for_selector("#Time", state="visible", timeout=10000)
        time.sleep(2)
        update_progress("Voiding Unpaid Transactions...")

def are_transactions_voided(page):
    return page.locator(".panel-body .no-history").count() > 0

def void_transaction(page):
    page.click("#ReportResults > div:nth-child(2) > div:nth-child(6) > a")
    time.sleep(2)
    page.click(".modal .btn.btn-danger")
    page.wait_for_selector(".modal.bootstrap-dialog.type-danger", state="hidden")
    time.sleep(2)

def handle_retry(page, url, exception, retries, update_progress):
    update_progress(f"Error during voiding process: {exception}. Retrying...")
    time.sleep(min(2 ** retries, 60))
    page.goto(url)
    update_progress("Voiding Unpaid Transactions...")

def check_date(page):
    date_element = page.locator("#ReportResults > div:nth-child(1) > div:nth-child(1)")
    date_str = date_element.inner_text().strip()
    date_str = re.search(r'\d{2}/\d{2}/\d{4}', date_str).group()
    extracted_date = datetime.strptime(date_str, '%m/%d/%Y')
    today = datetime.today()
    delta_days = (today - extracted_date).days
    return delta_days < 4

def verify_base_url(page, base_url, update_progress):
    try:
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        update_progress(None, f"Base URL accessible: {page.url}")
        return True
    except Exception as e:
        update_progress(None, f"Error accessing base URL: {str(e)}")
        return False

def start_playwright_process(event_id, upload_choice, update_progress, should_stop, callback):
    csv_filepath = None
    login_url = config_manager.get_global_var('website_login_url')
    bid_home_page = config_manager.get_global_var('bid_home_page')
    report_url = f"{bid_home_page}/Account/EventSalesTransactionReport?EventID={event_id}&page=0&sort=DateTime&descending=True&dateStart=&dateEnd=&lotNumber=&description=&priceLow=&priceHigh=&quantity=&totalPriceLow=&totalPriceHigh=&invoiceID=&payer=&firstName=&lastName=&isPaid=2"
    print("Report URL: " + report_url)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            update_progress(5, "Initializing browser...")
            page.goto(login_url)

            username = config_manager.get_warehouse_var("bid_username")
            password = config_manager.get_warehouse_var("bid_password")

            if username is None or password is None:
                update_progress(15, "Failed to retrieve login credentials from config.")
                return

            update_progress(20, "Attempting login...")
            login_success = login(page, username, password, update_progress, should_stop)

            if not login_success:
                update_progress(40, "Login failed. Aborting process.")
                return

            update_progress(45, "Login successful. Current URL: " + page.url)
            
            page.wait_for_load_state("networkidle")

            update_progress(50, "Navigating to report page...")
            page.goto(report_url)
            
            try:
                page.wait_for_selector("#ReportResults", state="visible", timeout=30000)
                update_progress(60, f"Report page loaded. Current URL: {page.url}")
            except:
                update_progress(60, f"Timeout waiting for report page. Current URL: {page.url}")
                
                if "Account/LogOn" in page.url:
                    update_progress(65, "Redirected to login page. Session might have expired. Attempting to log in again...")
                    login_success = login(page, username, password, update_progress, should_stop)
                    if not login_success:
                        update_progress(70, "Login failed. Aborting process.")
                        return
                    
                    update_progress(75, "Navigating to report page after re-login...")
                    page.goto(report_url)
                    
                    try:
                        page.wait_for_selector("#ReportResults", state="visible", timeout=30000)
                        update_progress(80, f"Report page loaded after re-login. Current URL: {page.url}")
                    except:
                        update_progress(80, f"Failed to load report page after re-login. Current URL: {page.url}")
                        return

            if not check_login_status(page):
                update_progress(85, "Not logged in on report page. Aborting process.")
                return

            update_progress(90, "Starting to void unpaid transactions...")
            void_unpaid_transactions(page, report_url, update_progress, should_stop)

            update_progress(95, "Exporting CSV...")
            csv_filepath = export_csv(page, event_id, update_progress, should_stop)

            if csv_filepath:
                update_progress(97, "CSV exported successfully. Uploading to Airtable...")
                send_to_airtable(upload_choice, csv_filepath, update_progress, should_stop)
            else:
                update_progress(97, "CSV filepath not set due to an error. Skipping Upload to Airtable.")

        except Exception as e:
            update_progress(98, f"An error occurred: {str(e)}")
            should_stop.set()
        finally:
            browser.close()
            if csv_filepath:
                update_progress(99, f"Process completed. CSV Filepath: {csv_filepath}")
            else:
                update_progress(99, "Process completed, but CSV filepath was not set.")
            callback()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Void unpaid transactions")
    parser.add_argument("auction_id", help="Auction ID")
    parser.add_argument("upload_choice", type=int, choices=[0, 1], help="Upload choice (0: No upload, 1: Upload to Airtable)")
    parser.add_argument("warehouse", help="Warehouse name")
    
    args = parser.parse_args()

    void_unpaid_main(args.auction_id, args.upload_choice, args.warehouse, lambda progress, message: print(f"Progress: {progress}%, Message: {message}"))