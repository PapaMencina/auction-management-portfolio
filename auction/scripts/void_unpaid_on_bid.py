import os
import re
import time
import csv
import requests
import json
from selenium import webdriver
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
from tkinter import messagebox
from auction.utils import config_manager

def get_resources_dir(folder):
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Use abspath to ensure full path
    return os.path.join(base_path, 'resources', folder)

# Constants
DOWNLOAD_DIR = get_resources_dir('voided_csv')  # Use the home directory's download path
AIRTABLE_URL = lambda base_id, table_id: f'https://api.airtable.com/v0/{base_id}/{table_id}'

def void_unpaid_main(auction_id, upload_choice, show_browser):
    def gui_callback(message):
        print(message)

    def should_stop():
        return False

    def callback():
        print("Void unpaid process completed.")

    start_selenium_process(auction_id, upload_choice, gui_callback, should_stop, callback, show_browser)

if __name__ == "__main__":
    void_unpaid_main("sample_auction_id", 1, True)

def configure_driver(url, show_browser):
    '''Configure for Firefox'''
    download_dir = get_resources_dir('voided_csv')  # Make sure this returns the absolute path

    firefox_options = Options()
    firefox_profile = webdriver.FirefoxProfile()
    firefox_profile.set_preference("browser.download.folderList", 2)
    firefox_profile.set_preference("browser.download.manager.showWhenStarting", False)
    firefox_profile.set_preference("browser.download.dir", download_dir)
    firefox_profile.set_preference("browser.helperApps.neverAsk.saveToDisk", "text/csv")
    firefox_profile.set_preference("browser.download.useDownloadDir", True)  # Ensure Firefox uses the specified directory

    # Set the Firefox profile in the options
    firefox_options.profile = firefox_profile
    if show_browser == 0:    
        firefox_options.add_argument("--headless")  # Run in headless mode
        firefox_options.add_argument("--window-size=1920x1080")

    # Create a Firefox driver
    driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=firefox_options)

    # Open the URL
    driver.get(url)

    return driver

def login(driver, username, password, gui_callback, should_stop):
    """Logs into the website using the provided credentials."""
    if not should_continue(should_stop, gui_callback, "Login operation stopped by user."):
        return

    gui_callback("Logging In...")
    time.sleep(1)
    username_field = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "username")))
    username_field.send_keys(username)
    
    time.sleep(1)
    password_field = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "password")))
    password_field.send_keys(password)

    time.sleep(2)
    if not should_continue(should_stop, gui_callback, "Login operation stopped before finalizing."):
        return

    password_field.send_keys(Keys.RETURN)

def export_csv(driver, event_id, gui_callback, should_stop):
    """Clicks the button to export a CSV file and waits for the download to complete."""
    if not should_continue(should_stop, gui_callback, "CSV export operation stopped by user."):
        return None

    gui_callback("Exporting CSV...")
    filename = f"SalesTransactions_Event_{event_id}.csv"
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(file_path):
        gui_callback(f"File {filename} already exists. Skipping download.")
        return file_path

    try:
        export_csv_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "ExportCSV")))
        export_csv_button.click()

        if not should_continue(should_stop, gui_callback, "CSV export operation stopped during download."):
            return None
        return wait_for_download(filename, file_path, gui_callback)
    except TimeoutException as e:
        gui_callback(f"Error: {e}")
        return None

def wait_for_download(filename, file_path, gui_callback, timeout=60):
    """Waits for a file to be downloaded."""
    start_time = time.time()
    while not os.path.exists(file_path):
        if time.time() - start_time > timeout:
            error_message = f"Timed out waiting for {filename} to download."
            gui_callback(error_message)
            raise TimeoutException(error_message)  # It will stop execution unless caught by an outer try-except block
        time.sleep(3)  # Sleep briefly to avoid busy-waiting
    gui_callback(f"File {filename} downloaded successfully.")
    return file_path

def upload_to_airtable(records_batches, headers, csv_filepath, gui_callback, should_stop):
    """Sends data to Airtable in batches."""
    all_batches_successful = True  # Flag to track overall success

    for batch in records_batches:
        if not should_continue(should_stop, gui_callback, "Upload to Airtable stopped by user."):
            return
        response = requests.post(AIRTABLE_URL(config_manager.get_global_var('airtable_sales_base_id'),
                                              config_manager.get_global_var('airtable_cancels_table_id')),
                                 json={"records": batch}, headers=headers)
        if response.status_code != 200:
            error_message = f"Failed to send data to Airtable: {response.status_code} {response.text}"
            gui_callback(error_message)            
            gui_callback(f"Upload CSV Manually. CSV Filepath: {csv_filepath}")
            all_batches_successful = False
            break

    if all_batches_successful:
        gui_callback("Successfully Uploaded to Airtable")

