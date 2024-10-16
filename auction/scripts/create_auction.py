import os
import time
import json
import re
import traceback
import threading
import sys
import asyncio
from django.utils.timezone import make_aware
from celery import shared_task
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from django.db import transaction, connection
from auction.utils import config_manager
from concurrent.futures import ThreadPoolExecutor
import logging
from asgiref.sync import sync_to_async
from auction.models import Event
from django.utils.dateparse import parse_date
from django.conf import settings
from playwright.sync_api import sync_playwright
from auction.utils.redis_utils import RedisTaskStatus

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

def get_maule_login_credentials():
    # Temporarily set the active warehouse to Maule
    original_warehouse = config_manager.active_warehouse
    config_manager.set_active_warehouse("Maule Warehouse")
    
    bid_username = config_manager.get_warehouse_var('bid_username')
    bid_password = config_manager.get_warehouse_var('bid_password')
    
    # Reset the active warehouse to the original selection
    config_manager.set_active_warehouse(original_warehouse)
    
    logger.info('Note: Using Maule warehouse credentials for auction site login, regardless of selected warehouse.')
    return bid_username, bid_password

# Make sure to keep these helper functions in your script
def wait_for_element(page, selector, timeout=30000):
    """Wait for an element to be present and return it."""
    return page.wait_for_selector(selector, timeout=timeout)

async def wait_for_loading_to_complete(page, timeout=60000):
    """Wait for the loading indicator to disappear."""
    try:
        # Wait for any element with 'loading' in its class to disappear
        await page.wait_for_selector("*[class*='loading']", state="hidden", timeout=timeout)
        logger.info("Loading indicator disappeared")
    except Exception as e:
        logger.warning(f"Error waiting for loading to complete: {e}")
        await page.screenshot(path='loading_incomplete.png')
    
    # Add a small delay to ensure everything has settled
    await page.wait_for_timeout(2000)

def wait_for_download(page, timeout=300000):
    """Wait for a file to be downloaded and return its path."""
    with page.expect_download(timeout=timeout) as download_info:
        # Trigger the download
        pass
    download = download_info.value
    path = download.path()
    return path

