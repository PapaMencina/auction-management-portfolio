import os
import threading
import time
import re
import ftplib
import tempfile
import traceback
import json
import shutil
import random
import asyncio
from collections import defaultdict
from asgiref.sync import sync_to_async
from playwright.async_api import async_playwright, expect
from typing import List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from auction.models import Event, ImageMetadata, AuctionFormattedData
from PIL import Image, ExifTags
from auction.utils import config_manager

from django.core.wsgi import get_wsgi_application
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auction_webapp.settings")
application = get_wsgi_application()

from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

def auction_formatter_main(auction_id, selected_warehouse, gui_callback, should_stop, callback):
    config_manager.set_active_warehouse(selected_warehouse)
    event = get_event(auction_id)
    formatter = AuctionFormatter(event, gui_callback, should_stop, callback, selected_warehouse)
    asyncio.run(formatter.run_auction_formatter())
    return formatter

# Load configuration
config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'config.json')
config_manager.load_config(config_path)

def get_extension_from_content_disposition(content_disposition: str) -> str:
    filename_match = re.search(r'filename="([^"]+)"', content_disposition)
    if filename_match:
        filename = filename_match.group(1)
        return os.path.splitext(filename)[1][1:]
    return None

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

def download_image(url: str, file_name: str, gui_callback) -> str:
    if not url or not file_name:
        gui_callback("Invalid input: URL and file_name are required")
        return None

    try:
        response = requests.get(url)
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '')
        if content_type.startswith('image/'):
            file_extension = content_type.split("/")[1]
        else:
            file_extension = 'jpg'  # Default to jpg if content-type is not image

        if len(response.content) < 1000:
            gui_callback(f"Image for {file_name} is too small, might be corrupted")
            return None

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as temp_file:
            temp_file.write(response.content)
            return temp_file.name

    except requests.RequestException as e:
        gui_callback(f"Error with {file_name} while trying to download {url}: {e}")
    except Exception as e:
        gui_callback(f"An unexpected error occurred for {file_name}: {e}")

    return None

def process_image(file_path: str, gui_callback, width_threshold: int = 1024, dpi_threshold: int = 72) -> None:
    try:
        with Image.open(file_path) as img:
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
                img.save(file_path, dpi=(dpi_threshold, dpi_threshold))
            else:
                img.save(file_path)

    except Exception as e:
        gui_callback(f"Error processing image {file_path}: {e}")

def convert_webp_to_jpeg(file_path: str, gui_callback) -> str:
    try:
        with Image.open(file_path) as im:
            if im.mode == 'P':
                im = im.convert("RGB")
            new_file_path = os.path.splitext(file_path)[0] + ".jpg"
            im.save(new_file_path, "JPEG")
            return new_file_path
    except Exception as e:
        gui_callback(f"Error converting WebP to JPEG: {e}")
    return file_path

def process_image_wrapper(image_path: str, gui_callback, should_stop: threading.Event) -> str:
    if should_stop.is_set():
        return image_path
    if image_path.endswith(".webp"):
        image_path = convert_webp_to_jpeg(image_path, gui_callback)
    process_image(image_path, gui_callback)
    return image_path

def process_images_in_bulk(downloaded_images_bulk: Dict[str, List[Tuple[str, int]]], gui_callback, should_stop: threading.Event) -> Dict[str, List[Tuple[str, int]]]:
    gui_callback("Processing Images...")
    all_image_paths = []
    record_id_map = {}

    for record_id, image_paths in downloaded_images_bulk.items():
        for image_path, image_number in image_paths:
            all_image_paths.append((image_path, image_number))
            record_id_map[image_path] = record_id

    processed_images = {}
    with ThreadPoolExecutor() as executor:
        future_to_image = {executor.submit(process_image_wrapper, img_path, gui_callback, should_stop): (img_path, img_number) for img_path, img_number in all_image_paths}

        for future in as_completed(future_to_image):
            if should_stop.is_set():
                break

            img_path, img_number = future_to_image[future]
            try:
                result = future.result()
                record_id = record_id_map[img_path]
                processed_images.setdefault(record_id, []).append((result, img_number))
            except Exception as e:
                gui_callback(f"Error processing image {img_path}: {e}")

    return processed_images

