import os
import threading
import re
import time
import csv
import requests
import json
import traceback
from selenium import webdriver
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
from auction.utils import config_manager
from auction.utils.progress_tracker import ProgressTracker, run_with_progress

config_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'config.json')

def get_resources_dir(folder):
    # Get the directory of the current script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Navigate up to the project root (two levels up from the script)
    project_root = os.path.dirname(os.path.dirname(script_dir))
    
    # Construct the path to the resources directory
    resources_dir = os.path.join(project_root, 'auction', 'resources', folder)
    
    # Ensure the directory exists
    os.makedirs(resources_dir, exist_ok=True)
    
    return resources_dir

# Update the DOWNLOAD_DIR constant
DOWNLOAD_DIR = get_resources_dir('voided_csv')
AIRTABLE_URL = lambda base_id, table_id: f'https://api.airtable.com/v0/{base_id}/{table_id}'

@run_with_progress
def void_unpaid_main(auction_id, upload_choice, show_browser, warehouse, update_progress):
    config_manager.load_config(config_path)
    config_manager.set_active_warehouse(warehouse)
    
    should_stop = threading.Event()

    def callback():
        update_progress(100, "Void unpaid process completed.")

    start_selenium_process(auction_id, upload_choice, update_progress, should_stop, callback, show_browser)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Void unpaid transactions")
    parser.add_argument("auction_id", help="Auction ID")
    parser.add_argument("upload_choice", type=int, choices=[0, 1], help="Upload choice (0: No upload, 1: Upload to Airtable)")
    parser.add_argument("show_browser", type=int, choices=[0, 1], help="Show browser (0: Headless, 1: Show browser)")
    parser.add_argument("warehouse", help="Warehouse name")
    
    args = parser.parse_args()

    void_unpaid_main(args.auction_id, args.upload_choice, bool(args.show_browser), args.warehouse)

def configure_driver(url, show_browser):
    firefox_options = Options()
    firefox_profile = webdriver.FirefoxProfile()
    firefox_profile.set_preference("browser.download.folderList", 2)
    firefox_profile.set_preference("browser.download.manager.showWhenStarting", False)
    firefox_profile.set_preference("browser.download.dir", DOWNLOAD_DIR)
    firefox_profile.set_preference("browser.helperApps.neverAsk.saveToDisk", "text/csv")
    firefox_profile.set_preference("browser.download.useDownloadDir", True)

    firefox_options.profile = firefox_profile
    if not show_browser:
        firefox_options.add_argument("--headless")
        firefox_options.add_argument("--window-size=1920x1080")

    driver_path = config_manager.get_global_var('webdriver_path')
    if driver_path == "auto" or not driver_path:
        driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=firefox_options)
    else:
        driver = webdriver.Firefox(service=FirefoxService(driver_path), options=firefox_options)
    return driver

def login(driver, username, password, update_progress, should_stop):
    if not should_continue(should_stop, lambda msg: update_progress(None, msg), "Login operation stopped by user."):
        return False

    update_progress(None, "Logging In...")
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        update_progress(None, "Waiting for username field to be present...")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "username")))
        
        update_progress(None, "Entering username...")
        username_field = driver.find_element(By.ID, "username")
        username_field.clear()
        username_field.send_keys(username)
        
        update_progress(None, "Entering password...")
        password_field = driver.find_element(By.ID, "password")
        password_field.clear()
        password_field.send_keys(password)

        if not should_continue(should_stop, lambda msg: update_progress(None, msg), "Login operation stopped before finalizing."):
            return False

        update_progress(None, "Submitting login form...")
        password_field.send_keys(Keys.RETURN)
        
        update_progress(None, "Waiting for login to complete...")
        WebDriverWait(driver, 30).until(EC.url_contains("EventSalesTransactionReport"))
        update_progress(None, "Login successful.")
        return True
    except Exception as e:
        update_progress(None, f"Login failed: {str(e)}")
        return False

def handle_captcha(driver, update_progress):
    # Check if a captcha is present
    try:
        captcha_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "captcha-container"))  # Adjust the locator as needed
        )
        update_progress(None, "Captcha detected. Please solve it manually.")
        # Wait for the captcha to be solved (you might need to implement a way for the user to indicate they've solved it)
        WebDriverWait(driver, 300).until_not(
            EC.presence_of_element_located((By.ID, "captcha-container"))
        )
        update_progress(None, "Captcha solved. Continuing with login.")
        return True
    except TimeoutException:
        # No captcha found, or it wasn't solved in time
        return False

# Add this function to your script
def check_login_status(driver):
    try:
        # Check for elements that are typically present after a successful login
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "logoutForm"))
        )
        return True
    except TimeoutException:
        return False

def should_continue(should_stop, gui_callback, message):
    if should_stop.is_set():
        gui_callback(message)
        return False
    return True