def save_event_to_database(event_data):
    try:
        with transaction.atomic():
            start_date = make_aware(datetime.strptime(event_data['start_date'], '%m/%d/%Y'))
            ending_date = make_aware(datetime.strptime(event_data['ending_date'], '%Y-%m-%d'))
            Event.objects.create(
                event_id=event_data['event_id'],
                warehouse=event_data['warehouse'],
                title=event_data['title'],
                start_date=start_date,
                ending_date=ending_date,
                timestamp=event_data['timestamp']
            )
        logger.info(f"Event {event_data['event_id']} saved to database")
    except Exception as e:
        logger.error(f"Error saving event to database: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

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

def login_relaythat(page, username, password, url):
    """Logs in to RelayThat using provided credentials."""
    try:
        page.goto(url)
        page.wait_for_load_state('networkidle', timeout=60000)
        
        # Wait for and fill email field
        logger.info("Waiting for RelayThat email field to be visible...")
        page.wait_for_selector("#user_email", state="visible", timeout=90000)
        page.fill("#user_email", username)
        
        # Wait for and fill password field
        logger.info("Waiting for RelayThat password field to be visible...")
        page.wait_for_selector("#user_password", state="visible", timeout=90000)
        page.fill("#user_password", password)
        
        # Wait for and click the sign-in button
        logger.info("Waiting for RelayThat sign-in button to be visible...")
        sign_in_button = page.wait_for_selector('input[type="submit"][name="commit"][value="Sign in"].button-primary', state="visible", timeout=90000)
        if sign_in_button:
            sign_in_button.click()
        else:
            logger.error("RelayThat sign in button not found")
            page.screenshot(path='relaythat_login_error_button_not_found.png')
            return False

        # Wait for navigation after clicking sign in
        page.wait_for_load_state('networkidle', timeout=60000)
        
        # Check if login was successful
        if "login" in page.url.lower():
            logger.error("RelayThat login failed. Still on login page.")
            page.screenshot(path='relaythat_login_error_still_on_login_page.png')
            return False
        
        logger.info("RelayThat login successful")
        return True
    except Exception as e:
        logger.error(f"RelayThat login failed: {e}")
        logger.error(f"Current URL: {page.url}")
        page.screenshot(path='relaythat_login_error_exception.png')
        return False

async def login_auction_site(page, username, password, url):
    """Logs in to the auction site using provided credentials."""
    try:
        await page.goto(url)
        await page.wait_for_load_state('networkidle', timeout=60000)
        
        # Wait for and fill username field
        logger.info("Waiting for auction site username field to be visible...")
        await page.wait_for_selector("#username", state="visible", timeout=90000)
        await page.fill("#username", username)
        
        # Wait for and fill password field
        logger.info("Waiting for auction site password field to be visible...")
        await page.wait_for_selector("#password", state="visible", timeout=90000)
        await page.fill("#password", password)
        
        # Wait for and click the sign-in button
        logger.info("Waiting for auction site sign-in button to be visible...")
        sign_in_button = await page.wait_for_selector('input[type="submit"][value="Sign In"]', state="visible", timeout=90000)
        if sign_in_button:
            await sign_in_button.click()
        else:
            logger.error("Auction site sign in button not found")
            await page.screenshot(path='auction_site_login_error_button_not_found.png')
            return False

        # Wait for navigation after clicking sign in
        await page.wait_for_load_state('networkidle', timeout=60000)
        
        # Check if login was successful
        if "logon" in page.url.lower() or "login" in page.url.lower():
            logger.error("Auction site login failed. Still on login page.")
            await page.screenshot(path='auction_site_login_error_still_on_login_page.png')
            return False
        
        logger.info("Auction site login successful")
        return True
    except Exception as e:
        logger.error(f"Auction site login failed: {e}")
        logger.error(f"Current URL: {page.url}")
        await page.screenshot(path='auction_site_login_error_exception.png')
        return None

async def set_content_in_ckeditor(page, iframe_title, formatted_text):
    """Sets content in a CKEditor iframe."""
    iframe = page.frame_locator(f"iframe[title='Rich Text Editor, {iframe_title}']")
    ckeditor_body = iframe.locator("body[contenteditable='true']")
    await ckeditor_body.evaluate(f"element => element.innerHTML = {json.dumps(formatted_text)}")

def element_value_is_not_empty(page, element_id):
    """Checks if the value of an element is not empty."""
    return page.evaluate(f"document.getElementById('{element_id}').value !== ''")

def get_image(page, ending_date, selected_warehouse, task):
    try:
        task.update_state(state="PROGRESS", meta={'status': f"Initiating image download for {selected_warehouse}"})
        
        relaythat_email = config_manager.get_global_var('relaythat_email')
        relaythat_password = config_manager.get_global_var('relaythat_password')
        relaythat_url = config_manager.get_warehouse_var('relaythat_url')

        task.update_state(state="PROGRESS", meta={'status': f"Logging in to RelayThat for {selected_warehouse}"})
        logger.info(f"Attempting to log in with email: {relaythat_email}")
        
        page.goto(relaythat_url)
        page.wait_for_load_state('networkidle', timeout=60000)
        
        # Login process
        page.fill("#user_email", relaythat_email)
        page.fill("#user_password", relaythat_password)
        page.click('input[type="submit"][name="commit"][value="Sign in"].button-primary')
        page.wait_for_load_state('networkidle', timeout=60000)
        
        if "login" in page.url.lower():
            raise Exception("RelayThat login failed")
        
        task.update_state(state="PROGRESS", meta={'status': "RelayThat login successful"})
        logger.info('RelayThat login successful. Waiting for page to load...')
        page.wait_for_load_state('networkidle', timeout=60000)

        if selected_warehouse == "Maule Warehouse":
            task.update_state(state="PROGRESS", meta={'status': f"Inserting ending date: {ending_date} into RelayThat design"})
            logger.info(f'Inserting ending date: {ending_date} into RelayThat design')
            date_input_selector = 'textarea.text-input__textarea'
            page.wait_for_selector(date_input_selector, state="visible", timeout=10000)
            
            page.evaluate(f'''(selector) => {{
                const element = document.querySelector(selector);
                element.value = '';
                element.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}''', date_input_selector)
            page.fill(date_input_selector, f"Ending {ending_date}")
            
            page.evaluate(f'''(selector) => {{
                const element = document.querySelector(selector);
                element.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}''', date_input_selector)
            
            page.wait_for_timeout(3000)

        task.update_state(state="PROGRESS", meta={'status': "Preparing to download image"})
        logger.info('Preparing to download image...')
        
        # Click the first Download button
        first_download_button = page.wait_for_selector("button.ui.teal.tiny.button:has-text('Download')", state="visible", timeout=60000)
        if not first_download_button:
            raise Exception("Download button not found")
        first_download_button.click()

        # Wait for the download popup to appear
        page.wait_for_timeout(2000)

        # Click the second Download button in the popup
        second_download_button = page.wait_for_selector("button.ui.fluid.primary.button:has-text('Download')", state="visible", timeout=60000)
        if not second_download_button:
            raise Exception("Second Download button not found")

        with page.expect_download(timeout=60000) as download_info:
            second_download_button.click()
        
        download = download_info.value
        downloaded_file = download.path()
        
        if not downloaded_file:
            raise Exception("No file was downloaded")

        task.update_state(state="PROGRESS", meta={'status': f"Image downloaded: {downloaded_file}"})
        logger.info(f"Image downloaded: {downloaded_file}")
        return downloaded_file

    except Exception as e:
        logger.error(f"An error occurred in get_image: {e}")
        logger.error(traceback.format_exc())
        page.screenshot(path='get_image_error.png')
        raise

def create_auction(page, auction_title, image_path, formatted_start_date, bid_formatted_ending_date, selected_warehouse, task):
    try:
        task.update_state(state="PROGRESS", meta={'status': "Navigating to auction creation page"})
        logger.info('Navigating to auction creation page...')
        bid_create_event = config_manager.get_global_var('bid_create_event')
        website_login_url = config_manager.get_global_var('website_login_url')
        
        # Navigate to the login page first
        page.goto(website_login_url)
        
        task.update_state(state="PROGRESS", meta={'status': "Logging in to auction site"})
        logger.info('Logging in to auction site using Maule Warehouse credentials...')
        bid_username, bid_password = get_maule_login_credentials()
        login_success = login_auction_site(page, bid_username, bid_password, website_login_url)
        if not login_success:
            raise Exception("Failed to log in to auction site")
        
        # Navigate to the auction creation page after login
        page.goto(bid_create_event)
        page.wait_for_load_state('networkidle', timeout=60000)

        task.update_state(state="PROGRESS", meta={'status': "Filling auction details"})
        logger.info('Filling auction details...')
        page.fill("#Title", auction_title)

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
        page.fill("#Subtitle", Summary_field_text)
        set_content_in_ckeditor(page, "EventDescription", formatted_text_event_description)
        set_content_in_ckeditor(page, "TermsAndConditions", formatted_text_terms_and_conditions)
        set_content_in_ckeditor(page, "ShippingInfo", formatted_text_shipping_info)

        logger.info('Uploading auction image...')
        file_input = page.locator("#html5files_EventImage")
        file_input.set_input_files(image_path)

        page.wait_for_selector("#progress_bar_EventImage .percent:text('100%')")
        page.wait_for_function("document.getElementById('ThumbnailRendererState_EventImage').value !== ''")

        logger.info('Setting auction dates...')
        page.fill("#StartDate", formatted_start_date)
        page.fill("#StartTime", '1:00 AM')
        page.fill("#EndDate", bid_formatted_ending_date)
        page.fill("#EndTime", '6:30 PM')

        logger.info('Creating auction...')
        page.click("#create")

        page.wait_for_selector(".alert-success")
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
        page.screenshot(path='create_auction_error.png')
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
        save_event_to_database(event_data)

def create_auction_main(task, auction_title, ending_date, selected_warehouse):
    try:
        task.update_state(state="STARTED", meta={'status': f"Starting auction creation for {auction_title}"})
        
        logger.info(f"Starting create_auction_main for auction: {auction_title}, warehouse: {selected_warehouse}")

        config_manager.set_active_warehouse(selected_warehouse)
        task.update_state(state='PROGRESS', meta={'status': "Warehouse configuration set"})

        task.update_state(state='PROGRESS', meta={'status': "Initializing auction creation process"})

        month_formatted_date, bid_formatted_ending_date = format_date(ending_date)
        task.update_state(state='PROGRESS', meta={'status': f"Date formatting completed: {month_formatted_date}, {bid_formatted_ending_date}"})

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            task.update_state(state='PROGRESS', meta={'status': "Browser launched"})

            formatted_start_date = datetime.now().strftime('%m/%d/%Y')
            task.update_state(state='PROGRESS', meta={'status': f"Getting auction image for date: {month_formatted_date}"})

            event_image = get_image(page, month_formatted_date, selected_warehouse, task)
            if not event_image:
                raise Exception("Failed to download the event image")

            task.update_state(state='PROGRESS', meta={'status': f"Image downloaded: {event_image}"})

            event_id = create_auction(page, auction_title, event_image, formatted_start_date, 
                                      bid_formatted_ending_date, selected_warehouse, task)
            if not event_id:
                raise Exception("Failed to obtain event ID")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            event_data = {
                "warehouse": selected_warehouse,
                "title": auction_title,
                "event_id": event_id,
                "start_date": formatted_start_date,
                "ending_date": ending_date.strftime('%Y-%m-%d'),
                "timestamp": timestamp
            }
        
            save_event_to_database(event_data)
        
            task.update_state(state='SUCCESS', meta={'status': f"Event {event_id} created and saved at {timestamp}"})
            return event_id

    except Exception as e:
        task.update_state(state='FAILURE', meta={'status': f"Error in create_auction_main: {str(e)}"})
        logger.error(f"Error in create_auction_main: {str(e)}")
        logger.error(traceback.format_exc())
        raise

@shared_task(bind=True)
def create_auction_task(self, auction_title, ending_date, selected_warehouse):
    async def run_task():
        return await create_auction_main(self, auction_title, ending_date, selected_warehouse)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(run_task())
    finally:
        loop.close()