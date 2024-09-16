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

config_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'config.json')

def get_resources_dir(folder):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    resources_dir = os.path.join(project_root, 'auction', 'resources', folder)
    os.makedirs(resources_dir, exist_ok=True)
    return resources_dir

DOWNLOAD_DIR = get_resources_dir('voided_csv')
AIRTABLE_URL = lambda base_id, table_id: f'https://api.airtable.com/v0/{base_id}/{table_id}'

def void_unpaid_main(auction_id, upload_choice, warehouse):
    config_manager.load_config(config_path)
    config_manager.set_active_warehouse(warehouse)
    
    should_stop = threading.Event()

    start_playwright_process(auction_id, upload_choice, should_stop)

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
        print("CSV export operation stopped by user.")
        return None

    print("Exporting CSV...")
    filename = f"SalesTransactions_Event_{event_id}.csv"
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(file_path):
        print(f"File {filename} already exists. Skipping download.")
        return file_path

    try:
        with page.expect_download() as download_info:
            page.click("#ExportCSV")
        download = download_info.value
        download.save_as(file_path)

        if should_stop.is_set():
            print("CSV export operation stopped during download.")
            return None
        return file_path
    except Exception as e:
        print(f"Error exporting CSV: {str(e)}")
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

def process_csv_for_airtable(csv_filepath):
    with open(csv_filepath, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        records = [{"fields": record} for record in reader] 
    return (records[i:i+10] for i in range(0, len(records), 10))

def send_to_airtable(upload_choice, csv_filepath, should_stop):
    if should_stop.is_set():
        print("Upload to Airtable stopped by user.")
        return
    if upload_choice == 1:
        print("Uploading data to Airtable...")
        records_batches = process_csv_for_airtable(csv_filepath)
        headers = {
            'Authorization': f'Bearer {config_manager.get_warehouse_var("airtable_api_key")}',
            'Content-Type': 'application/json'
        }
        upload_to_airtable(records_batches, headers, csv_filepath, should_stop)
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
    csv_filepath = None
    login_url = config_manager.get_global_var('website_login_url')
    bid_home_page = config_manager.get_global_var('bid_home_page')
    report_url = f"{bid_home_page}/Account/EventSalesTransactionReport?EventID={event_id}&page=0&sort=DateTime&descending=True&dateStart=&dateEnd=&lotNumber=&description=&priceLow=&priceHigh=&quantity=&totalPriceLow=&totalPriceHigh=&invoiceID=&payer=&firstName=&lastName=&isPaid=2"
    print("Report URL: " + report_url)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = browser.new_context()
        page = context.new_page()
        
        try:
            print("Initializing browser...")
            page.goto(login_url)

            username = config_manager.get_warehouse_var("bid_username")
            password = config_manager.get_warehouse_var("bid_password")

            if username is None or password is None:
                print("Failed to retrieve login credentials from config.")
                return

            print("Attempting login...")
            login_success = login(page, username, password)

            if not login_success:
                print("Login failed. Aborting process.")
                return

            print("Login successful. Current URL: " + page.url)
            
            page.wait_for_load_state("networkidle")

            print("Navigating to report page...")
            page.goto(report_url)
            
            try:
                page.wait_for_selector("#ReportResults", state="visible", timeout=30000)
                print(f"Report page loaded. Current URL: {page.url}")
            except:
                print(f"Timeout waiting for report page. Current URL: {page.url}")
                
                if "Account/LogOn" in page.url:
                    print("Redirected to login page. Session might have expired. Attempting to log in again...")
                    login_success = login(page, username, password)
                    if not login_success:
                        print("Login failed. Aborting process.")
                        return
                    
                    print("Navigating to report page after re-login...")
                    page.goto(report_url)
                    
                    try:
                        page.wait_for_selector("#ReportResults", state="visible", timeout=30000)
                        print(f"Report page loaded after re-login. Current URL: {page.url}")
                    except:
                        print(f"Failed to load report page after re-login. Current URL: {page.url}")
                        return

            if not check_login_status(page):
                print("Not logged in on report page. Aborting process.")
                return

            print("Starting to void unpaid transactions...")
            void_unpaid_transactions(page, report_url, should_stop)

            print("Exporting CSV...")
            csv_filepath = export_csv(page, event_id, should_stop)

            if csv_filepath:
                print("CSV exported successfully. Uploading to Airtable...")
                send_to_airtable(upload_choice, csv_filepath, should_stop)
            else:
                print("CSV filepath not set due to an error. Skipping Upload to Airtable.")

        except Exception as e:
            print(f"An error occurred: {str(e)}")
            should_stop.set()
        finally:
            browser.close()
            if csv_filepath:
                print(f"Process completed. CSV Filepath: {csv_filepath}")
            else:
                print("Process completed, but CSV filepath was not set.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Void unpaid transactions")
    parser.add_argument("auction_id", help="Auction ID")
    parser.add_argument("upload_choice", type=int, choices=[0, 1], help="Upload choice (0: No upload, 1: Upload to Airtable)")
    parser.add_argument("warehouse", help="Warehouse name")
    
    args = parser.parse_args()

    void_unpaid_main(args.auction_id, args.upload_choice, args.warehouse)