def export_csv(driver, event_id, update_progress, should_stop):
    if not should_continue(should_stop, lambda msg: update_progress(None, msg), "CSV export operation stopped by user."):
        return None

    update_progress(None, "Exporting CSV...")
    filename = f"SalesTransactions_Event_{event_id}.csv"
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(file_path):
        update_progress(None, f"File {filename} already exists. Skipping download.")
        return file_path

    try:
        export_csv_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "ExportCSV")))
        export_csv_button.click()

        if not should_continue(should_stop, lambda msg: update_progress(None, msg), "CSV export operation stopped during download."):
            return None
        return wait_for_download(filename, file_path, lambda msg: update_progress(None, msg))
    except Exception as e:
        update_progress(None, f"Error exporting CSV: {str(e)}")
        return None

def wait_for_download(filename, file_path, update_progress, timeout=60):
    start_time = time.time()
    while not os.path.exists(file_path):
        if time.time() - start_time > timeout:
            error_message = f"Timed out waiting for {filename} to download."
            update_progress(None, error_message)
            raise TimeoutException(error_message)
        time.sleep(3)
    update_progress(None, f"File {filename} downloaded successfully.")
    return file_path

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

def retry_operation(driver, operation, operation_description, url, max_retries, update_progress, should_stop, initial_sleep=5, max_sleep=60):
    retries = 0
    sleep_time = initial_sleep

    while retries < max_retries:
        if should_stop.is_set():
            update_progress(None, f"{operation_description} operation stopped by user.")
            return None
        try:
            return operation()
        except TimeoutException:
            handle_timeout_exception(retries, max_retries, operation_description, sleep_time, driver, url, lambda msg: update_progress(None, msg))
        except WebDriverException as e:
            handle_webdriver_exception(retries, max_retries, operation_description, sleep_time, driver, url, e, lambda msg: update_progress(None, msg))

        sleep_time = update_sleep_time(sleep_time, max_sleep)
        retries += 1

    failure_message = f"{operation_description} operation failed after {max_retries} attempts."
    update_progress(None, failure_message)
    return None

def handle_timeout_exception(retries, max_retries, operation_description, sleep_time, driver, url, update_progress):
    update_progress(f"Timeout waiting for element during {operation_description}. Retrying after {sleep_time} seconds...")
    time.sleep(sleep_time)
    if retries < max_retries - 1:
        driver.get(url)

def handle_webdriver_exception(retries, max_retries, operation_description, sleep_time, driver, url, exception, update_progress):
    update_progress(f"Attempt {retries + 1}/{max_retries} for {operation_description} - WebDriverException detected: {exception}. Reloading and retrying...")
    time.sleep(sleep_time)
    if retries < max_retries - 1:
        reload_page_on_network_error(driver, url)

def reload_page_on_network_error(driver, url):
    try:
        network_error = driver.find_elements(By.ID, "main-frame-error")
        if network_error:
            driver.get(url)
            WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "Time")))
    except NoSuchElementException:
        pass

def update_sleep_time(current_sleep_time, max_sleep_time):
    return min(current_sleep_time * 2, max_sleep_time)

def void_unpaid_transactions(driver, url, update_progress, should_stop, timeout=1000, max_retries=5):
    update_progress(None, "Starting the voiding process for unpaid transactions...")
    start_time = time.time()
    count = 0
    retries = 0

    while not should_stop.is_set():
        if has_timed_out(start_time, timeout):
            update_progress(None, "Timeout reached, stopping voiding process.")
            break

        if retries >= max_retries:
            update_progress(None, "Maximum retries reached, stopping voiding process.")
            break

        try:
            handle_network_error(driver, url, lambda msg: update_progress(None, msg))
            if are_transactions_voided(driver):
                update_progress(None, f"All {count} unpaid transactions have been voided.")
                break
            void_transaction(driver)
            count += 1
            update_progress(None, f"Voided {count} transactions...")

        except (NoSuchElementException, TimeoutException) as e:
            handle_retry(driver, url, e, retries, lambda msg: update_progress(None, msg))
            retries += 1

        except Exception as e:
            update_progress(None, f"Unexpected error during voiding: {e}")
            break

def has_timed_out(start_time, timeout):
    return time.time() - start_time > timeout

def handle_network_error(driver, url, update_progress):
    network_error = driver.find_elements(By.ID, "main-frame-error")
    if network_error:
        update_progress("Network error detected. Reloading the page...")
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "Time")))
        time.sleep(2)
        update_progress("Voiding Unpaid Transactions...")

def are_transactions_voided(driver):
    no_results_elements = driver.find_elements(By.CSS_SELECTOR, ".panel-body .no-history")
    return bool(no_results_elements)

