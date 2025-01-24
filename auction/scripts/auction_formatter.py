import os
import time
import re
import traceback
import json
import random
import asyncio
import tempfile
import cachetools
import csv
import gc

from typing import Optional
from asyncio import Semaphore
from collections import defaultdict
from typing import List, Dict, Tuple
from io import BytesIO, StringIO
from contextlib import asynccontextmanager

# Django imports
from django.core.wsgi import get_wsgi_application
from django.conf import settings
from django.db import transaction
from asgiref.sync import sync_to_async, async_to_sync
from celery import shared_task
from celery.utils.log import get_task_logger

# Third-party imports
import aiohttp
from minio import Minio
from minio.error import S3Error
import pandas as pd
from PIL import Image, ExifTags
from playwright.async_api import async_playwright

# Local imports
from auction.models import Event, ImageMetadata, AuctionFormattedData
from auction.utils import config_manager
from auction.utils.redis_utils import RedisTaskStatus

logger = get_task_logger(__name__)

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auction_webapp.settings")
application = get_wsgi_application()

# Load configuration
config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'config.json'
)
config_manager.load_config(config_path)

# Initialize MinIO client
minio_client = Minio(
    endpoint=config_manager.get_global_var('minio_endpoint'),
    access_key=config_manager.get_global_var('minio_access_key'),
    secret_key=config_manager.get_global_var('minio_secret_key'),
    secure=config_manager.get_global_var('minio_secure')
)