def upload_image(file_path: str, gui_callback, should_stop: threading.Event, max_retries: int = 3, retry_delay: int = 10) -> str:
    attempt = 0

    while attempt < max_retries:
        try:
            file_name = os.path.basename(file_path)
            online_image_file_path = upload_file_via_ftp(str(file_name), file_path, gui_callback, should_stop)
            return online_image_file_path

        except Exception as e:
            if "421 Too many connections" in str(e):
                attempt += 1
                gui_callback(f"Failed to upload image due to too many connections. Retry {attempt}/{max_retries} after {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                gui_callback(f"Failed to upload image: {e}")
        return None

def upload_file_via_ftp(file_name: str, local_file_path: str, gui_callback, should_stop: threading.Event, max_retries: int = 3) -> str:
    remote_file_path = config_manager.get_global_var('ftp_remote_path')
    server = config_manager.get_global_var('ftp_server')
    username = config_manager.get_global_var('ftp_username')
    password = config_manager.get_global_var('ftp_password')
    
    retries = 0
    while retries < max_retries and not should_stop.is_set():
        try:
            with ftplib.FTP(server, username, password) as ftp:
                ftp.set_pasv(True)
                ftp.cwd('/')
                ftp.sendcmd('TYPE I')
                remote_path_full = os.path.join(remote_file_path, file_name)

                gui_callback(f"Uploading file {file_name} to {remote_path_full}")
                with open(local_file_path, 'rb') as file:
                    ftp.storbinary(f'STOR {remote_path_full}', file)
                gui_callback(f"File {file_name} uploaded successfully")

            formatted_url = remote_path_full.replace("/public_html", "", 1).lstrip('/')
            return f"https://{formatted_url}"

        except ftplib.error_temp as e:
            gui_callback(f"Temporary FTP error: {e}. Retrying in 5 seconds...")
            retries += 1
            time.sleep(5)
        except Exception as e:
            gui_callback(f"FTP upload error: {e}.")
            break

    gui_callback("Failed to upload after maximum retries.")
    return None

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
        2830823: ["lawn & garden", "garden & outdor"],
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

    return 162733

def get_airtable_records_list(BASE: str, TABLE: str, VIEW: str, gui_callback, airtable_token: str) -> List[Dict]:
    gui_callback("Getting Airtable Records...")
    responseList = []
    offset = ""
    
    myHeaders = {
        "Authorization": f"Bearer {airtable_token}",
        "Content-Type": "application/json",
    }

    while True:
        try:
            url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?view={VIEW}"
            if offset:
                url += f"&offset={offset}"

            gui_callback(f"Requesting URL: {url}")
            response = requests.get(url, headers=myHeaders)
            response.raise_for_status()

            response_json = response.json()
            records = response_json.get("records", [])
            responseList.extend(records)
            gui_callback(f"Retrieved {len(records)} records")

            offset = response_json.get("offset")
            if not offset:
                break
        except Exception as e:
            gui_callback(f"Exception occurred: {e}")
            break

    gui_callback(f"Retrieved a total of {len(responseList)} records from Airtable")
    return responseList

def text_shortener(inputText: str, strLen: int) -> str:
    if len(inputText) > strLen:
        end = inputText.rfind(' ', 0, strLen)
        return inputText[:end if end != -1 else strLen].strip()
    return inputText

def format_msrp(msrp: float) -> str:
    if msrp >= 15:
        return "5.00"
    elif msrp <= 10:
        return "1.00"
    else:
        return "2.50"
    
def format_field(label: str, value: str) -> str:
    return f"{label}: {value}" if value is not None and str(value).strip() else ""

def format_html_field(field_name: str, value: str) -> str:
    return f"<b>{field_name}</b>: {value}<br>" if value else ""
    
def get_image_url(airtable_record: Dict, count: int) -> Tuple[str, str, int]:
    url = airtable_record.get("fields", {}).get(f"Image {count}", [{}])[0].get("url", "")
    filename = url.split('/')[-1] if url else ""
    print(f"Airtable Image {count} URL: {url}")
    print(f"Airtable Image {count} Filename: {filename}")
    return url, filename, count

class AuctionFormatter:
    def __init__(self, event, gui_callback, should_stop, callback, selected_warehouse):
        self.event = event
        self.Auction_ID = event.event_id
        self.gui_callback = gui_callback
        self.should_stop = should_stop
        self.callback = callback
        self.selected_warehouse = selected_warehouse
        
        config_manager.set_active_warehouse(selected_warehouse)
        
        self.final_csv_path = None
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

            self.gui_callback("Step 1: Navigating to ImportCSV URL...")
            await page.goto("https://bid.702auctions.com/Admin/ImportCSV")
            await page.wait_for_load_state('networkidle', timeout=60000)
            
            self.gui_callback("Step 2: Waiting for form to load...")
            try:
                await page.wait_for_selector("#CsvImportForm", state="visible", timeout=60000)
            except Exception as e:
                self.gui_callback(f"Error: Form not found. {str(e)}")
                await page.screenshot(path='form_not_found.png')
                return False
            
            self.gui_callback("Step 3: Unchecking 'Validate Data ONLY' checkbox...")
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
                await page.screenshot(path='validate_checkbox_error.png')
            
            self.gui_callback("Step 4: Updating report email address...")
            try:
                await page.fill("#Text1", "matthew@702auctions.com")
            except Exception as e:
                self.gui_callback(f"Error: Failed to update email address. {str(e)}")
                await page.screenshot(path='email_update_error.png')
            
            self.gui_callback("Step 5: Preparing CSV file for upload...")
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.csv') as temp_file:
                temp_file.write(csv_content)
                temp_file_path = temp_file.name
            
            self.gui_callback("Step 6: Selecting CSV file...")
            try:
                await page.set_input_files("#file", temp_file_path)
            except Exception as e:
                self.gui_callback(f"Error: Failed to select CSV file. {str(e)}")
                await page.screenshot(path='file_selection_error.png')
                return False
            
            self.gui_callback("Step 7: Clicking 'Upload CSV' button...")
            try:
                upload_button = await page.wait_for_selector("input.btn.btn-info.btn-sm[type='submit'][value='Upload CSV']", state="visible", timeout=20000)
                if upload_button:
                    await upload_button.click()
                else:
                    self.gui_callback("Error: Upload button not found")
                    await page.screenshot(path='upload_button_not_found.png')
                    return False
            except Exception as e:
                self.gui_callback(f"Error: Failed to click upload button. {str(e)}")
                await page.screenshot(path='upload_click_error.png')
                return False
            
            self.gui_callback("Step 8: Waiting for upload to complete...")
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
                await page.screenshot(path='upload_completion_error.png')
                return False
            
        except Exception as e:
            self.gui_callback(f"Unexpected error during CSV upload: {str(e)}")
            await page.screenshot(path='unexpected_upload_error.png')
            return False
        
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    async def run_auction_formatter(self):
        try:
            self.gui_callback("Starting auction formatting process...")
            
            # Fetch Airtable records
            airtable_records = await self.fetch_airtable_records()

            # Process images
            self.gui_callback("Collecting image URLs...")
            download_tasks = collect_image_urls(airtable_records, self.should_stop)
            
            self.gui_callback("Downloading images...")
            downloaded_images = await self.download_images_bulk(download_tasks)
            
            self.gui_callback("Processing images...")
            processed_images = await self.process_images_in_bulk(downloaded_images)
            
            self.gui_callback("Uploading images...")
            uploaded_image_urls = await self.upload_images_and_get_urls(processed_images)

            # Save uploaded image URLs to the database
            self.gui_callback("Saving image metadata to database...")
            await save_images_to_database(self.event, uploaded_image_urls)

            # Process records
            self.gui_callback("Processing records...")
            processed_records, failed_records = await self.process_records_concurrently(
                airtable_records, uploaded_image_urls
            )

            # Create and format CSV
            if processed_records:
                self.gui_callback("Creating CSV content...")
                csv_content = await self.processed_records_to_df(processed_records)
                self.final_csv_content = await self.format_final_csv(csv_content)

            # Upload CSV to website
            if self.final_csv_content:
                self.gui_callback("Initializing browser...")
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()
                    
                    # Use Maule Warehouse credentials
                    username, password = self.get_maule_login_credentials()
                    self.gui_callback("CSV Content Preview:")
                    self.gui_callback(self.final_csv_content[:1000])  # Print first 1000 characters of CSV

                    self.gui_callback("Logging into website...")
                    login_success = await self.login_to_website(page, username, password)
                    
                    if login_success:
                        self.gui_callback("Uploading CSV to 702 Auctions...")
                        upload_success = await self.upload_csv_to_website(page, self.final_csv_content)
                        if upload_success:
                            self.gui_callback("CSV uploaded successfully to 702 Auctions.")
                        else:
                            self.gui_callback("CSV upload to 702 Auctions failed.")
                            await self.save_screenshot(page, "csv_upload_failure")
                    else:
                        self.gui_callback("Login to 702 Auctions failed.")
                        await self.save_screenshot(page, "login_failure")
                    
                    self.gui_callback(f"Final URL: {page.url}")
                    await browser.close()

            # Process failed records
            if failed_records:
                self.gui_callback("Processing failed records...")
                self.failed_records_csv_content = await self.failed_records_csv(failed_records)

            # Organize images
            self.gui_callback("Organizing images...")
            await self.organize_images()

            self.gui_callback("Auction formatting process completed successfully.")

        except Exception as e:
            self.gui_callback(f"Error in auction formatting process: {str(e)}")
            self.gui_callback(f"Traceback: {traceback.format_exc()}")
        finally:
            self.callback()

    async def format_final_csv(self, csv_content):
        try:
            from io import StringIO
            data = pd.read_csv(StringIO(csv_content))
            self.gui_callback(f"Initial data loaded with {len(data)} records.")

            data['UPC'] = data['UPC'].astype(str)
            data['MSRP'] = pd.to_numeric(data['MSRP'], errors='coerce').round(2)

            sorted_data = data.sort_values(by='MSRP', ascending=False)

            top_50_items = sorted_data[~sorted_data['Subtitle'].str.contains('missing|damaged|no', case=False, na=False)].head(50)
            remaining_items = sorted_data[~sorted_data.index.isin(top_50_items.index)].sample(frac=1).reset_index(drop=True)

            processed_top_50 = self.process_items_avoid_adjacency(top_50_items)
            processed_remaining = self.process_items_avoid_adjacency(remaining_items)

            final_data = pd.concat([processed_top_50, processed_remaining]).reset_index(drop=True)

            csv_content = final_data.to_csv(index=False)
            
            # Use sync_to_async for database operation
            await self.save_formatted_data(csv_content)

            self.gui_callback(f"Formatted data saved to database for event {self.Auction_ID}")
            return csv_content
        except Exception as e:
            self.gui_callback(f"Error formatting final CSV: {e}")
            return None

    @sync_to_async
    def save_formatted_data(self, csv_content):
        AuctionFormattedData.objects.create(
            event=self.event,
            csv_data=csv_content
        )

    def process_items_avoid_adjacency(self, items):
        processed_items = []
        title_buffer = {}

        for _, row in items.iterrows():
            title = row['Title']
            if title in title_buffer:
                title_buffer[title].append(row)
            else:
                if processed_items and processed_items[-1]['Title'] == title:
                    title_buffer[title] = [row]
                else:
                    processed_items.append(row)

            if random.random() < 0.2:
                for buffered_title in list(title_buffer.keys()):
                    if buffered_title != title and title_buffer[buffered_title]:
                        processed_items.append(title_buffer[buffered_title].pop(0))
                        if not title_buffer[buffered_title]:
                            del title_buffer[buffered_title]
                        break

        for buffered_items in title_buffer.values():
            for item in buffered_items:
                insert_position = self.find_insert_position(processed_items, item['Title'])
                processed_items.insert(insert_position, item)

        return pd.DataFrame(processed_items)
    
    # Add new async methods
    async def fetch_airtable_records(self):
        return await sync_to_async(get_airtable_records_list)(
            config_manager.get_warehouse_var('airtable_inventory_base_id'),
            config_manager.get_warehouse_var('airtable_inventory_table_id'),
            config_manager.get_warehouse_var('airtable_send_to_auction_view_id'),
            self.gui_callback,
            config_manager.get_warehouse_var('airtable_api_key')
        )

    async def download_images_bulk(self, download_tasks):
        async def download_image_async(url, file_name):
            return await sync_to_async(download_image)(url, file_name, self.gui_callback)

        image_paths = defaultdict(list)
        for record_id, url, file_name, image_number in download_tasks:
            if self.should_stop.is_set():
                break
            downloaded_path = await download_image_async(url, file_name)
            if downloaded_path:
                image_paths[record_id].append((downloaded_path, image_number))
        return dict(image_paths)

    async def process_images_in_bulk(self, downloaded_images):
        async def process_image_async(image_path):
            return await sync_to_async(process_image_wrapper)(image_path, self.gui_callback, self.should_stop)

        processed_images = defaultdict(list)
        for record_id, image_paths in downloaded_images.items():
            for image_path, image_number in image_paths:
                if self.should_stop.is_set():
                    break
                processed_path = await process_image_async(image_path)
                processed_images[record_id].append((processed_path, image_number))
        return dict(processed_images)

    async def upload_images_and_get_urls(self, processed_images):
        async def upload_image_async(image_path):
            try:
                return await sync_to_async(upload_image)(image_path, self.gui_callback, self.should_stop)
            except Exception as e:
                self.gui_callback(f"Error uploading image {image_path}: {e}")
                return None  # Return None instead of raising an exception

        uploaded_image_urls = defaultdict(list)
        for record_id, image_paths in processed_images.items():
            for image_path, image_number in image_paths:
                if self.should_stop.is_set():
                    break
                url = await upload_image_async(image_path)
                if url:
                    if not url.startswith("https://"):
                        url = "https://" + url
                    uploaded_image_urls[record_id].append((url, image_number))
                else:
                    self.gui_callback(f"Failed to upload image for record {record_id}, image number {image_number}")
        return dict(uploaded_image_urls)

    async def process_records_concurrently(self, airtable_records, uploaded_image_urls):
        async def process_record_async(record):
            return await sync_to_async(process_single_record)(
                record, uploaded_image_urls, self.Auction_ID, self.selected_warehouse, self.gui_callback
            )

        processed_records = []
        failed_records = []
        for record in airtable_records:
            if self.should_stop.is_set():
                break
            result = await process_record_async(record)
            if result.get('Success', False):
                processed_records.append(result)
            else:
                failed_records.append(result)
        return processed_records, failed_records

    async def processed_records_to_df(self, processed_records):
        return await sync_to_async(processed_records_to_df)(processed_records, self.Auction_ID, self.gui_callback)

    async def failed_records_csv(self, failed_records):
        return await sync_to_async(failed_records_csv)(failed_records, self.Auction_ID, self.gui_callback)

    async def organize_images(self):
        try:
            await asyncio.wait_for(organize_images(self.event), timeout=300)
            self.gui_callback("Images organized successfully")
        except asyncio.TimeoutError:
            self.gui_callback("Organizing images timed out after 5 minutes")
        except Exception as e:
            self.gui_callback(f"Error organizing images: {str(e)}")
            self.gui_callback(f"Traceback: {traceback.format_exc()}")

    def find_insert_position(self, processed_items, title):
        for i in range(len(processed_items) - 1, -1, -1):
            if processed_items[i]['Title'] != title:
                return i + 1
        return 0

def collect_image_urls(airtable_records: List[Dict], should_stop: threading.Event) -> List[tuple]:
    download_tasks = []
    for record in airtable_records:
        if should_stop.is_set():
            break

        product_id = str(record["fields"].get("Lot Number", ""))
        record_id = record['id']
        for count in range(1, 11):
            image_url, filename, image_number = get_image_url(record, count)
            if image_url:
                file_name = f"{product_id}_{count}"
                download_tasks.append((record_id, image_url, file_name, image_number))
    return download_tasks

def download_images_bulk(download_tasks: List[tuple], gui_callback, should_stop: threading.Event) -> Dict[str, List[Tuple[str, int]]]:
    gui_callback("Downloading Images...")
    image_paths = {}

    with ThreadPoolExecutor(max_workers=7) as executor:
        future_to_task = {executor.submit(download_image, url, file_name, gui_callback): (record_id, file_name, image_number) for record_id, url, file_name, image_number in download_tasks}

        for future in as_completed(future_to_task):
            if should_stop.is_set():
                break

            record_id, file_name, image_number = future_to_task[future]
            try:
                downloaded_path = future.result()
                if downloaded_path:
                    image_paths.setdefault(record_id, []).append((downloaded_path, image_number))
            except Exception as e:
                gui_callback(f"Error downloading image for {file_name}: {e}")

    return image_paths

def upload_images_and_get_urls(downloaded_images: Dict[str, List[Tuple[str, int]]], gui_callback, should_stop: threading.Event) -> Dict[str, List[Tuple[str, int]]]:
    gui_callback("Uploading Images...")
    uploaded_image_urls = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_image = {executor.submit(upload_image, image_path, gui_callback, should_stop): (record_id, image_path, image_number) for record_id, image_paths in downloaded_images.items() for image_path, image_number in image_paths}

        for future in as_completed(future_to_image):
            if should_stop.is_set():
                return uploaded_image_urls

            record_id, image_path, image_number = future_to_image[future]
            try:
                url = future.result()
                if url:
                    if not url.startswith("https://"):
                        url = "https://" + url
                    uploaded_image_urls.setdefault(record_id, []).append((url, image_number))
                    gui_callback(f"Uploaded image for record {record_id}: {url}")
                else:
                    gui_callback(f"Failed to upload image for record {record_id}")
            except Exception as e:
                gui_callback(f"Error uploading image {image_path}: {e}")

    gui_callback(f"Final uploaded_image_urls: {uploaded_image_urls}")
    return uploaded_image_urls

@sync_to_async
def save_images_to_database(event: Event, uploaded_image_urls: Dict[str, List[Tuple[str, int]]]):
    for record_id, urls in uploaded_image_urls.items():
        for url, image_number in urls:
            ImageMetadata.objects.create(
                event=event,
                filename=f"{record_id}_{image_number}.jpg",
                is_primary=(image_number == 1),
                image=url
            )

def process_single_record(airtable_record: Dict, uploaded_image_urls: Dict[str, List[Tuple[str, int]]], Auction_ID: str, selected_warehouse: str, gui_callback) -> Dict:
    try:
        newRecord = {}
        record_id = airtable_record.get('id', '')
        gui_callback(f"Processing record ID: {record_id}")

        # Basic information
        newRecord["AuctionCount"] = airtable_record["fields"].get("Auction Count", "")
        newRecord["Photo Taker"] = airtable_record["fields"].get("Clerk", "")
        newRecord["Size"] = airtable_record["fields"].get("Size", "")
        newRecord["UPC"] = str(airtable_record["fields"].get("UPC", ""))
        newRecord["ID"] = record_id
        product_id = str(airtable_record["fields"].get("Lot Number", ""))
        newRecord["LotNumber"] = newRecord["Lot Number"] = str(product_id)
        newRecord["Other Notes"] = airtable_record["fields"].get("Notes", "")
        newRecord["MSRP"] = airtable_record["fields"].get("MSRP", "0.00")
        newRecord["Truck"] = airtable_record["fields"].get("Shipment", "")
        newRecord["Category_not_formatted"] = airtable_record["fields"].get("Category", "")
        newRecord["Amazon ID"] = airtable_record["fields"].get("B00 ASIN", "")
        newRecord["Item Condition"] = airtable_record["fields"].get("Condition", "")
        newRecord["HibidSearchText"] = airtable_record["fields"].get("Description", "")
        newRecord["FullTitle"] = airtable_record["fields"].get("Product Name", "")
        newRecord["Location"] = airtable_record["fields"].get("Location", "")

        # Format fields
        base_fields = [
            format_field("Description", newRecord['FullTitle']),
            format_field("MSRP", newRecord['MSRP']),
            format_field("Condition", newRecord['Item Condition']),
            format_field("Notes", newRecord['Other Notes']),
            format_field("Other info", newRecord['HibidSearchText']),
            format_field("Lot Number", product_id)
        ]

        html_base_fields = [
            format_html_field("Description", newRecord['FullTitle']),
            format_html_field("MSRP", newRecord['MSRP']),
            format_html_field("Condition", newRecord['Item Condition']),
            format_html_field("Notes", newRecord['Other Notes']),
            format_html_field("Other info", newRecord['HibidSearchText']),
            format_html_field("Lot Number", product_id)
        ]

        # HiBid and Description fields
        hibid_message = f"This item is live on our site, 702 Auctions.com. To view additional images and bid on this item, CLICK THE LINK ABOVE or visit bid.702auctions.com and search for lot number {newRecord['LotNumber']}."
        newRecord["HiBid"] = " -- ".join([hibid_message] + [field for field in base_fields if field])
        newRecord["Description"] = ''.join(field for field in html_base_fields if field)

        # Standard fields
        newRecord["Currency"] = "USD"
        newRecord["ListingType"] = "Auction"
        newRecord["Seller"] = "702Auctions"
        newRecord["EventID"] = Auction_ID
        newRecord["Region"] = "88850842" if selected_warehouse == "Maule Warehouse" else "88850843" if selected_warehouse == "Sunrise Warehouse" else ""
        newRecord["Source"] = "AMZ FC"
        newRecord["IsTaxable"] = "TRUE"
        newRecord["Quantity"] = "1"

        # Title and Category
        title = airtable_record["fields"].get("Product Name", "")
        if selected_warehouse == "Sunrise Warehouse":
            title = "OFFSITE " + title
        newRecord["Title"] = text_shortener(title, 80)
        newRecord["Category"] = category_converter(newRecord.get("Category_not_formatted", ""))

        # Price and Subtitle
        auction_count = int(newRecord.get("AuctionCount", 0))
        newRecord["Price"] = "5.00" if auction_count == 1 else "2.50" if auction_count == 2 else "1.00" if auction_count >= 3 else "5.00"
        newRecord["Subtitle"] = format_subtitle(
            auction_count,
            float(newRecord.get("MSRP", 0)),
            newRecord.get("Other Notes", "")
        )

        # Handle image ordering
        if record_id in uploaded_image_urls:
            gui_callback(f"Found uploaded images for record ID: {record_id}")
            gui_callback(f"Uploaded image URLs: {uploaded_image_urls[record_id]}")
            
            # Sort the uploaded images by image number
            sorted_images = sorted(uploaded_image_urls[record_id], key=lambda x: x[1])
            
            for i in range(1, 11):
                newRecord[f'Image_{i}'] = ''  # Initialize all image fields as empty
            
            for url, image_number in sorted_images:
                if image_number <= 10:  # Ensure we only use up to 10 images
                    newRecord[f'Image_{image_number}'] = url
                    gui_callback(f"Assigned Image_{image_number}: {url}")
        else:
            gui_callback(f"No uploaded images found for record ID: {record_id}")
            # Add empty image fields
            for i in range(1, 11):
                newRecord[f'Image_{i}'] = ''
                gui_callback(f"No Image_{i} assigned")

        gui_callback(f"Final newRecord: {newRecord}")
        newRecord['Success'] = True
        return newRecord

    except Exception as e:
        lot_number = airtable_record.get('fields', {}).get('Lot Number', 'Unknown')
        error_message = f"Error processing Lot Number {lot_number}: {str(e)}"
        gui_callback(f"Error: {error_message}")
        gui_callback(f"Traceback: {traceback.format_exc()}")
        return {'Lot Number': lot_number, 'Failure Message': error_message, 'Success': False}

def process_records_concurrently(airtable_records: List[Dict], uploaded_image_urls: Dict[str, List[str]], gui_callback, auction_id: str, selected_warehouse: str, should_stop: threading.Event) -> Tuple[List[Dict], List[Dict]]:
    gui_callback("Creating CSV...")
    processed_records = []
    failed_records = []

    with ThreadPoolExecutor() as executor:
        future_to_record = {executor.submit(process_single_record, record, uploaded_image_urls, auction_id, selected_warehouse, gui_callback): record for record in airtable_records}

        for future in as_completed(future_to_record):
            if should_stop.is_set():
                return processed_records, failed_records

            try:
                result = future.result()
                if result.get('Success', False):
                    processed_records.append(result)
                else:
                    failed_records.append(result)
            except Exception as e:
                gui_callback(f"Error processing record: {e}")

    gui_callback(f"Processed {len(processed_records)} records, {len(failed_records)} failed.")
    return processed_records, failed_records

def failed_records_csv(failed_records: List[Dict], Auction_ID: str, gui_callback) -> str:
    failed_dataframe = pd.DataFrame(failed_records, columns=['Lot Number', 'Failure Message'])
    csv_content = failed_dataframe.to_csv(index=False)
    gui_callback(f'Processed {len(failed_records)} failed records.')
    return csv_content

def processed_records_to_df(processed_records: List[Dict], Auction_ID: str, gui_callback) -> str:
    df = pd.DataFrame(processed_records)
    column_order = ["EventID", "LotNumber", "Seller", "Category", "Region", "ListingType", "Currency",
                    "Title", "Subtitle", "Description", "Price", "Quantity", "IsTaxable", 
                    "Image_1", "Image_2", "Image_3", "Image_4", "Image_5", 
                    "Image_6", "Image_7", "Image_8", "Image_9", "Image_10",
                    "YouTubeID", "PdfAttachments", "Bold", "Badge", "Highlight", "ShippingOptions", 
                    "Duration", "StartDTTM", "EndDTTM", "AutoRelist", "GoodTilCanceled", "Working Condition",
                    "UPC", "Truck", "Source", "Size", "Photo Taker", "Packaging", "Other Notes", "MSRP", 
                    "Lot Number", "Location", "Item Condition", "ID", "Amazon ID", "HiBid", "AuctionCount"]
    
    df = df.reindex(columns=column_order, fill_value='')
    
    # Check image URLs
    for i in range(1, 11):
        col_name = f'Image_{i}'
        if col_name in df.columns:
            non_empty_urls = df[df[col_name] != ''][col_name]
            if not non_empty_urls.empty:
                gui_callback(f"Sample of {col_name} URLs:")
                gui_callback(non_empty_urls.head().to_string())
            else:
                gui_callback(f"No non-empty URLs found for {col_name}")
        else:
            gui_callback(f"{col_name} column not found in DataFrame")

    csv_content = df.to_csv(index=False)
    gui_callback(f'Processed {len(processed_records)} records successfully.')
    gui_callback(f'CSV content preview:\n{csv_content[:1000]}')

    return csv_content

def get_event(event_id: str) -> Event:
    try:
        return Event.objects.get(event_id=event_id)
    except Event.DoesNotExist:
        raise ValueError(f"Event with ID {event_id} does not exist")

@sync_to_async
def organize_images(event: Event) -> None:
    print(f"Starting to organize images for event {event.id}")
    image_files = ImageMetadata.objects.filter(event=event)
    print(f"Found {image_files.count()} images to process")
    for image in image_files:
        print(f"Processing image: {image.filename}")
        if image.filename.endswith(("_1.jpeg", "_1.png", '_1.jpg')):
            print(f"Marking image {image.filename} as primary")
            image.is_primary = True
            image.save()
    print("Finished organizing images")