def void_transaction(driver):
    void_sale_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "#ReportResults > div:nth-child(2) > div:nth-child(6) > a"))
    )
    void_sale_button.click()
    time.sleep(2)
    popup_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, ".modal .btn.btn-danger"))
    )
    driver.execute_script("arguments[0].click();", popup_button)
    WebDriverWait(driver, 10).until(
        EC.invisibility_of_element_located((By.CSS_SELECTOR, ".modal.bootstrap-dialog.type-danger")))
    time.sleep(2)

def handle_retry(driver, url, exception, retries, update_progress):
    update_progress(f"Error during voiding process: {exception}. Retrying...")
    time.sleep(min(2 ** retries, 60))
    driver.get(url)
    update_progress("Voiding Unpaid Transactions...")
    
def should_continue(should_stop, gui_callback, message):
    if should_stop.is_set():
        gui_callback(message)
        return False
    return True

def check_date(driver):
    date_element = driver.find_element(By.CSS_SELECTOR, "#ReportResults > div:nth-child(1) > div:nth-child(1)")
    date_str = date_element.text.strip()
    date_str = re.search(r'\d{2}/\d{2}/\d{4}', date_str).group()
    extracted_date = datetime.strptime(date_str, '%m/%d/%Y')
    today = datetime.today()
    delta_days = (today - extracted_date).days
    return delta_days < 4

def verify_base_url(driver, base_url, gui_callback):
    try:
        driver.get(base_url)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        gui_callback(f"Base URL accessible: {driver.current_url}")
        return True
    except Exception as e:
        gui_callback(f"Error accessing base URL: {str(e)}")
        return False

def start_selenium_process(event_id, upload_choice, update_progress, should_stop, callback, show_browser):
    csv_filepath = None
    bid_url = config_manager.get_global_var('bid_url')
    login_url = f"{bid_url}/Account/LogOn"
    report_url = f"{bid_url}/Account/EventSalesTransactionReport?EventID={event_id}&page=0&sort=DateTime&descending=True&dateStart=&dateEnd=&lotNumber=&description=&priceLow=&priceHigh=&quantity=&totalPriceLow=&totalPriceHigh=&invoiceID=&payer=&firstName=&lastName=&isPaid=2"
    
    update_progress(5, "Initializing browser...")
    driver = configure_driver(login_url, show_browser)

    try:
        update_progress(10, "Navigating to login page...")
        driver.get(login_url)

        username = config_manager.get_warehouse_var("bid_username")
        password = config_manager.get_warehouse_var("bid_password")

        if username is None or password is None:
            update_progress(15, "Failed to retrieve login credentials from config.")
            return

        update_progress(20, "Attempting login...")
        login_success = retry_operation(
            driver, 
            lambda: login(driver, username, password, lambda msg: update_progress(None, msg), should_stop),
            "Login",
            login_url,
            3,
            lambda msg: update_progress(None, msg),
            should_stop
        )

        if not login_success:
            update_progress(25, "Login failed after multiple attempts. Aborting process.")
            return

        update_progress(30, "Navigating to report page...")
        driver.get(report_url)

        update_progress(40, "Exporting CSV...")
        csv_filepath = retry_operation(
            driver,
            lambda: export_csv(driver, event_id, lambda msg: update_progress(None, msg), should_stop),
            "CSV Download",
            report_url,
            3,
            lambda msg: update_progress(None, msg),
            should_stop
        )

        if csv_filepath:
            update_progress(60, "CSV exported successfully. Uploading to Airtable...")
            send_to_airtable(upload_choice, csv_filepath, lambda msg: update_progress(None, msg), should_stop)
        else:
            update_progress(60, "CSV filepath not set due to an error. Skipping Upload to Airtable.")

        update_progress(80, "Starting to void unpaid transactions...")
        void_unpaid_transactions(driver, report_url, lambda msg: update_progress(None, msg), should_stop)

    except Exception as e:
        update_progress(95, f"An error occurred: {str(e)}")
        should_stop.set()
    finally:
        if driver:
            driver.quit()
        if csv_filepath:
            update_progress(98, f"Process completed. CSV Filepath: {csv_filepath}")
        else:
            update_progress(98, "Process completed, but CSV filepath was not set.")
        callback()

def run_void_unpaid_on_bid(auction_id, upload_choice, gui_callback, should_stop, callback, show_browser):
    config_manager.load_config(config_path)
    warehouse = "Maule Warehouse"  # You can modify this to dynamically select the warehouse
    config_manager.set_active_warehouse(warehouse)
    
    start_selenium_process(auction_id, upload_choice, gui_callback, should_stop, callback, show_browser)

if __name__ == "__main__":
    args = parser.parse_args()
    void_unpaid_main(args.auction_id, args.upload_choice, args.show_browser, args.warehouse, lambda progress, message: print(f"Progress: {progress}%, Message: {message}"))