def process_csv_for_airtable(csv_filepath):
    """Processes CSV data for uploading to Airtable."""
    with open(csv_filepath, newline='', encoding='utf-8') as csvfile:  # Specify UTF-8 encoding
        reader = csv.DictReader(csvfile)
        records = [{"fields": record} for record in reader] 
    return (records[i:i+10] for i in range(0, len(records), 10))

# Ask the user if they want to upload to Airtable
def send_to_airtable(upload_choice, csv_filepath, gui_callback, should_stop):
    if not should_continue(should_stop, gui_callback, "Upload to Airtable stopped by user."):
        return
    if upload_choice == 1:
        gui_callback("Uploading data to Airtable...")

        # Process the CSV for Airtable
        records_batches = process_csv_for_airtable(csv_filepath)
        headers = {
            'Authorization': f'Bearer {config_manager.get_global_var("airtable_api_key")}',
            'Content-Type': 'application/json'
        }
        # Send data to Airtable
        upload_to_airtable(records_batches, headers, csv_filepath, gui_callback, should_stop)
    else:
        gui_callback("Upload to Airtable skipped.")

def retry_operation(driver, operation, operation_description, url, max_retries, gui_callback, should_stop, initial_sleep=5, max_sleep=60):
    """
    Retries a Selenium operation with exponential backoff, reloading the URL on failure.
    
    :param driver: Selenium WebDriver instance
    :param operation: Callable operation to retry
    :param operation_description: Description of the operation for logging
    :param url: URL to reload on failure
    :param max_retries: Maximum number of retries
    :param initial_sleep: Initial sleep time before retrying
    :param max_sleep: Maximum sleep time between retries
    :return: Result of the operation if successful
    :raises: Exception if the operation fails after maximum retries
    """
    retries = 0
    sleep_time = initial_sleep

    while retries < max_retries:
        if should_stop.is_set():
            gui_callback(f"{operation_description} operation stopped by user.")
            return None
        try:
            return operation()
        except TimeoutException:
            handle_timeout_exception(retries, max_retries, operation_description, sleep_time, driver, url, gui_callback)
        except WebDriverException as e:
            handle_webdriver_exception(retries, max_retries, operation_description, sleep_time, driver, url, e, gui_callback)

        sleep_time = update_sleep_time(sleep_time, max_sleep)
        retries += 1

    failure_message = f"{operation_description} operation failed after {max_retries} attempts."
    gui_callback(failure_message)
    return None

def handle_timeout_exception(retries, max_retries, operation_description, sleep_time, driver, url, gui_callback):
    """Handles TimeoutException during retries."""
    gui_callback(f"Timeout waiting for element during {operation_description}. Retrying after {sleep_time} seconds...")
    time.sleep(sleep_time)
    if retries < max_retries - 1:
        driver.get(url)  # Reload the URL for a fresh start

def handle_webdriver_exception(retries, max_retries, operation_description, sleep_time, driver, url, exception, gui_callback):
    """Handles WebDriverException during retries."""
    gui_callback(f"Attempt {retries + 1}/{max_retries} for {operation_description} - WebDriverException detected: {exception}. Reloading and retrying...")
    time.sleep(sleep_time)
    if retries < max_retries - 1:
        reload_page_on_network_error(driver, url)

def reload_page_on_network_error(driver, url):
    """Reloads the page on network error."""
    try:
        network_error = driver.find_elements(By.ID, "main-frame-error")
        if network_error:
            driver.get(url)
            WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "Time")))
    except NoSuchElementException:
        pass  # If no element is found, continue with the normal flow

def update_sleep_time(current_sleep_time, max_sleep_time):
    """Updates the sleep time for exponential backoff."""
    return min(current_sleep_time * 2, max_sleep_time)

