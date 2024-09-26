import os
import time
import re
import traceback
import json
import random
import asyncio
import tempfile
from collections import defaultdict
from typing import List, Dict, Tuple
from io import BytesIO

# Django imports
from django.core.wsgi import get_wsgi_application
from django.conf import settings
from asgiref.sync import sync_to_async

# Third-party imports
import aiohttp
import aioftp
import pandas as pd
from PIL import Image, ExifTags
from playwright.async_api import async_playwright

# Local imports
from auction.models import Event, ImageMetadata, AuctionFormattedData
from auction.utils import config_manager
from auction.utils.redis_utils import RedisTaskStatus

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auction_webapp.settings")
application = get_wsgi_application()

# Load configuration
config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'config.json'
)
config_manager.load_config(config_path)


def get_image_orientation(img: Image.Image) -> int:
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == 'Orientation':
                    return value
    except (AttributeError, KeyError, IndexError):
        pass
    return None


async def download_image_async(url: str, gui_callback) -> bytes:
    if not url:
        gui_callback("Invalid input: URL is required")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    gui_callback(f"Error downloading image: HTTP {response.status}")
                    return None

                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith('image/'):
                    gui_callback("URL does not point to an image")
                    return None

                content = await response.read()
                if len(content) < 1000:
                    gui_callback("Image is too small, might be corrupted")
                    return None

                return content

    except Exception as e:
        gui_callback(f"Error while downloading {url}: {str(e)}")
        return None


async def process_image_async(image_data: bytes, gui_callback, width_threshold: int = 1024, dpi_threshold: int = 72) -> bytes:
    try:
        img = Image.open(BytesIO(image_data))
        orientation = get_image_orientation(img)

        if orientation == 6:
            img = img.rotate(-90, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)

        if img.mode == 'P':
            img = img.convert('RGB')

        width, height = img.size
        if width > width_threshold:
            new_width = width_threshold
            new_height = int(height * (new_width / width))
            img = img.resize((new_width, new_height))

        if img.mode == 'RGBA':
            img = img.convert('RGB')

        dpi = img.info.get('dpi', (72, 72))
        if dpi[0] > dpi_threshold or dpi[1] > dpi_threshold:
            img.info['dpi'] = (dpi_threshold, dpi_threshold)

        output = BytesIO()
        img.save(output, format='JPEG')
        return output.getvalue()

    except Exception as e:
        gui_callback(f"Error processing image: {e}")
        return None


async def upload_file_via_ftp_async(file_name: str, file_content: bytes, gui_callback, max_retries: int = 3) -> str:
    remote_file_path = config_manager.get_global_var('ftp_remote_path')
    server = config_manager.get_global_var('ftp_server')
    username = config_manager.get_global_var('ftp_username')
    password = config_manager.get_global_var('ftp_password')

    retries = 0
    while retries < max_retries:
        try:
            async with aioftp.Client.context(server, username, password) as client:
                await client.change_directory('/')
                remote_path_full = os.path.join(remote_file_path, file_name)

                gui_callback(f"Uploading file {file_name} to {remote_path_full}")
                await client.upload_stream(BytesIO(file_content), remote_path_full)
                gui_callback(f"File {file_name} uploaded successfully")

            formatted_url = remote_path_full.replace("/public_html", "", 1).lstrip('/')
            return f"https://{formatted_url}"

        except aioftp.StatusCodeError as e:
            gui_callback(f"Temporary FTP error: {e}. Retrying in 5 seconds...")
            retries += 1
            await asyncio.sleep(5)
        except Exception as e:
            gui_callback(f"FTP upload error: {e}.")
            break

    gui_callback("Failed to upload after maximum retries.")
    return None


async def get_cached_airtable_records(BASE: str, TABLE: str, VIEW: str, gui_callback, airtable_token: str) -> List[Dict]:
    # Caching is omitted as Redis is not used in this refactoring
    records = await get_airtable_records_list(BASE, TABLE, VIEW, gui_callback, airtable_token)
    return records


