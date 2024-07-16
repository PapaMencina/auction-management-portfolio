import os
import time
import json
import re
import traceback
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from webdriver_manager.firefox import GeckoDriverManager

def get_resources_dir():
    """Navigate up one directory from the script's location and then into the 'resources/event_images' folder."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'resources', 'event_images')

def wait_for_download(gui_callback, timeout=60):
    """Waits for a new file to appear in the resources directory and ensures the download is complete."""
    download_dir = get_resources_dir()
    initial_files = os.listdir(download_dir)
    start_time = time.time()

    while True:
        current_files = os.listdir(download_dir)
        new_files = set(current_files) - set(initial_files)

        if new_files:
            for file in new_files:
                if not file.endswith(".part"):
                    downloaded_file = os.path.join(download_dir, file)
                    gui_callback(f"Download complete: {downloaded_file}")
                    return downloaded_file

        elapsed_time = time.time() - start_time
        if elapsed_time > timeout:
            gui_callback("Download timed out.")
            return None

        if int(elapsed_time) % 5 == 0:
            gui_callback(f"Waiting for download... {int(elapsed_time)}s elapsed")

        time.sleep(1)

def configure_driver(url, show_browser):
    """Configures and returns a Firefox WebDriver."""
    firefox_options = FirefoxOptions()

    firefox_options.set_preference("browser.download.folderList", 2)
    firefox_options.set_preference("browser.download.manager.showWhenStarting", False)
    firefox_options.set_preference("browser.download.dir", get_resources_dir())
    firefox_options.set_preference("browser.helperApps.neverAsk.saveToDisk", "image/jpeg")

    if not show_browser:
        firefox_options.add_argument("--headless")
        firefox_options.add_argument("--window-size=1920x1080")

    driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=firefox_options)
    driver.get(url)
    return driver

def format_date(date_obj):
    """Formats a datetime object into 'December 2nd' and '12/02/2023' formats."""
    if 11 <= date_obj.day <= 13:
        suffix = "th"
    else:
        suffix = {"1": "st", "2": "nd", "3": "rd"}.get(str(date_obj.day)[-1], "th")
    
    month_day_str = date_obj.strftime("%B %d").replace(" 0", " ") + suffix
    full_date = date_obj.strftime('%m/%d/%Y')
    return month_day_str, full_date

def find_element(driver, locator, timeout=15):
    """Wait for an element to be present and return it."""
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))

def click_element(driver, locator, timeout=10):
    """Wait for an element to be clickable and then click it."""
    element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
    time.sleep(1)
    element.click()

def enter_text(driver, locator, text, clear_first=True, timeout=10):
    """Wait for a text field, clear it, and enter text."""
    element = find_element(driver, locator, timeout)
    if clear_first:
        element.clear()
    time.sleep(1)
    element.send_keys(text)

def login(driver, user_locator, pass_locator, username, password, url, gui_callback):
    """Logs in to the specified URL using provided credentials."""
    try:
        enter_text(driver, user_locator, username)
        enter_text(driver, pass_locator, password + Keys.RETURN)
    except Exception as e:
        gui_callback(f"Login failed: {e}")
        driver.get(url)

def set_content_in_ckeditor(driver, iframe_title, formatted_text):
    """Sets content in a CKEditor iframe."""
    iframe = find_element(driver, (By.XPATH, f"//iframe[@title='Rich Text Editor, {iframe_title}']"))
    driver.switch_to.frame(iframe)
    ckeditor_body = find_element(driver, (By.CSS_SELECTOR, "body[contenteditable='true']"))
    driver.execute_script(f"arguments[0].innerHTML = {json.dumps(formatted_text)};", ckeditor_body)
    driver.switch_to.default_content()

def element_value_is_not_empty(driver, element_id):
    """Checks if the value of an element is not empty."""
    element = driver.find_element(By.ID, element_id)
    return element.get_attribute('value') != ''

def get_image(driver, ending_date_input, relaythat_url, gui_callback, selected_warehouse):
    """Logs in to RelayThat, sets the end date, and downloads the event image."""
    try:
        gui_callback('Getting auction image...')
        login(driver, (By.ID, "user_email"), (By.ID, "user_password"), "rtl3wd@gmail.com", "##RelayThat702..", relaythat_url, gui_callback)
    except Exception as e:
        gui_callback(f"Error logging in: {e}")
        driver.quit()
        return

    try:
        if selected_warehouse == "Sunrise Warehouse":
            image_text = "OFFSITE"
        else:
            image_text = f"Ending {ending_date_input}"

        enter_text(driver, (By.XPATH, "//*[@id='asset-inputs-text']/div[1]/div[1]/form/textarea"), image_text)
        time.sleep(1)
        click_element(driver, (By.XPATH, "//*[@id='main_container']/div/div[2]/div[1]/div/div[2]/div[1]/button"))
        time.sleep(1)
        click_element(driver, (By.XPATH, "//*[@id='main_container']/div/div[2]/div[1]/div/div[2]/div[1]/div[2]/div[2]/button"))

        downloaded_file_path = wait_for_download(gui_callback)
        if downloaded_file_path:
            return downloaded_file_path
        else:
            gui_callback("No file was downloaded.")
    except Exception as e:
        gui_callback(f"An error occurred: {e}")

def create_auction(driver, auction_title, image_path, formatted_start_date, bid_formatted_ending_date, gui_callback, selected_warehouse):
    """Creates an auction on the specified bid website."""
    try:
        gui_callback('Creating auction on bid...')
        bid_url = "https://bid.702auctions.com/Event/CreateEvent"
        driver.get(bid_url)
    except Exception as e:
        gui_callback(f"Error configuring driver: {e}")
        return

    try:
        login(driver, (By.ID, "username"), (By.ID, "password"), "702marketplace@gmail.com", "Ronch420$", bid_url, gui_callback)
    except Exception as e:
        gui_callback(f"Error logging in: {e}")
        return

    try:
        enter_text(driver, (By.ID, "Title"), auction_title)

        # Customize information based on the selected warehouse
        if selected_warehouse == "Maule Warehouse":
            Summary_field_text = ('No reserve auctions of general merchandise returns and brand new shelf pulls from major retailers. '
                                  '702 Auctions offers hassle free returns on items that are misdescribed within 10 days of the date you picked up your items.')
            formatted_text_event_description = """
                <p><strong>PICKUP ONLY</strong></p>
                <p><strong>PICKUP WILL BE AT:</strong>
                <strong>702 AUCTIONS</</strong><br>
                <strong>1889 E. MAULE AVE SUITE F</strong><br>
                <strong>LAS VEGAS, NV 89119</strong></p>
            """
            formatted_text_terms_and_conditions = """
            <ul>
                <li>All auctions are no reserve and sold to the highest bidder.&nbsp;</li>
                <li>A 15% buyers fee applies to all items.&nbsp;</li>
                <li>We accept Cash and&nbsp;all major credit cards.</li>
                <li>All payments must be made online prior to pickup. You will be emailed an invoice with a payment link,&nbsp;or your invoice can be found under the My Account section and can be paid for there. If you are paying cash, your order must be paid and picked up within 72 hours.&nbsp;</li>
                <li>All invoices will be automatically charged to the Credit / Debit card on file&nbsp;by 5pm&nbsp;the following&nbsp;day.</li>
                <li>If there is no Credit/Debit card on file and there is no payment within 24 hours, the item will be relisted and you will not be allowed to bid again until you add a payment method to your account.</li>
                <li>All bidders must pick up&nbsp;their items&nbsp;from&nbsp;<b>1889 E. MAULE AVE. SUITE F&nbsp;Las Vegas, NV 89119</b>&nbsp;within&nbsp;<b>7 days</b>.</li>
                <li>Once payment is received, you will receive an email with a link to schedule a pickup time and pickup instructions.</li>
                <li>All items must be picked up within 10 days of the auction ending&nbsp;or your order will be canceled and may be subject to a restocking fee.</li>
            </ul>

            <p>702 Auctions offers returns on items that are misdescribed within 10 days of the date you picked up your items.</p>

            <p>For our complete terms and conditions,&nbsp;<a href="https://bid.702auctions.com/Home/Terms">Click Here</a></p>
            """
            formatted_text_shipping_info = """
                <p>Pickup only!!&nbsp;<b><a href="https://www.google.com/maps/place/702+Auctions/@36.0639879,-115.1263821,15z/data=!4m5!3m4!1s0x0:0xe95798d6193dc64!8m2!3d36.0639879!4d-115.1263821" target="_blank">1889 E. MAULE AVE. SUITE F&nbsp;Las Vegas, NV 89119</a></b>&nbsp;Monday-Friday 9am-5pm&nbsp;within 10 days. Once payment is received, you will receive an email with a link to schedule a pickup time and pickup instructions. We offer contactless pickup options and take all possible measures to ensure your safety.</p>
            """
        elif selected_warehouse == "Sunrise Warehouse":
            Summary_field_text = ('PICKUP FROM ACTION DISCOUNT SALES 3201 SUNRISE AVE LAS VEGAS, NV 89101 No reserve auctions of general merchandise returns and brand new shelf pulls from major retailers. 702 Auctions offers hassle free returns on items that  are misdescribed within 10 days of the date you picked up your items.')
            formatted_text_event_description = """
                <p><strong>PICKUP ONLY</strong></p>
                <p><strong>PICKUP WILL BE AT:</strong>
                <strong>ACTION DISCOUNT SALES</strong><br>
                <strong>3201 SUNRISE AVE</strong><br>
                <strong>LAS VEGAS, NV 89101</strong></p>
            """
            formatted_text_terms_and_conditions = """
            <ul>
                <li>All auctions are no reserve and sold to the highest bidder.&nbsp;</li>
                <li>A 15% buyers fee applies to all items.&nbsp;</li>
                <li>We accept Cash and&nbsp;all major credit cards.</li>
                <li>All payments must be made online prior to pickup. You will be emailed an invoice with a payment link,&nbsp;or your invoice can be found under the My Account section and can be paid for there. If you are paying cash, your order must be paid and picked up within 48 hours.&nbsp;</li>
                <li>All invoices will be automatically charged to the Credit / Debit card on file&nbsp;by 4pm&nbsp;the following&nbsp;day.</li>
                <li>If there is no Credit/Debit card on file and there is no payment within 24 hours, the item will be relisted and you will not be allowed to bid again until you add a payment method to your account.</li>
                <li>All bidders must pick up&nbsp;their items&nbsp;from&nbsp;<b>3201 SUNRISE AVE LAS VEGAS, NV 89101</b>&nbsp;within&nbsp;<b>5 days</b>.</li>
                <li>Once payment is received, you will receive an email with a link to schedule a pickup time and pickup instructions.</li>
                <li>All items must be picked up within 10 days of the auction ending&nbsp;or your order will be canceled and may be subject to a restocking fee.</li>
            </ul>

            <p>702 Auctions offers returns on items that are misdescribed within 10 days of the date you picked up your items.</p>

            <p>For our complete terms and conditions,&nbsp;<a href="https://bid.sunriseauctions.com/Home/Terms">Click Here</a></p>
            """
            formatted_text_shipping_info = """
                <p>Pickup only!!&nbsp;<b><a href="https://www.google.com/maps/place/Action+Discount+Sales/@36.1622141,-115.1056554,15z/data=!4m5!3m4!1s0x80c8c37738ffc453:0x74f8f3ddc1379320!8m2!3d36.1622141!4d-115.1056554" target="_blank">3201 SUNRISE AVE&nbsp;Las Vegas, NV 89101</a></b>&nbsp;Tuesday-Friday 10am-4pm&nbsp;within 10 days. Once payment is received, you will receive an email with a link to schedule a pickup time and pickup instructions. We offer contactless pickup options and take all possible measures to ensure your safety.</p>
            """

        # Enter the rest of the fields using the selected warehouse details
        enter_text(driver, (By.ID, "Subtitle"), Summary_field_text)
        set_content_in_ckeditor(driver, "EventDescription", formatted_text_event_description)
        set_content_in_ckeditor(driver, "TermsAndConditions", formatted_text_terms_and_conditions)
        set_content_in_ckeditor(driver, "ShippingInfo", formatted_text_shipping_info)

        file_input_html5 = find_element(driver, (By.ID, "html5files_EventImage"))
        if file_input_html5.get_attribute('type') == 'hidden':
            driver.execute_script("arguments[0].type = 'file';", file_input_html5)
        file_input_html5.send_keys(image_path)

        WebDriverWait(driver, 20).until(
            EC.text_to_be_present_in_element((By.CSS_SELECTOR, "#progress_bar_EventImage .percent"), "100%"))
        WebDriverWait(driver, 10).until(lambda driver: element_value_is_not_empty(driver, "ThumbnailRendererState_EventImage"))

        enter_text(driver, (By.ID, "StartDate"), formatted_start_date)
        enter_text(driver, (By.ID, "StartTime"), '1:00 AM')
        enter_text(driver, (By.ID, "EndDate"), bid_formatted_ending_date)
        enter_text(driver, (By.ID, "EndTime"), '6:30 PM')
        click_element(driver, (By.ID, "create"))

        find_element(driver, (By.CLASS_NAME, "alert-success"))
        current_url = driver.current_url

        match = re.search(r'/Event/EventConfirmation/(\d+)', current_url)
        if match:
            event_id = match.group(1)
            gui_callback(f"Event {event_id} created")
            return event_id
        else:
            gui_callback("Event ID not found in the URL.")
            return None
    except Exception as e:
        gui_callback(f"An error occurred: {e}")
        return None

def run_create_auction_with_callback(auction_title, ending_date, gui_callback, should_stop, shared_events, callback, show_browser, selected_warehouse):
    """Main function to create an auction with callback functionality."""

    # Select the relaythat_url based on the selected warehouse
    if selected_warehouse == "Maule Warehouse":
        relaythat_url = "https://app.relaythat.com/composition/2126969"
    elif selected_warehouse == "Sunrise Warehouse":
        relaythat_url = "https://app.relaythat.com/composition/1992064"
    else:
        gui_callback("Invalid warehouse selected.")
        return

    driver = None

    try:
        gui_callback("Creating Auction...")
        if should_stop.is_set():
            return

        month_formatted_date, bid_formatted_ending_date = format_date(ending_date)
        driver = configure_driver(relaythat_url, show_browser)

        if should_stop.is_set():
            driver.quit()
            return

        formatted_start_date = datetime.now().strftime('%m/%d/%Y')
        event_image = get_image(driver, month_formatted_date, relaythat_url, gui_callback, selected_warehouse)

        if should_stop.is_set():
            driver.quit()
            return

        if event_image:
            event_id = create_auction(driver, auction_title, event_image, formatted_start_date, bid_formatted_ending_date, gui_callback, selected_warehouse)
            if should_stop.is_set():
                driver.quit()
                return

            if event_id:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                shared_events.add_event(auction_title, event_id, ending_date, timestamp)
                gui_callback(f"Event {event_id} created at {timestamp}")
            else:
                gui_callback("Failed to obtain event ID.")
        else:
            gui_callback("Failed to download the event image.")
    except Exception as e:
        gui_callback(f"Error: {e}")
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()
        callback()

# Add this function to allow calling the script from Django view
def create_auction_main(auction_title, ending_date, show_browser, selected_warehouse):
    def gui_callback(message):
        print(message)

    def should_stop():
        return False

    class SharedEvents:
        def add_event(self, title, event_id, ending_date, timestamp):
            print(f"Event added: {title}, ID: {event_id}, Ending Date: {ending_date}, Timestamp: {timestamp}")

    shared_events = SharedEvents()

    def callback():
        print("Auction creation process completed.")

    run_create_auction_with_callback(auction_title, ending_date, gui_callback, should_stop, shared_events, callback, show_browser, selected_warehouse)

if __name__ == "__main__":
    create_auction_main("Sample Auction", datetime.now(), show_browser=True, selected_warehouse="Maule Warehouse")
