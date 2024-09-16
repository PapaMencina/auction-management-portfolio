import os
import time
import json
import re
import traceback
import threading
import sys
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
from auction.utils import config_manager
import logging
from asgiref.sync import sync_to_async

# Set up logging to console
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.StreamHandler(sys.stdout)
                    ])

# Load configuration
config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'config.json')
config_manager.load_config(config_path)

logger = logging.getLogger(__name__)

# Define a lock for thread-safe file operations
file_lock = threading.Lock()

# Make sure to keep these helper functions in your script
def wait_for_element(page, selector, timeout=30000):
    """Wait for an element to be present and return it."""
    return page.wait_for_selector(selector, timeout=timeout)

def wait_for_loading_to_complete(page, timeout=30000):
    """Wait for the loading indicator to disappear."""
    try:
        page.wait_for_selector("div[class*='loading']", state="hidden", timeout=timeout)
    except TimeoutError:
        logger.warning("Loading indicator not found or did not disappear")

def wait_for_download(page, timeout=300000):
    """Wait for a file to be downloaded and return its path."""
    with page.expect_download(timeout=timeout) as download_info:
        # Trigger the download
        pass
    download = download_info.value
    path = download.path()
    return path

def save_event_to_file(event_data):
    file_path = os.path.join(get_resources_dir(''), 'events.json')
    logger.info(f"Attempting to save event to {file_path}")
    with file_lock:
        try:
            events = []
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                try:
                    with open(file_path, "r") as file:
                        events = json.load(file)
                    logger.info(f"Loaded existing events: {len(events)}")
                except json.JSONDecodeError:
                    logger.warning("Existing file contains invalid JSON. Starting with empty list.")
            else:
                logger.info("No existing events file or file is empty. Creating new.")

            events.append(event_data)
            logger.info(f"Added new event. Total events: {len(events)}")

            with open(file_path, "w") as file:
                json.dump(events, file, indent=4)
            logger.info(f"Event successfully saved to {file_path}")

        except Exception as e:
            logger.error(f"Unexpected error when saving event to file: {e}")
            logger.error("Traceback:", exc_info=True)
        finally:
            logger.debug(f"Current working directory: {os.getcwd()}")
            logger.debug(f"File exists: {os.path.exists(file_path)}")

    return os.path.exists(file_path) and os.path.getsize(file_path) > 0

def get_resources_dir(folder=''):
    base_path = os.environ.get('AUCTION_RESOURCES_PATH', '/app/resources')
    return os.path.join(base_path, folder)

def format_date(date_obj):
    """Formats a datetime object into 'December 2nd' and '12/02/2023' formats."""
    if 11 <= date_obj.day <= 13:
        suffix = "th"
    else:
        suffix = {"1": "st", "2": "nd", "3": "rd"}.get(str(date_obj.day)[-1], "th")

    month_day_str = date_obj.strftime("%B %d").replace(" 0", " ") + suffix
    full_date = date_obj.strftime('%m/%d/%Y')
    return month_day_str, full_date

async def login(page, username, password, url):
    """Logs in to the specified URL using provided credentials."""
    try:
        await page.goto(url)
        await page.wait_for_load_state('networkidle')
        
        await page.wait_for_selector("#user_email", state="visible", timeout=60000)
        await page.fill("#user_email", username)
        
        await page.wait_for_selector("#user_password", state="visible", timeout=60000)
        await page.fill("#user_password", password)
        
        await page.click('button:has-text("Sign in")')
        await page.wait_for_load_state('networkidle')
        
        # Check if login was successful
        if "login" in page.url.lower():
            logger.error("Login failed. Still on login page.")
            await page.screenshot(path='login_error.png')
            return False
        return True
    except Exception as e:
        logger.error(f"Login failed: {e}")
        logger.error(f"Current URL: {page.url}")
        await page.screenshot(path='login_error.png')
        return False

def set_content_in_ckeditor(page, iframe_title, formatted_text):
    """Sets content in a CKEditor iframe."""
    iframe = page.frame_locator(f"iframe[title='Rich Text Editor, {iframe_title}']")
    ckeditor_body = iframe.locator("body[contenteditable='true']")
    ckeditor_body.evaluate(f"element => element.innerHTML = {json.dumps(formatted_text)}")

def element_value_is_not_empty(page, element_id):
    """Checks if the value of an element is not empty."""
    return page.evaluate(f"document.getElementById('{element_id}').value !== ''")

async def get_image(page, ending_date_input, relaythat_url, selected_warehouse):
    try:
        logger.info('Logging in to RelayThat...')
        relaythat_email = config_manager.get_global_var('relaythat_email')
        relaythat_password = config_manager.get_global_var('relaythat_password')
        login_success = await login(page, relaythat_email, relaythat_password, relaythat_url)
        
        if not login_success:
            logger.error("Failed to log in to RelayThat. Aborting process.")
            return None

        logger.info('Generating auction image...')
        image_text = "OFFSITE" if selected_warehouse == "Sunrise Warehouse" else f"Ending {ending_date_input}"

        text_input = page.locator("#asset-inputs-text textarea").first
        await text_input.fill(image_text)

        generate_button = page.locator("button:has-text('Generate')")
        await generate_button.click()
        await wait_for_loading_to_complete(page)

        download_button = page.locator("button:has-text('Download')")
        
        logger.info("Waiting for image download...")
        
        downloaded_file = await wait_for_download(page)
        
        if downloaded_file:
            logger.info(f"Image downloaded: {downloaded_file}")
            return downloaded_file
        else:
            logger.error("No file was downloaded.")
            return None

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        logger.error(traceback.format_exc())
        return None