async def get_airtable_records_list(BASE: str, TABLE: str, VIEW: str, gui_callback, airtable_token: str) -> List[Dict]:
    gui_callback("Getting Airtable Records...")
    response_list = []
    offset = ""

    headers = {
        "Authorization": f"Bearer {airtable_token}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?view={VIEW}"
                if offset:
                    url += f"&offset={offset}"

                gui_callback(f"Requesting URL: {url}")
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    response_json = await response.json()

                records = response_json.get("records", [])
                response_list.extend(records)
                gui_callback(f"Retrieved {len(records)} records")

                offset = response_json.get("offset")
                if not offset:
                    break
            except Exception as e:
                gui_callback(f"Exception occurred: {e}")
                break

    gui_callback(f"Retrieved a total of {len(response_list)} records from Airtable")
    return response_list


def text_shortener(input_text: str, str_len: int) -> str:
    if len(input_text) > str_len:
        end = input_text.rfind(' ', 0, str_len)
        return input_text[:end if end != -1 else str_len].strip()
    return input_text


def format_field(label: str, value: str) -> str:
    return f"{label}: {value}" if value is not None and str(value).strip() else ""


def format_html_field(field_name: str, value: str) -> str:
    return f"<b>{field_name}</b>: {value}<br>" if value else ""


def category_converter(category: str) -> int:
    category_dict = {
        2830472: "appliances",
        2830485: ["arts, crafts & sewing", "arts,crafts & sewing", "arts & crafts", "arts"],
        339711: ["automotive", "automotive parts & accessories"],
        339747: "furniture",
        2830498: "baby products",
        2830511: "beauty & personal care",
        2830524: "cell phones & accessories",
        2830537: ["clothing", "clothing,shoes & jewelry", "clothing, shoes & jewelry"],
        2153220: ["comics", "collectibles"],
        339723: ["electronics", "computers & accessories"],
        2830563: "grocery & gourmet food",
        2830576: "health & household",
        162703: ["home & kitchen", "storage & organization", "kitchen & dining"],
        2830771: "industrial & scientific",
        2830784: "medical supplies & equipment",
        2830797: "mobility & daily living aids",
        2673968: "musical instruments",
        2830810: "office products",
        2830823: ["lawn & garden", "garden & outdoor"],
        2830836: ["dogs", "cats", "pet supplies"],
        2830862: "restaurant appliances & equipment",
        2830875: "sports & fitness",
        2830914: ["lighting & ceiling fans", "tools & home improvement", "kitchen & bath fixtures", "power & hand tools"],
        2830927: "toys & games",
        2830940: "video games",
        162733: "misc",
        2830888: ["sports & outdoors", "outdoors"],
        2831231: "movies & tv",
        507716: "luggage",
        507704: "drugstore",
        2673955: "books",
        2831248: "cds & vinyl",
        70189253: "Pool",
        83468654: "Christmas"
    }

    for key, value in category_dict.items():
        if isinstance(value, str):
            if category.lower() == value.lower():
                return key
        elif isinstance(value, list):
            if category.lower() in [v.lower() for v in value]:
                return key

    return 162733  # Default category ID


def format_subtitle(auction_count: int, msrp: float, other_notes: str) -> str:
    msrp_str = f"MSRP: ${msrp}"
    if auction_count >= 4:
        final_msrp = msrp_str
    elif auction_count == 3:
        final_msrp = f"{msrp_str} ---"
    elif auction_count == 2:
        final_msrp = f"{msrp_str} --"
    else:
        final_msrp = f"{msrp_str} -"

    notes_str = f"NOTES: {other_notes}" if other_notes and other_notes.strip() else ""
    return (final_msrp + " " + notes_str)[:80]


def process_single_record(airtable_record: Dict, uploaded_image_urls: Dict[str, List[Tuple[str, int]]],
                          auction_id: str, selected_warehouse: str, starting_price: str, gui_callback) -> Dict:
    try:
        new_record = {}
        record_id = airtable_record.get('id', '')
        gui_callback(f"Processing record ID: {record_id}")

        # Basic information
        new_record["AuctionCount"] = airtable_record["fields"].get("Auction Count", "")
        new_record["Photo Taker"] = airtable_record["fields"].get("Clerk", "")
        new_record["Size"] = airtable_record["fields"].get("Size", "")

        # Handle UPC field
        upc = str(airtable_record["fields"].get("UPC", ""))
        if upc.lower() == 'nan' or not upc.isdigit():
            new_record["UPC"] = ""
        else:
            new_record["UPC"] = upc

        new_record["ID"] = record_id
        product_id = str(airtable_record["fields"].get("Lot Number", ""))
        new_record["LotNumber"] = new_record["Lot Number"] = str(product_id)
        new_record["Other Notes"] = airtable_record["fields"].get("Notes", "")
        new_record["MSRP"] = airtable_record["fields"].get("MSRP", "0.00")
        new_record["Truck"] = airtable_record["fields"].get("Shipment", "")
        new_record["Category_not_formatted"] = airtable_record["fields"].get("Category", "")
        new_record["Amazon ID"] = airtable_record["fields"].get("B00 ASIN", "")
        new_record["Item Condition"] = airtable_record["fields"].get("Condition", "")
        new_record["HibidSearchText"] = airtable_record["fields"].get("Description", "")
        new_record["FullTitle"] = airtable_record["fields"].get("Product Name", "")
        new_record["Location"] = airtable_record["fields"].get("Location", "")

        # Format fields
        base_fields = [
            format_field("Description", new_record['FullTitle']),
            format_field("MSRP", new_record['MSRP']),
            format_field("Condition", new_record['Item Condition']),
            format_field("Notes", new_record['Other Notes']),
            format_field("Other info", new_record['HibidSearchText']),
            format_field("Lot Number", product_id)
        ]

        html_base_fields = [
            format_html_field("Description", new_record['FullTitle']),
            format_html_field("MSRP", new_record['MSRP']),
            format_html_field("Condition", new_record['Item Condition']),
            format_html_field("Notes", new_record['Other Notes']),
            format_html_field("Other info", new_record['HibidSearchText']),
            format_html_field("Lot Number", product_id)
        ]

        # HiBid and Description fields
        hibid_message = f"This item is live on our site, 702 Auctions.com. To view additional images and bid on this item, CLICK THE LINK ABOVE or visit bid.702auctions.com and search for lot number {new_record['LotNumber']}."
        new_record["HiBid"] = " -- ".join([hibid_message] + [field for field in base_fields if field])
        new_record["Description"] = ''.join(field for field in html_base_fields if field)

        # Standard fields
        new_record["Currency"] = "USD"
        new_record["ListingType"] = "Auction"
        new_record["Seller"] = "702Auctions"
        new_record["EventID"] = auction_id
        new_record["Region"] = "88850842" if selected_warehouse == "Maule Warehouse" else "88850843" if selected_warehouse == "Sunrise Warehouse" else ""
        new_record["Source"] = "AMZ FC"
        new_record["IsTaxable"] = "TRUE"
        new_record["Quantity"] = "1"

        # Title and Category
        title = airtable_record["fields"].get("Product Name", "")
        if selected_warehouse == "Sunrise Warehouse":
            title = "OFFSITE " + title
        new_record["Title"] = text_shortener(title, 80)
        new_record["Category"] = category_converter(new_record.get("Category_not_formatted", ""))

        # Price and Subtitle
        auction_count = int(new_record.get("AuctionCount", 0))
        new_record["Price"] = starting_price
        new_record["Subtitle"] = format_subtitle(
            auction_count,
            float(new_record.get("MSRP", 0)),
            new_record.get("Other Notes", "")
        )

        # Handle image ordering
        if record_id in uploaded_image_urls:
            gui_callback(f"Found uploaded images for record ID: {record_id}")
            gui_callback(f"Uploaded image URLs: {uploaded_image_urls[record_id]}")

            # Sort the uploaded images by image number
            sorted_images = sorted(uploaded_image_urls[record_id], key=lambda x: x[1])

            for i in range(1, 11):
                new_record[f'Image_{i}'] = ''  # Initialize all image fields as empty

            for url, image_number in sorted_images:
                if image_number <= 10:  # Ensure we only use up to 10 images
                    new_record[f'Image_{image_number}'] = url
                    gui_callback(f"Assigned Image_{image_number}: {url}")
        else:
            gui_callback(f"No uploaded images found for record ID: {record_id}")
            # Add empty image fields
            for i in range(1, 11):
                new_record[f'Image_{i}'] = ''
                gui_callback(f"No Image_{i} assigned")

        gui_callback(f"Final new_record: {new_record}")
        new_record['Success'] = True
        return new_record

    except Exception as e:
        lot_number = airtable_record.get('fields', {}).get('Lot Number', 'Unknown')
        error_message = f"Error processing Lot Number {lot_number}: {str(e)}"
        gui_callback(f"Error: {error_message}")
        gui_callback(f"Traceback: {traceback.format_exc()}")
        return {'Lot Number': lot_number, 'Failure Message': error_message, 'Success': False}


def get_event(event_id: str) -> Event:
    try:
        return Event.objects.get(event_id=event_id)
    except Event.DoesNotExist:
        raise ValueError(f"Event with ID {event_id} does not exist")


async def organize_images(event: Event) -> None:
    image_files = ImageMetadata.objects.filter(event=event)
    image_files = await sync_to_async(list)(image_files)
    for image in image_files:
        if image.filename.endswith(("_1.jpeg", "_1.png", '_1.jpg')):
            image.is_primary = True
            await sync_to_async(image.save)()


class AuctionFormatter:
    def __init__(self, event, gui_callback, should_stop, callback, selected_warehouse, starting_price, task_id):
        self.event = event
        self.auction_id = event.event_id
        self.gui_callback = gui_callback
        self.should_stop = should_stop
        self.callback = callback
        self.selected_warehouse = selected_warehouse
        self.starting_price = starting_price
        self.task_id = task_id

        config_manager.set_active_warehouse(selected_warehouse)

        self.final_csv_content = None
        self.website_login_url = config_manager.get_global_var('website_login_url')
        self.import_csv_url = config_manager.get_global_var('import_csv_url')
        self.notification_email = config_manager.get_global_var('notification_email')

    def should_continue(self, message):
        if self.should_stop.is_set():
            self.gui_callback(message)
            return False
        return True

    async def save_screenshot(self, page, name="error_screenshot"):
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        screenshot_path = f"/tmp/{name}_{timestamp}.png"
        await page.screenshot(path=screenshot_path)
        self.gui_callback(f"Screenshot saved: {screenshot_path}")

    def get_maule_login_credentials(self):
        # Temporarily set the active warehouse to Maule
        original_warehouse = config_manager.active_warehouse
        config_manager.set_active_warehouse("Maule Warehouse")

        bid_username = config_manager.get_warehouse_var('bid_username')
        bid_password = config_manager.get_warehouse_var('bid_password')

        # Reset the active warehouse to the original selection
        config_manager.set_active_warehouse(original_warehouse)

        self.gui_callback('Note: Using Maule warehouse credentials for auction site login, regardless of selected warehouse.')
        return bid_username, bid_password

    async def login_to_website(self, page, username, password):
        if not self.should_continue("Login operation stopped by user."):
            return False

        self.gui_callback("Logging In...")
        try:
            self.gui_callback(f"Navigating to {self.website_login_url}")
            await page.goto(self.website_login_url)
            await page.wait_for_load_state('networkidle', timeout=60000)

            self.gui_callback("Waiting for username field to be present...")
            username_field = await page.wait_for_selector("#username", state="visible", timeout=90000)

            if not username_field:
                self.gui_callback("Username field not found")
                await self.save_screenshot(page, "username_field_not_found")
                return False

            self.gui_callback("Entering credentials...")
            await page.fill("#username", username)
            await page.fill("#password", password)

            if not self.should_continue("Login operation stopped before finalizing."):
                return False

            self.gui_callback("Submitting login form...")
            sign_in_button = await page.wait_for_selector('input[type="submit"][value="Sign In"]', state="visible", timeout=90000)
            if sign_in_button:
                await sign_in_button.click()
            else:
                self.gui_callback("Sign in button not found")
                await self.save_screenshot(page, "sign_in_button_not_found")
                return False

            self.gui_callback("Waiting for login to complete...")
            await page.wait_for_load_state('networkidle', timeout=60000)

            # Check if login was successful
            if "logon" in page.url.lower() or "login" in page.url.lower():
                self.gui_callback("Login failed. Still on login page.")
                await self.save_screenshot(page, "login_failure_still_on_login_page")
                return False

            self.gui_callback("Login successful.")
            return True

        except Exception as e:
            self.gui_callback(f"Login failed: Unexpected error. Error: {str(e)}")
            self.gui_callback(f"Current URL: {page.url}")
            await self.save_screenshot(page, "login_failure_unexpected")
            return False

    async def upload_csv_to_website(self, page, csv_content):
        temp_file_path = None
        try:
            # Check if we're already logged in
            if "Account/LogOn" in page.url:
                self.gui_callback("Not logged in. Proceeding with login...")
                username, password = self.get_maule_login_credentials()
                login_success = await self.login_to_website(page, username, password)
                if not login_success:
                    self.gui_callback("Error: Failed to log in. Aborting CSV upload.")
                    return False
            else:
                self.gui_callback("Already logged in. Proceeding with CSV upload...")

            self.gui_callback("Navigating to ImportCSV URL...")
            await page.goto(self.import_csv_url)
            await page.wait_for_load_state('networkidle', timeout=60000)

            self.gui_callback("Waiting for form to load...")
            try:
                await page.wait_for_selector("#CsvImportForm", state="visible", timeout=60000)
            except Exception as e:
                self.gui_callback(f"Error: Form not found. {str(e)}")
                await self.save_screenshot(page, 'form_not_found')
                return False

            self.gui_callback("Unchecking 'Validate Data ONLY' checkbox...")
            try:
                await page.evaluate("""
                () => {
                    var checkbox = document.querySelector('input[name="validate"]');
                    var toggle = document.querySelector('.fs-checkbox-toggle');
                    if (checkbox && toggle) {
                        checkbox.checked = false;
                        toggle.classList.remove('fs-checkbox-checked');
                        toggle.classList.add('fs-checkbox-unchecked');
                    }
                }
                """)
            except Exception as e:
                self.gui_callback(f"Error: Failed to uncheck 'Validate Data ONLY'. {str(e)}")
                await self.save_screenshot(page, 'validate_checkbox_error')

            self.gui_callback("Updating report email address...")
            try:
                await page.fill("#Text1", self.notification_email)
            except Exception as e:
                self.gui_callback(f"Error: Failed to update email address. {str(e)}")
                await self.save_screenshot(page, 'email_update_error')

            self.gui_callback("Preparing CSV file for upload...")
            temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.csv')
            temp_file.write(csv_content)
            temp_file_path = temp_file.name
            temp_file.close()

            self.gui_callback("Selecting CSV file...")
            try:
                await page.set_input_files("#file", temp_file_path)
            except Exception as e:
                self.gui_callback(f"Error: Failed to select CSV file. {str(e)}")
                await self.save_screenshot(page, 'file_selection_error')
                return False

            self.gui_callback("Clicking 'Upload CSV' button...")
            try:
                upload_button = await page.wait_for_selector("input.btn.btn-info.btn-sm[type='submit'][value='Upload CSV']", state="visible", timeout=20000)
                if upload_button:
                    await upload_button.click()
                else:
                    self.gui_callback("Error: Upload button not found")
                    await self.save_screenshot(page, 'upload_button_not_found')
                    return False
            except Exception as e:
                self.gui_callback(f"Error: Failed to click upload button. {str(e)}")
                await self.save_screenshot(page, 'upload_click_error')
                return False

            self.gui_callback("Waiting for upload to complete...")
            try:
                await page.wait_for_selector(".alert-success", state="visible", timeout=120000)
                success_message = await page.inner_text(".alert-success")
                self.gui_callback(f"Upload result: {success_message}")

                if "CSV listing import has started" in success_message:
                    self.gui_callback("CSV upload initiated successfully!")
                    return True
                else:
                    self.gui_callback("CSV upload failed.")
                    return False
            except Exception as e:
                self.gui_callback(f"Error: Upload completion not detected. {str(e)}")
                await self.save_screenshot(page, 'upload_completion_error')
                return False

        except Exception as e:
            self.gui_callback(f"Unexpected error during CSV upload: {str(e)}")
            await self.save_screenshot(page, 'unexpected_upload_error')
            return False

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    async def run_auction_formatter(self):
        try:
            RedisTaskStatus.set_status(self.task_id, "STARTED", f"Starting auction formatting for event {self.auction_id}")

            RedisTaskStatus.set_status(self.task_id, "IN_PROGRESS", "Fetching Airtable records")
            airtable_records = await get_cached_airtable_records(
                config_manager.get_warehouse_var('airtable_inventory_base_id'),
                config_manager.get_warehouse_var('airtable_inventory_table_id'),
                config_manager.get_warehouse_var('airtable_send_to_auction_view_id'),
                self.gui_callback,
                config_manager.get_warehouse_var('airtable_api_key')
            )
            RedisTaskStatus.set_status(self.task_id, "IN_PROGRESS", f"Retrieved {len(airtable_records)} records from Airtable")

            if self.should_stop.is_set():
                return

            # Collect image URLs
            download_tasks = []
            for record in airtable_records:
                if self.should_stop.is_set():
                    break

                product_id = str(record["fields"].get("Lot Number", ""))
                record_id = record['id']
                for count in range(1, 11):
                    image_info = record["fields"].get(f"Image {count}")
                    if image_info:
                        url = image_info[0].get("url")
                        if url:
                            download_tasks.append((record_id, url, f"{product_id}_{count}", count))

            # Download and process images concurrently
            self.gui_callback("Downloading and processing images...")
            image_data_dict = defaultdict(list)
            async with aiohttp.ClientSession() as session:
                tasks = []
                for record_id, url, file_name, image_number in download_tasks:
                    if self.should_stop.is_set():
                        break
                    tasks.append(self.download_and_process_image(session, record_id, url, image_number, image_data_dict))

                await asyncio.gather(*tasks)

            if self.should_stop.is_set():
                return

            # Upload images and get URLs
            self.gui_callback("Uploading images...")
            uploaded_image_urls = defaultdict(list)
            tasks = []
            for record_id, images in image_data_dict.items():
                for image_data, image_number in images:
                    if self.should_stop.is_set():
                        break
                    tasks.append(self.upload_image_and_get_url(record_id, image_data, image_number, uploaded_image_urls))

            await asyncio.gather(*tasks)

            if self.should_stop.is_set():
                return

            # Save images to database
            await self.save_images_to_database(uploaded_image_urls)

            # Process records
            self.gui_callback("Processing records...")
            processed_records = []
            failed_records = []
            tasks = []
            for record in airtable_records:
                if self.should_stop.is_set():
                    break
                tasks.append(self.process_record(record, uploaded_image_urls, processed_records, failed_records))

            await asyncio.gather(*tasks)

            if self.should_stop.is_set():
                return

            # Generate CSV content
            csv_content = self.generate_csv_content(processed_records)
            self.final_csv_content = csv_content

            # Save formatted data to the database
            await self.save_formatted_data(csv_content)

            # Upload CSV to website using Playwright
            await self.upload_csv_to_website_playwright(csv_content)

            RedisTaskStatus.set_status(self.task_id, "COMPLETED", "Auction formatting process completed successfully")

        except Exception as e:
            RedisTaskStatus.set_status(self.task_id, "ERROR", f"Error in auction formatting process: {str(e)}")
            self.gui_callback(f"Error in auction formatting process: {str(e)}")
            self.gui_callback(f"Traceback: {traceback.format_exc()}")
        finally:
            await sync_to_async(self.callback)()

    async def download_and_process_image(self, session, record_id, url, image_number, image_data_dict):
        image_data = await download_image_async(url, self.gui_callback)
        if image_data:
            processed_image_data = await process_image_async(image_data, self.gui_callback)
            if processed_image_data:
                image_data_dict[record_id].append((processed_image_data, image_number))

    async def upload_image_and_get_url(self, record_id, image_data, image_number, uploaded_image_urls):
        file_name = f"{record_id}_{image_number}.jpg"
        url = await upload_file_via_ftp_async(file_name, image_data, self.gui_callback)
        if url:
            if not url.startswith("https://"):
                url = "https://" + url
            uploaded_image_urls[record_id].append((url, image_number))

    async def save_images_to_database(self, uploaded_image_urls):
        for record_id, urls in uploaded_image_urls.items():
            for url, image_number in urls:
                await sync_to_async(ImageMetadata.objects.create)(
                    event=self.event,
                    filename=f"{record_id}_{image_number}.jpg",
                    is_primary=(image_number == 1),
                    image=url
                )

    async def process_record(self, record, uploaded_image_urls, processed_records, failed_records):
        result = process_single_record(
            record, uploaded_image_urls, self.auction_id, self.selected_warehouse, self.starting_price, self.gui_callback
        )
        if result.get('Success', False):
            processed_records.append(result)
        else:
            failed_records.append(result)

    def generate_csv_content(self, processed_records):
        df = pd.DataFrame(processed_records)
        # Reorder columns as needed
        csv_content = df.to_csv(index=False)
        return csv_content

    async def save_formatted_data(self, csv_content):
        await sync_to_async(AuctionFormattedData.objects.create)(
            event=self.event,
            csv_data=csv_content
        )

    async def upload_csv_to_website_playwright(self, csv_content):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            username, password = self.get_maule_login_credentials()
            login_success = await self.login_to_website(page, username, password)

            if login_success:
                upload_success = await self.upload_csv_to_website(page, csv_content)
                if upload_success:
                    self.gui_callback("CSV uploaded successfully")
                else:
                    self.gui_callback("CSV upload failed")
            else:
                self.gui_callback("Login to auction site failed")

            await browser.close()


def auction_formatter_main(auction_id, selected_warehouse, starting_price, gui_callback, should_stop, callback, task_id=None):
    config_manager.set_active_warehouse(selected_warehouse)
    event = get_event(auction_id)
    formatter = AuctionFormatter(event, gui_callback, should_stop, callback, selected_warehouse, starting_price, task_id)
    asyncio.run(formatter.run_auction_formatter())
    return formatter