def void_unpaid_transactions(driver, url, gui_callback, should_stop, timeout=1000, max_retries=5):
    gui_callback("Starting the voiding process for unpaid transactions...")
    start_time = time.time()
    count = 0
    retries = 0

    while not should_stop.is_set():
        if has_timed_out(start_time, timeout):
            gui_callback("Timeout reached, stopping voiding process.")
            break

        if retries >= max_retries:
            gui_callback("Maximum retries reached, stopping voiding process.")
            break

        try:
            handle_network_error(driver, url, gui_callback)
            if are_transactions_voided(driver):
                gui_callback(f"All {count} unpaid transactions have been voided.")
                break
            void_transaction(driver)
            count += 1

        except (NoSuchElementException, TimeoutException) as e:
            handle_retry(driver, url, e, retries, gui_callback)
            retries += 1

        except Exception as e:
            gui_callback(f"Unexpected error during voiding: {e}")
            break

def has_timed_out(start_time, timeout):
    return time.time() - start_time > timeout

def handle_network_error(driver, url, gui_callback):
    network_error = driver.find_elements(By.ID, "main-frame-error")
    if network_error:
        gui_callback("Network error detected. Reloading the page...")
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "Time")))
        time.sleep(2)
        gui_callback("Voiding Unpaid Transactions...")

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

def handle_retry(driver, url, exception, retries, gui_callback):
    gui_callback(f"Error during voiding process: {exception}. Retrying...")
    time.sleep(min(2 ** retries, 60))
    driver.get(url)
    gui_callback("Voiding Unpaid Transactions...")
    
def should_continue(should_stop, gui_callback, message):
    if should_stop.is_set():
        gui_callback(message)
        return False
    return True

def check_date(driver):
    # Find the element and extract the date text
    date_element = driver.find_element(By.CSS_SELECTOR, "#ReportResults > div:nth-child(1) > div:nth-child(1)")
    date_str = date_element.text.strip()
    date_str = re.search(r'\d{2}/\d{2}/\d{4}', date_str).group()
    # Convert the date string to a datetime object
    extracted_date = datetime.strptime(date_str, '%m/%d/%Y')
    today = datetime.today()
    delta_days = (today - extracted_date).days
    if delta_days < 4:
        return True
    else:
        return False

def start_selenium_process(event_id, upload_choice, gui_callback, should_stop, callback, show_browser):
    csv_filepath = None
    original_url = f"https://bid.702auctions.com/Account/EventSalesTransactionReport?EventID={event_id}&page=0&sort=DateTime&descending=True&dateStart=&dateEnd=&lotNumber=&description=&priceLow=&priceHigh=&quantity=&totalPriceLow=&totalPriceHigh=&invoiceID=&payer=&firstName=&lastName=&isPaid=2"
    gui_callback("Loading Bid...")
    driver = configure_driver(original_url, show_browser)

    try:
        if not should_continue(should_stop, gui_callback, "Operation stopped before login."):
            return

        # Use the correct bid credentials
        username = config_manager.get_global_var("bid_username")
        password = config_manager.get_global_var("bid_password")

        retry_operation(driver, lambda: login(driver, username, password, gui_callback, should_stop), "Login", original_url, 3, gui_callback, should_stop)

        if not should_continue(should_stop, gui_callback, "Operation stopped before CSV export."):
            return

        csv_filepath = retry_operation(driver, lambda: export_csv(driver, event_id, gui_callback, should_stop), "CSV Download", original_url, 3, gui_callback, should_stop)

        if csv_filepath and not should_continue(should_stop, gui_callback, "Operation stopped before uploading to Airtable."):
            return

        if csv_filepath:
            send_to_airtable(upload_choice, csv_filepath, gui_callback, should_stop)
        else:
            gui_callback("CSV filepath not set due to an error. Skipping Upload to Airtable.")

        if not should_continue(should_stop, gui_callback, "Operation stopped before checking date."):
            return

        void_unpaid_transactions(driver, original_url, gui_callback, should_stop)

    except Exception as e:
        gui_callback(f"An error occurred: {e}")
        should_stop.set()
    finally:
        if driver:
            driver.quit()
        if csv_filepath:
            gui_callback(f"CSV Filepath: {csv_filepath}")
        else:
            gui_callback("CSV filepath not set due to an error.")
        callback()  # Invoke the callback to re-enable the button

def run_void_unpaid_on_bid(auction_id, upload_choice, gui_callback, should_stop, callback, show_browser):
    username = config_manager.get_global_var("bid_username")
    password = config_manager.get_global_var("bid_password")
    start_selenium_process(auction_id, upload_choice, gui_callback, should_stop, callback, show_browser)