# Ensure bucket exists
bucket_name = config_manager.get_global_var('minio_bucket')
try:
    if not minio_client.bucket_exists(bucket_name):
        minio_client.make_bucket(bucket_name)
        minio_client.set_bucket_policy(bucket_name, {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}/*"]
                }
            ]
        })
except S3Error as e:
    logger.error(f"Error setting up MinIO bucket: {str(e)}")

class RateLimiter:
    def __init__(self, rate_limit, time_period):
        self.rate_limit = rate_limit
        self.time_period = time_period
        self.semaphore = None

    async def acquire(self):
        if self.semaphore is None:
            self.semaphore = asyncio.Semaphore(self.rate_limit)
        await self.semaphore.acquire()
        asyncio.create_task(self.release_after_delay())

    async def release_after_delay(self):
        await asyncio.sleep(self.time_period)
        self.semaphore.release()

rate_limiter = RateLimiter(rate_limit=20, time_period=1)  # Increased rate limit since MinIO is more performant

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

async def process_image_async(image_data: bytes, gui_callback, width_threshold: int = 1024, dpi_threshold: int = 72) -> Optional[bytes]:
    try:
        if not image_data:
            gui_callback("Error: Empty image data")
            return None

        # Use BytesIO to avoid disk I/O
        with Image.open(BytesIO(image_data)) as img:
            # Verify that the image was opened successfully
            if not img:
                gui_callback("Error: Failed to open image")
                return None

            # Convert to RGB early if needed
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')

            # Handle orientation
            orientation = get_image_orientation(img)
            if orientation == 6:
                img = img.transpose(Image.ROTATE_270)
            elif orientation == 8:
                img = img.transpose(Image.ROTATE_90)

            # Resize if needed (using LANCZOS for better quality)
            width, height = img.size
            if width > width_threshold:
                new_width = width_threshold
                new_height = int(height * (new_width / width))
                try:
                    img = img.resize((new_width, new_height), Image.LANCZOS)
                except Exception as resize_error:
                    gui_callback(f"Error resizing image: {str(resize_error)}")
                    return None

            # Set DPI
            if img.info.get('dpi', (72, 72))[0] > dpi_threshold:
                img.info['dpi'] = (dpi_threshold, dpi_threshold)

            # Optimize output
            output = BytesIO()
            img.save(output, 
                    format='JPEG', 
                    quality=85,  # Slightly reduced quality for better performance
                    optimize=True,
                    progressive=True)
            return output.getvalue()

    except (IOError, OSError) as e:
        gui_callback(f"Error opening or processing image: {str(e)}")
    except Image.DecompressionBombError:
        gui_callback("Error: Image is too large to process")
    except Exception as e:
        gui_callback(f"Unexpected error processing image: {str(e)}")
    
    return None

async def upload_file_to_minio(file_name: str, file_content: bytes, gui_callback) -> Optional[str]:
    try:
        await rate_limiter.acquire()
        
        # Create a temporary file to store the content
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name

        try:
            # Upload to MinIO
            minio_client.fput_object(
                bucket_name,
                file_name,
                temp_file_path,
                content_type='image/jpeg'
            )
            
            # Generate public URL
            url = f"https://{config_manager.get_global_var('minio_endpoint')}/{bucket_name}/{file_name}"
            gui_callback(f"File uploaded successfully: {url}")
            return url

        finally:
            # Clean up temporary file
            os.unlink(temp_file_path)

    except Exception as e:
        gui_callback(f"Error uploading to MinIO: {str(e)}")
        return None

async def upload_file_via_ftp_async(file_name, file_content, gui_callback, should_stop, max_retries=3):
    """Compatibility wrapper for MinIO upload"""
    return await upload_file_to_minio(file_name, file_content, gui_callback)

@cachetools.cached(cache=cachetools.TTLCache(maxsize=100, ttl=3600))
async def get_cached_airtable_records(BASE: str, TABLE: str, VIEW: str, gui_callback, airtable_token: str) -> List[Dict]:
    return await get_airtable_records_list(BASE, TABLE, VIEW, gui_callback, airtable_token)

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
                url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?view={VIEW}&pageSize=100"  # Increased page size
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
        record_id = airtable_record.get('id', '')
        gui_callback(f"Processing record ID: {record_id}")

        fields = airtable_record.get('fields', {})

        new_record = {
            'EventID': auction_id,
            'LotNumber': ('M' if selected_warehouse == "Maule Warehouse" else 'S' if selected_warehouse == "Sahara Warehouse" else '') + str(fields.get("Lot Number", "")),
            'Lot Number': str(fields.get("Lot Number", "")),  # Duplicate as in original
            'Seller': "702Auctions",
            'ConsignorNumber': "",
            'Category_not_formatted': fields.get("Category", ""),
            'Category': category_converter(fields.get("Category", "")),
            'Region': "88850842" if selected_warehouse == "Maule Warehouse" else "88850843" if selected_warehouse == "Sahara Warehouse" else "",
            'ListingType': "Auction",
            'Currency': "USD",
            'Title': text_shortener(fields.get("Product Name", ""), 80),
            'Subtitle': "",  # Will be set later
            'Description': "",  # Will be set later
            'Price': starting_price,
            'Quantity': "1",
            'IsTaxable': "TRUE",
            'YouTubeID': "",
            'PdfAttachments': "",
            'Bold': "false",
            'Badge': "",
            'Highlight': "false",
            'ShippingOptions': "",
            'Duration': "",
            'StartDTTM': "",
            'EndDTTM': "",
            'AutoRelist': "0",
            'GoodTilCanceled': "false",
            'Working Condition': fields.get("Working Condition", ""),
            'UPC': "",  # Will be set later with specific logic
            'Truck': fields.get("Shipment", ""),
            'Source': "AMZ FC",
            'Size': fields.get("Size", ""),
            'Photo Taker': fields.get("Clerk", ""),
            'Packaging': "",
            'Other Notes': fields.get("Notes", ""),
            'MSRP': fields.get("MSRP", "0.00"),
            'Location': fields.get("Location", ""),
            'Item Condition': fields.get("Condition", ""),
            'ID': record_id,
            'Amazon ID': fields.get("B00 ASIN", ""),
            'AuctionCount': fields.get("Auction Count", ""),
            'HibidSearchText': fields.get("Description", ""),
            'FullTitle': fields.get("Product Name", "")
        }

        # UPC handling
        upc = str(fields.get("UPC", ""))
        new_record["UPC"] = "" if upc.lower() == 'nan' or not upc.isdigit() else upc

        # Subtitle
        new_record["Subtitle"] = format_subtitle(
            int(new_record["AuctionCount"]),
            float(new_record["MSRP"]),
            new_record["Other Notes"]
        )

        # Description
        description_parts = [
            format_html_field("Description", new_record['FullTitle']),
            format_html_field("MSRP", new_record['MSRP']),
            format_html_field("Condition", new_record['Item Condition']),
            format_html_field("Notes", new_record['Other Notes']),
            format_html_field("Other info", new_record['HibidSearchText']),
            format_html_field("Lot Number", new_record['Lot Number'])
        ]
        new_record["Description"] = ''.join(part for part in description_parts if part)
        new_record["Description"] += "<br><b>Pickup Information:</b> This item is available for LOCAL PICKUP ONLY. No shipping available."

        # Handle image URLs
        for i in range(1, 11):
            new_record[f"Image_{i}"] = ''

        if record_id in uploaded_image_urls:
            gui_callback(f"Found uploaded images for record ID: {record_id}")
            sorted_images = sorted(uploaded_image_urls[record_id], key=lambda x: x[1])
            for url, image_number in sorted_images:
                if 1 <= image_number <= 10:
                    new_record[f'Image_{image_number}'] = url
                    gui_callback(f"Assigned Image_{image_number}: {url}")
            
            # Check if Image_1 is present
            if not new_record['Image_1']:
                gui_callback(f"Warning: Image_1 is missing for record ID: {record_id}")
                # You might want to add additional error handling or reporting here
        else:
            gui_callback(f"No uploaded images found for record ID: {record_id}")

        gui_callback(f"Final new_record: {new_record}")
        new_record['Success'] = True
        return new_record

    except Exception as e:
        lot_number = fields.get('Lot Number', 'Unknown')
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
        self.should_stop = should_stop if should_stop is not None else asyncio.Event()
        self.callback = callback
        self.selected_warehouse = selected_warehouse
        self.starting_price = starting_price
        self.task_id = task_id
        self.total_steps = 6
        self.current_step = 0

        # Standard-2X dyno optimized configuration (2x-8x compute)
        self.MAX_CONCURRENT_TASKS = 16        # Increased for 2x CPU share
        self.MAX_CONCURRENT_IMAGES = 32       # Doubled for parallel processing
        self.BATCH_SIZE = 50                  # Smaller batches for better memory management
        self.IMAGE_CHUNK_SIZE = 10           # Smaller chunks for better throughput
        
        # Memory management - Standard-2X has 1GB RAM
        self.memory_limit = 512 * 1024 * 1024  # 512MB target (safe margin for other processes)
        
        # Website URLs and notification email
        self.website_login_url = config_manager.get_global_var('website_login_url')
        self.import_csv_url = config_manager.get_global_var('import_csv_url')
        self.notification_email = config_manager.get_global_var('notification_email')
        
        config_manager.set_active_warehouse(selected_warehouse)
        
        self.semaphores = None
        self.rate_limiter = None

    async def setup_resources(self):
        """Initialize resources with optimized concurrency settings"""
        self.semaphores = {
            'main': asyncio.Semaphore(self.MAX_CONCURRENT_TASKS),
            'image': asyncio.Semaphore(self.MAX_CONCURRENT_IMAGES)
        }
        self.rate_limiter = RateLimiter(rate_limit=32, time_period=1)  # Increased for 2x CPU

    def update_progress(self, message, sub_progress=None):
        self.current_step += 1
        progress = (self.current_step / self.total_steps) * 100
        if sub_progress:
            progress = ((self.current_step - 1) / self.total_steps * 100) + (sub_progress / self.total_steps)
        self.gui_callback(message, progress)
        RedisTaskStatus.set_status(self.task_id, "IN_PROGRESS", message, progress)

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
                temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.csv', encoding='utf-8')
                self.gui_callback(f"CSV content length before writing to file: {len(csv_content)}")
                temp_file.write(csv_content)
                temp_file_path = temp_file.name
                temp_file.close()
                with open(temp_file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                self.gui_callback(f"CSV file content length after writing: {len(file_content)}")

                self.gui_callback("Selecting CSV file...")
                try:
                    await page.set_input_files("#file", temp_file_path)
                except Exception as e:
                    self.gui_callback(f"Error: Failed to select CSV file. {str(e)}")
                    await self.save_screenshot(page, 'file_selection_error')
                    return False
                
                self.gui_callback(f"CSV file selected for upload: {temp_file_path}")

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
            self.update_progress("Starting auction formatting")

            airtable_records = await self.fetch_airtable_records()
            if not airtable_records:
                self.update_progress("Failed to fetch Airtable records")
                return

            processed_records, failed_records = await self.process_records_and_images(airtable_records)
            self.update_progress("Records and images processed")

            cleaned_csv_content = await self.generate_and_clean_csv(processed_records)
            if not cleaned_csv_content:
                self.update_progress("Failed to generate CSV content")
                return

            validation_result = self.validate_csv_content(cleaned_csv_content)
            if not validation_result['valid']:
                self.update_progress(f"CSV validation failed: {validation_result['message']}")
                return

            await self.save_formatted_data(cleaned_csv_content)
            self.update_progress("Formatted data saved to database")

            upload_success = await self.upload_csv_to_website_playwright(cleaned_csv_content)
            if upload_success:
                final_message = "Auction formatting process completed successfully"
                self.update_progress(final_message)
                return final_message
            else:
                error_message = "Failed to upload CSV to website"
                self.update_progress(error_message)
                return error_message

        except Exception as e:
            error_message = f"Error in auction formatting process: {str(e)}"
            self.update_progress(error_message)
            raise

        finally:
            await self.cleanup_resources()
            await sync_to_async(self.callback)()

    def validate_csv_content(self, csv_content):
        expected_columns = [
            'EventID', 'LotNumber', 'Seller', 'ConsignorNumber', 'Category', 'Region',
            'ListingType', 'Currency', 'Title', 'Subtitle', 'Description', 'Price',
            'Quantity', 'IsTaxable', 'Image_1', 'Image_2', 'Image_3', 'Image_4',
            'Image_5', 'Image_6', 'Image_7', 'Image_8', 'Image_9', 'Image_10',
            'YouTubeID', 'PdfAttachments', 'Bold', 'Badge', 'Highlight', 'ShippingOptions',
            'Duration', 'StartDTTM', 'EndDTTM', 'AutoRelist', 'GoodTilCanceled',
            'Working Condition', 'UPC', 'Truck', 'Source', 'Size', 'Photo Taker',
            'Packaging', 'Other Notes', 'MSRP', 'Lot Number', 'Location', 'Item Condition',
            'ID', 'Amazon ID'
        ]
        
        df = pd.read_csv(StringIO(csv_content))
        csv_columns = df.columns.tolist()
        
        if csv_columns != expected_columns:
            missing_columns = set(expected_columns) - set(csv_columns)
            extra_columns = set(csv_columns) - set(expected_columns)
            message = f"CSV columns do not match expected columns. Missing: {missing_columns}, Extra: {extra_columns}"
            return {'valid': False, 'message': message}
        
        return {'valid': True, 'message': "CSV content is valid"}

    async def fetch_airtable_records(self):
        RedisTaskStatus.set_status(self.task_id, "IN_PROGRESS", "Fetching Airtable records")
        try:
            airtable_records = await get_cached_airtable_records(
                config_manager.get_warehouse_var('airtable_inventory_base_id'),
                config_manager.get_warehouse_var('airtable_inventory_table_id'),
                config_manager.get_warehouse_var('airtable_send_to_auction_view_id'),
                self.gui_callback,
                config_manager.get_warehouse_var('airtable_api_key')
            )
            RedisTaskStatus.set_status(self.task_id, "IN_PROGRESS", f"Retrieved {len(airtable_records)} records from Airtable")
            return airtable_records
        except Exception as e:
            RedisTaskStatus.set_status(self.task_id, "ERROR", f"Failed to fetch Airtable records: {str(e)}")
            self.gui_callback(f"Error fetching Airtable records: {str(e)}")
            return None
    
    def prepare_record_images(self, record):
        """Prepare and sort record images"""
        record_images = []
        for j in range(1, 11):
            image_info = record["fields"].get(f"Image {j}", [])
            if image_info:
                record_images.append((j, image_info[0].get("url")))
        # Sort to ensure image_1 is processed first
        record_images.sort(key=lambda x: x[0])
        return record_images

    async def process_records_and_images(self, airtable_records):
        """Optimized record and image processing with increased parallelism"""
        if not self.semaphores:
            await self.setup_resources()

        self.gui_callback(f"Starting to process {len(airtable_records)} records")
        processed_records = []
        failed_records = []
        total_records = len(airtable_records)

        # Process in chunks for better memory management
        chunk_size = min(self.BATCH_SIZE, 50)  # Limit chunk size
        for i in range(0, total_records, chunk_size):
            if self.should_stop.is_set():
                break

            chunk = airtable_records[i:i+chunk_size]
            self.gui_callback(f"Processing chunk {i//chunk_size + 1} of {(total_records + chunk_size - 1)//chunk_size}")

            # Process images with higher concurrency but controlled batch size
            image_tasks = []
            for record in chunk:
                record_images = self.prepare_record_images(record)
                if record_images:
                    record_image_tasks = [
                        self.process_single_image_with_semaphore(
                            self.semaphores['image'],
                            record['id'],
                            url,
                            j
                        )
                        for j, url in record_images
                    ]
                    image_tasks.extend(record_image_tasks)

            # Process images in smaller batches
            image_results = {}
            batch_size = min(self.IMAGE_CHUNK_SIZE, 10)
            for batch_start in range(0, len(image_tasks), batch_size):
                batch_end = batch_start + batch_size
                batch = image_tasks[batch_start:batch_end]
                
                results = await asyncio.gather(*batch, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        self.gui_callback(f"Error in image processing: {str(result)}")
                        continue
                    if result:
                        record_id, url, image_number = result
                        if record_id not in image_results:
                            image_results[record_id] = []
                        image_results[record_id].append((url, image_number))

            # Process records with controlled concurrency
            record_tasks = [
                self.process_single_record_with_semaphore(record, image_results.get(record['id'], []))
                for record in chunk
            ]

            batch_results = await asyncio.gather(*record_tasks, return_exceptions=True)
            
            for result in batch_results:
                if isinstance(result, Exception):
                    self.gui_callback(f"Error processing record: {str(result)}")
                    continue
                if result.get('Success', False):
                    processed_records.append(result)
                else:
                    failed_records.append(result)

            progress = min((i + chunk_size) / total_records * 100, 100)
            self.gui_callback(f"Processed {progress:.1f}% of records")

            # Memory management
            if self.check_memory_usage() > 0.8:  # If memory usage is above 80%
                gc.collect()  # Force garbage collection
                await asyncio.sleep(1)  # Give system time to reclaim memory

        return processed_records, failed_records

    async def process_single_record_async(self, record, image_results):
        """Async version of process_single_record as a method of AuctionFormatter"""
        try:
            record_id = record.get('id', '')
            self.gui_callback(f"Processing record ID: {record_id}")

            fields = record.get('fields', {})

            new_record = {
                'EventID': self.auction_id,
                'LotNumber': ('M' if self.selected_warehouse == "Maule Warehouse" else 'S' if self.selected_warehouse == "Sahara Warehouse" else '') + str(fields.get("Lot Number", "")),
                'Lot Number': str(fields.get("Lot Number", "")),
                'Seller': "702Auctions",
                'ConsignorNumber': "",
                'Category_not_formatted': fields.get("Category", ""),
                'Category': category_converter(fields.get("Category", "")),
                'Region': "88850842" if self.selected_warehouse == "Maule Warehouse" else "88850843" if self.selected_warehouse == "Sahara Warehouse" else "",
                'ListingType': "Auction",
                'Currency': "USD",
                'Title': text_shortener(fields.get("Product Name", ""), 80),
                'Subtitle': "",  # Will be set later
                'Description': "",  # Will be set later
                'Price': self.starting_price,
                'Quantity': "1",
                'IsTaxable': "TRUE",
                'YouTubeID': "",
                'PdfAttachments': "",
                'Bold': "false",
                'Badge': "",
                'Highlight': "false",
                'ShippingOptions': "",
                'Duration': "",
                'StartDTTM': "",
                'EndDTTM': "",
                'AutoRelist': "0",
                'GoodTilCanceled': "false",
                'Working Condition': fields.get("Working Condition", ""),
                'UPC': "",  # Will be set later
                'Truck': fields.get("Shipment", ""),
                'Source': "AMZ FC",
                'Size': fields.get("Size", ""),
                'Photo Taker': fields.get("Clerk", ""),
                'Packaging': "",
                'Other Notes': fields.get("Notes", ""),
                'MSRP': fields.get("MSRP", "0.00"),
                'Location': fields.get("Location", ""),
                'Item Condition': fields.get("Condition", ""),
                'ID': record_id,
                'Amazon ID': fields.get("B00 ASIN", ""),
                'AuctionCount': fields.get("Auction Count", ""),
                'HibidSearchText': fields.get("Description", ""),
                'FullTitle': fields.get("Product Name", "")
            }

            # UPC handling
            upc = str(fields.get("UPC", ""))
            new_record["UPC"] = "" if upc.lower() == 'nan' or not upc.isdigit() else upc

            # Subtitle
            new_record["Subtitle"] = format_subtitle(
                int(new_record["AuctionCount"]) if new_record["AuctionCount"] else 1,
                float(new_record["MSRP"]) if new_record["MSRP"] else 0.00,
                new_record["Other Notes"]
            )

            # Description
            description_parts = [
                format_html_field("Description", new_record['FullTitle']),
                format_html_field("MSRP", new_record['MSRP']),
                format_html_field("Condition", new_record['Item Condition']),
                format_html_field("Notes", new_record['Other Notes']),
                format_html_field("Other info", new_record['HibidSearchText']),
                format_html_field("Lot Number", new_record['Lot Number'])
            ]
            new_record["Description"] = ''.join(part for part in description_parts if part)
            new_record["Description"] += "<br><b>Pickup Information:</b> This item is available for LOCAL PICKUP ONLY. No shipping available."

            # Handle image URLs
            for i in range(1, 11):
                new_record[f"Image_{i}"] = ''

            if image_results:  # Using passed image results instead of checking uploaded_image_urls
                self.gui_callback(f"Found uploaded images for record ID: {record_id}")
                sorted_images = sorted(image_results, key=lambda x: x[1])
                for url, image_number in sorted_images:
                    if 1 <= image_number <= 10:
                        new_record[f'Image_{image_number}'] = url
                        self.gui_callback(f"Assigned Image_{image_number}: {url}")
                
                if not new_record['Image_1']:
                    self.gui_callback(f"Warning: Image_1 is missing for record ID: {record_id}")
            else:
                self.gui_callback(f"No uploaded images found for record ID: {record_id}")

            new_record['Success'] = True
            return new_record

        except Exception as e:
            lot_number = fields.get('Lot Number', 'Unknown')
            error_message = f"Error processing Lot Number {lot_number}: {str(e)}"
            self.gui_callback(f"Error: {error_message}")
            self.gui_callback(f"Traceback: {traceback.format_exc()}")
            return {'Lot Number': lot_number, 'Failure Message': error_message, 'Success': False}

    def check_memory_usage(self) -> float:
        """Check current memory usage ratio"""
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Convert to bytes
        return usage / self.memory_limit

    async def cleanup_resources(self):
        """Cleanup resources properly"""
        try:
            # Clear semaphores
            self.semaphores = None
            
            # Force garbage collection
            gc.collect()
            
        except Exception as e:
            self.gui_callback(f"Error during cleanup: {str(e)}")

    async def generate_and_clean_csv(self, processed_records):
        RedisTaskStatus.set_status(self.task_id, "IN_PROGRESS", "Generating CSV content")
        try:
            if not processed_records:
                raise ValueError("No processed records to generate CSV.")
            
            # Convert to DataFrame for sorting and duplicate handling
            df = pd.DataFrame(processed_records)
            self.gui_callback(f"Initial data loaded with {len(df)} records")
            
            # Convert MSRP to numeric for sorting
            df['MSRP'] = pd.to_numeric(df['MSRP'], errors='coerce').round(2)
            
            # Filter out damaged/missing items
            condition_mask = ~df['Subtitle'].str.contains('missing|damaged|no', 
                                                        case=False, 
                                                        na=False)
            
            # Process top items
            potential_top = df[condition_mask].sort_values('MSRP', ascending=False)
            
            # Handle duplicates in top 50
            top_50_indices = []
            seen_titles = set()
            duplicate_indices = []
            
            for idx, row in potential_top.iterrows():
                title_key = row['Title'].lower().strip()
                if len(top_50_indices) < 50:
                    if title_key not in seen_titles:
                        top_50_indices.append(idx)
                        seen_titles.add(title_key)
                    else:
                        duplicate_indices.append(idx)
            
            # Split into groups
            top_50_records = df.loc[top_50_indices].to_dict('records')
            remaining_mask = ~df.index.isin(top_50_indices + duplicate_indices)
            remaining_records = df[remaining_mask].to_dict('records')
            duplicate_records = df.loc[duplicate_indices].to_dict('records')
            
            # Combine and shuffle remaining records
            other_records = duplicate_records + remaining_records
            random.shuffle(other_records)
            
            # Combine all records in final order
            sorted_records = top_50_records + other_records
            
            self.gui_callback(f"Records sorted with {len(top_50_records)} premium items")
            
            # Use existing generate_csv_content with sorted records
            csv_content = self.generate_csv_content(sorted_records)
            if not csv_content.strip():
                raise ValueError("Generated CSV content is empty.")
            
            # Clean using existing method
            cleaned_csv_content = self.clean_csv_content(csv_content)
            if not cleaned_csv_content.strip():
                raise ValueError("Cleaned CSV content is empty.")
            
            self.final_csv_content = cleaned_csv_content
            self.gui_callback(f"CSV content generated successfully")
            
            await self.save_formatted_data(cleaned_csv_content)
            return cleaned_csv_content

        except Exception as e:
            error_message = f"Error generating CSV: {str(e)}"
            RedisTaskStatus.set_status(self.task_id, "ERROR", error_message)
            self.gui_callback(error_message)
            logger.error(f"CSV generation error: {error_message}")
            logger.error(traceback.format_exc())
            raise
        
    async def process_single_image_with_semaphore(self, semaphore, record_id, url, image_number):
        """Optimized image processing with reduced delays"""
        async with semaphore:
            for attempt in range(3):
                try:
                    self.gui_callback(f"Processing image {image_number} for record {record_id} (Attempt {attempt + 1})")
                    
                    # Reduced timeout for faster failure detection
                    try:
                        image_data = await asyncio.wait_for(
                            download_image_async(url, self.gui_callback),
                            timeout=20  # Reduced from 30
                        )
                        if not image_data:
                            await asyncio.sleep(1)  # Reduced delay
                            continue
                    except asyncio.TimeoutError: 
                        await asyncio.sleep(1)
                        continue

                    try:
                        processed_data = await asyncio.wait_for(
                            process_image_async(image_data, self.gui_callback),
                            timeout=20
                        )
                        if not processed_data:
                            await asyncio.sleep(1)
                            continue
                    except asyncio.TimeoutError:
                        await asyncio.sleep(1)
                        continue

                    try:
                        file_name = f"{record_id}_{image_number}.jpg"
                        uploaded_url = await asyncio.wait_for(
                            upload_file_via_ftp_async(
                                file_name,
                                processed_data,
                                self.gui_callback,
                                self.should_stop
                            ),
                            timeout=20
                        )
                        
                        if uploaded_url:
                            return (record_id, uploaded_url, image_number)
                    except asyncio.TimeoutError:
                        await asyncio.sleep(1)
                    
                    await asyncio.sleep(1)

                except Exception as e:
                    self.gui_callback(f"Error processing image {image_number} for record {record_id}: {str(e)}")
                    await asyncio.sleep(1)

            return None

    async def process_single_record_with_semaphore(self, record, image_results):
        """Process single record with semaphore control"""
        try:
            async with self.semaphores['main']:
                return await self.process_single_record_async(record, image_results)
        except Exception as e:
            self.gui_callback(f"Error processing record {record.get('id', 'unknown')}: {str(e)}")
            return {'id': record.get('id', 'unknown'), 'error': str(e), 'Success': False}

    async def download_and_process_image(self, record_id, url, image_number, image_data_dict):
        image_data = await download_image_async(url, self.gui_callback)
        if image_data:
            processed_image_data = await process_image_async(image_data, self.gui_callback)
            # Free up memory by deleting the original image data
            del image_data
            if processed_image_data:
                image_data_dict[record_id].append((processed_image_data, image_number))

    async def upload_image_and_get_url(self, record_id, image_data, image_number, uploaded_image_urls):
        file_name = f"{record_id}_{image_number}.jpg"
        url = await upload_file_via_ftp_async(file_name, image_data, self.gui_callback)
        # Free up memory by deleting the processed image data
        del image_data
        if url:
            if not url.startswith("https://"):
                url = "https://" + url
            uploaded_image_urls[record_id].append((url, image_number))

    async def save_images_to_database(self, uploaded_image_urls):
        image_metadata = []
        for record_id, urls in uploaded_image_urls.items():
            for url, image_number in urls:
                image_metadata.append(ImageMetadata(
                    event=self.event,
                    filename=f"{record_id}_{image_number}.jpg",
                    is_primary=(image_number == 1),
                    image=url
                ))
        await sync_to_async(ImageMetadata.objects.bulk_create)(image_metadata)

    async def process_record(self, record, uploaded_image_urls, processed_records, failed_records):
        try:
            result = process_single_record(
                record, uploaded_image_urls, self.auction_id, self.selected_warehouse, self.starting_price, self.gui_callback
            )
            if result.get('Success', False):
                processed_records.append(result['Data'])
            else:
                failed_records.append(result)
        except Exception as e:
            self.gui_callback(f"Error processing record {record['id']}: {str(e)}")
            failed_records.append({'RecordID': record['id'], 'Error': str(e)})

    def generate_csv_content(self, processed_records):
        expected_columns = [
            'EventID', 'LotNumber', 'Seller', 'ConsignorNumber', 'Category', 'Region',
            'ListingType', 'Currency', 'Title', 'Subtitle', 'Description', 'Price',
            'Quantity', 'IsTaxable', 'Image_1', 'Image_2', 'Image_3', 'Image_4',
            'Image_5', 'Image_6', 'Image_7', 'Image_8', 'Image_9', 'Image_10',
            'YouTubeID', 'PdfAttachments', 'Bold', 'Badge', 'Highlight', 'ShippingOptions',
            'Duration', 'StartDTTM', 'EndDTTM', 'AutoRelist', 'GoodTilCanceled',
            'Working Condition', 'UPC', 'Truck', 'Source', 'Size', 'Photo Taker',
            'Packaging', 'Other Notes', 'MSRP', 'Lot Number', 'Location', 'Item Condition',
            'ID', 'Amazon ID'
        ]
        
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=expected_columns)
        writer.writeheader()
        
        default_values = {
            'EventID': self.auction_id,
            'Seller': '702Auctions',
            'ListingType': 'Auction',
            'Currency': 'USD',
            'Price': self.starting_price,
            'Quantity': '1',
            'IsTaxable': 'true',
            'Bold': 'false',
            'Highlight': 'false',
            'ShippingOptions': '',
            'AutoRelist': '0',
            'GoodTilCanceled': 'false'
        }
        
        for record in processed_records:
            new_record = {column: str(record.get(column, '')) for column in expected_columns}
            new_record.update(default_values)
            
            # Convert boolean fields to lowercase strings
            for field in ['Bold', 'Highlight', 'GoodTilCanceled']:
                new_record[field] = str(new_record[field]).lower()
            
            writer.writerow(new_record)
        
        return output.getvalue()
    
    def clean_csv_content(self, csv_content):
        df = pd.read_csv(StringIO(csv_content))
        
        # Ensure 'Price' is a valid decimal
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce').fillna(0).round(2).astype(str)
        
        # Ensure 'Quantity' is an integer
        df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce').fillna(1).astype(int).astype(str)
        
        # Ensure 'Category' is an integer
        df['Category'] = pd.to_numeric(df['Category'], errors='coerce').fillna(162733).astype(int).astype(str)
        
        # Use a more efficient method to write CSV
        buffer = StringIO()
        df.to_csv(buffer, index=False)
        return buffer.getvalue()

    async def save_formatted_data(self, csv_content):
        await sync_to_async(AuctionFormattedData.objects.create)(
            event=self.event,
            csv_data=csv_content
        )

    async def upload_csv_to_website_playwright(self, csv_content):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                username, password = self.get_maule_login_credentials()
                login_success = await self.login_to_website(page, username, password)

                if not login_success:
                    self.gui_callback("Login to auction site failed")
                    return False

                upload_success = await self.upload_csv_to_website(page, csv_content)
                if upload_success:
                    self.gui_callback("CSV uploaded successfully")
                    return True
                else:
                    self.gui_callback("CSV upload failed")
                    return False

            except Exception as e:
                self.gui_callback(f"Error during CSV upload process: {str(e)}")
                return False
            finally:
                await browser.close()

@shared_task(bind=True)
def auction_formatter_task(self, auction_id, selected_warehouse, starting_price):
    config_manager.set_active_warehouse(selected_warehouse)
    
    try:
        with transaction.atomic():
            event = Event.objects.get(event_id=auction_id)
        
        def progress_callback(message, percentage=None):
            state = 'PROGRESS'
            meta = {'status': message}
            if percentage is not None:
                meta['progress'] = percentage
            self.update_state(state=state, meta=meta)
            logger.info(f"Progress: {message} - {percentage}%")
        
        formatter = AuctionFormatter(
            event=event,
            gui_callback=progress_callback,
            should_stop=asyncio.Event(),
            callback=lambda: None,
            selected_warehouse=selected_warehouse,
            starting_price=starting_price,
            task_id=self.request.id
        )
        
        asyncio.run(formatter.run_auction_formatter())
        
        final_message = "Auction formatting completed successfully"
        RedisTaskStatus.set_status(self.request.id, "COMPLETED", final_message, 100)
        logger.info(final_message)
        return final_message

    except Event.DoesNotExist:
        error_message = f"Event with ID {auction_id} does not exist"
        RedisTaskStatus.set_status(self.request.id, "FAILURE", error_message, 100)
        logger.error(error_message)
        raise

    except Exception as e:
        error_message = f"Error in auction formatting process: {str(e)}"
        RedisTaskStatus.set_status(self.request.id, "FAILURE", error_message, 100)
        logger.error(f"{error_message}\n{traceback.format_exc()}")
        raise self.retry(exc=e, max_retries=3)