async def create_auction(page, auction_title, image_path, formatted_start_date, bid_formatted_ending_date, selected_warehouse):
    try:
        logger.info('Navigating to auction creation page...')
        bid_create_event = config_manager.get_global_var('bid_create_event')
        await page.goto(bid_create_event)
    except Exception as e:
        logger.error(f"Error navigating to auction creation page: {e}")
        return

    try:
        logger.info('Logging in to auction site...')
        bid_username = config_manager.get_warehouse_var('bid_username')
        bid_password = config_manager.get_warehouse_var('bid_password')
        login_success = await login(page, bid_username, bid_password, bid_create_event)
        if not login_success:
            logger.error("Failed to log in to auction site. Aborting process.")
            return None
    except Exception as e:
        logger.error(f"Error logging in: {e}")
        return

    try:
        logger.info('Filling auction details...')
        await page.fill("#Title", auction_title)

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

        logger.info('Setting auction details...')
        await page.fill("#Subtitle", Summary_field_text)
        await set_content_in_ckeditor(page, "EventDescription", formatted_text_event_description)
        await set_content_in_ckeditor(page, "TermsAndConditions", formatted_text_terms_and_conditions)
        await set_content_in_ckeditor(page, "ShippingInfo", formatted_text_shipping_info)

        logger.info('Uploading auction image...')
        file_input = page.locator("#html5files_EventImage")
        await file_input.set_input_files(image_path)

        await page.wait_for_selector("#progress_bar_EventImage .percent:text('100%')")
        await page.wait_for_function("document.getElementById('ThumbnailRendererState_EventImage').value !== ''")

        logger.info('Setting auction dates...')
        await page.fill("#StartDate", formatted_start_date)
        await page.fill("#StartTime", '1:00 AM')
        await page.fill("#EndDate", bid_formatted_ending_date)
        await page.fill("#EndTime", '6:30 PM')

        logger.info('Creating auction...')
        await page.click("#create")

        await page.wait_for_selector(".alert-success")
        current_url = page.url

        match = re.search(r'/Event/EventConfirmation/(\d+)', current_url)
        if match:
            event_id = match.group(1)
            logger.info(f"Event {event_id} created")
            return event_id
        else:
            logger.error("Event ID not found in the URL.")
            return None
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return None

class SharedEvents:
    def add_event(self, title, event_id, ending_date, timestamp):
        print(f"Event added: {title}, ID: {event_id}, Ending Date: {ending_date}, Timestamp: {timestamp}")
        event_data = {
            "title": title,
            "event_id": event_id,
            "ending_date": str(ending_date),
            "timestamp": timestamp
        }
        save_event_to_file(event_data)

async def create_auction_main(auction_title, ending_date, show_browser, selected_warehouse):
    logger.info(f"Starting create_auction_main for auction: {auction_title}, warehouse: {selected_warehouse}")

    event_id = None

    try:
        config_manager.set_active_warehouse(selected_warehouse)
        logger.info("Warehouse configuration set")

        relaythat_url = config_manager.get_warehouse_var('relaythat_url')
        if not relaythat_url:
            raise ValueError("Invalid warehouse selected or missing relaythat_url in config.")

        logger.info("Initializing auction creation process")

        month_formatted_date, bid_formatted_ending_date = format_date(ending_date)
        logger.info(f"Date formatting completed: {month_formatted_date}, {bid_formatted_ending_date}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not show_browser)
            context = await browser.new_context()
            page = await context.new_page()

            logger.info("Browser launched")

            formatted_start_date = datetime.now().strftime('%m/%d/%Y')
            logger.info(f"Getting auction image for date: {month_formatted_date}")

            event_image = await get_image(page, month_formatted_date, relaythat_url, selected_warehouse)
            if not event_image:
                raise Exception("Failed to download the event image")

            logger.info(f"Image downloaded: {event_image}")

            event_id = await create_auction(page, auction_title, event_image, formatted_start_date, 
                                            bid_formatted_ending_date, selected_warehouse)
            if not event_id:
                raise Exception("Failed to obtain event ID")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            event_data = {
                "warehouse": selected_warehouse,
                "title": auction_title,
                "event_id": event_id,
                "start_date": formatted_start_date,
                "ending_date": str(ending_date),
                "timestamp": timestamp
            }
            save_event_to_file(event_data)
            logger.info(f"Event {event_id} created at {timestamp}")

    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in create_auction_main: {str(e)}")
        logger.error(traceback.format_exc())
    
    return event_id

if __name__ == "__main__":
    import asyncio
    asyncio.run(create_auction_main("Sample Auction", datetime.now(), True, "Maule Warehouse"))