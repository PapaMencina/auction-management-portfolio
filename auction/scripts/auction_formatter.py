import os
import threading
import time
import re
import ftplib
import json
import shutil
from typing import List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from PIL import Image, ExifTags
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.firefox import GeckoDriverManager

from auction.utils import config_manager

# Load configuration
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, '..', 'utils', 'config.json')
config_manager.load_config(config_path)

def auction_formatter_main(auction_id, selected_warehouse, gui_callback, should_stop, callback, show_browser):
    config_manager.set_active_warehouse(selected_warehouse)
    formatter = AuctionFormatter(auction_id, gui_callback, should_stop, callback, selected_warehouse, show_browser)
    formatter.run_auction_formatter()
    return formatter

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
        elif content_type.startswith('application/octet-stream'):
            file_extension = get_extension_from_content_disposition(response.headers.get('Content-Disposition', ''))
            if file_extension.lower() not in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
                gui_callback(f"The URL for {file_name} does not point to a valid image file extension: {file_extension}")
                return None
        else:
            gui_callback(f"The URL for {file_name} does not point to a valid image: {content_type}")
            return None

        if len(response.content) < 1000:
            gui_callback(f"Image for {file_name} is too small, might be corrupted")
            return None

        download_path = get_resources_dir('product_images')
        complete_file_name = os.path.join(download_path, f"{file_name}.{file_extension}")

        with open(complete_file_name, 'wb') as f:
            f.write(response.content)

        return complete_file_name

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

def process_images_in_bulk(downloaded_images_bulk: Dict[str, List[str]], gui_callback, should_stop: threading.Event) -> Dict[str, List[str]]:
    gui_callback("Processing Images...")
    all_image_paths = []
    record_id_map = {}

    for record_id, image_paths in downloaded_images_bulk.items():
        for image_path in image_paths:
            all_image_paths.append(image_path)
            record_id_map[image_path] = record_id

    processed_images = {}
    with ThreadPoolExecutor() as executor:
        future_to_image = {executor.submit(process_image_wrapper, img_path, gui_callback, should_stop): img_path for img_path in all_image_paths}

        for future in as_completed(future_to_image):
            if should_stop.is_set():
                break

            img_path = future_to_image[future]
            try:
                result = future.result()
                record_id = record_id_map[img_path]
                processed_images.setdefault(record_id, []).append(result)
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

def collect_image_urls(airtable_records: List[Dict], should_stop: threading.Event) -> List[tuple]:
    download_tasks = []
    for record in airtable_records:
        if should_stop.is_set():
            break

        product_id = str(record["fields"].get("Lot Number", ""))
        record_id = record['id']
        image_counter = 1
        for count in range(1, 11):
            image_url = get_image_url(record, count)
            if image_url:
                file_name = f"{product_id}_{image_counter}"
                download_tasks.append((record_id, image_url, file_name))
                image_counter += 1
    return download_tasks

def download_images_bulk(download_tasks: List[tuple], gui_callback, should_stop: threading.Event) -> Dict[str, List[str]]:
    gui_callback("Downloading Images...")
    image_paths = {}

    with ThreadPoolExecutor(max_workers=7) as executor:
        future_to_task = {executor.submit(download_image, url, file_name, gui_callback): (record_id, file_name) for record_id, url, file_name in download_tasks}

        for future in as_completed(future_to_task):
            if should_stop.is_set():
                break

            record_id, file_name = future_to_task[future]
            try:
                downloaded_path = future.result()
                if downloaded_path:
                    image_paths.setdefault(record_id, []).append(downloaded_path)
            except Exception as e:
                gui_callback(f"Error downloading image for {file_name}: {e}")

    return image_paths

def upload_images_and_get_urls(downloaded_images: Dict[str, List[str]], gui_callback, should_stop: threading.Event) -> Dict[str, List[str]]:
    gui_callback("Uploading Images...")
    uploaded_image_urls = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_image = {executor.submit(upload_image, image_path, gui_callback, should_stop): (record_id, image_path) for record_id, image_paths in downloaded_images.items() for image_path in image_paths}

        for future in as_completed(future_to_image):
            if should_stop.is_set():
                return uploaded_image_urls

            record_id, image_path = future_to_image[future]
            try:
                url = future.result()
                if url:
                    if not url.startswith("https://"):
                        url = "https://" + url
                    uploaded_image_urls.setdefault(record_id, []).append(url)
            except Exception as e:
                gui_callback(f"Error uploading image {image_path}: {e}")

    return uploaded_image_urls

def format_field(label: str, value: str) -> str:
    return f"{label}: {value}" if value is not None and str(value).strip() else ""

def get_image_url(airtable_record: Dict, count: int) -> str:
    return airtable_record.get("fields", {}).get(f"Image {count}", [{}])[0].get("url", "")

def upload_file_via_ftp(file_name: str, local_file_path: str, gui_callback, should_stop: threading.Event, max_retries: int = 3, remote_file_path: str = "/airtableimages.702auctions.com/public_html/", server: str = "702auctions.com", username: str = "702auctionsftp@702auctions.com", password: str = "Ronch420$") -> str:
    retries = 0
    while retries < max_retries and not should_stop.is_set():
        try:
            with ftplib.FTP(server, username, password) as ftp:
                ftp.set_pasv(True)
                ftp.cwd('/')
                ftp.sendcmd('TYPE I')
                remote_path_full = os.path.join(remote_file_path, file_name)

                with open(local_file_path, 'rb') as file:
                    ftp.storbinary(f'STOR {remote_path_full}', file)

            formatted_url = remote_path_full.replace("/public_html", "", 1).lstrip('/')
            return f"https://{formatted_url}"

        except ftplib.error_temp as e:
            print(f"Temporary FTP error: {e}. Retrying in 5 seconds...")
            retries += 1
            time.sleep(5)
        except Exception as e:
            print(f"FTP upload error: {e}.")
            break

    print("Failed to upload after maximum retries.")
    return None

def format_html_field(field_name: str, value: str) -> str:
    return f"<b>{field_name}</b>: {value}<br>" if value else ""

def process_single_record(airtable_record: Dict, uploaded_image_urls: Dict[str, List[str]], Auction_ID: str, selected_warehouse: str) -> Dict:
    try:
        newRecord = {}
        newRecord["AuctionCount"] = airtable_record["fields"].get("Auction Count", "")
        newRecord["Photo Taker"] = airtable_record["fields"].get("Clerk", "")
        newRecord["Size"] = airtable_record["fields"].get("Size", "")
        newRecord["UPC"] = str(airtable_record["fields"].get("UPC", ""))
        newRecord["ID"] = airtable_record.get("id", "")
        product_id = str(airtable_record["fields"].get("Lot Number", ""))
        newRecord["LotNumber"] = newRecord["Lot Number"] = str(product_id)
        newRecord["Other Notes"] = airtable_record["fields"].get("Notes", "")
        newRecord["MSRP"] = airtable_record["fields"].get("MSRP", "0.00")
        newRecord["Truck"] = airtable_record["fields"].get("Shipment", "")
        newRecord["Category_not_formatted"] = airtable_record["fields"].get("Category", "")
        newRecord["Amazon ID"] = airtable_record["fields"].get("B00 ASIN", "")
        newRecord["Item Condition"] = airtable_record["fields"].get("Condition")
        newRecord["HibidSearchText"] = airtable_record["fields"].get("Description", "")
        newRecord["FullTitle"] = airtable_record["fields"].get("Product Name", "")
        newRecord["Location"] = airtable_record["fields"].get("Location")

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

        hibid_message = f"This item is live on our site, 702 Auctions.com. To view additional images and bid on this item, CLICK THE LINK ABOVE or visit bid.702auctions.com and search for lot number {newRecord['LotNumber']}."
        newRecord["HiBid"] = " -- ".join([hibid_message] + [field for field in base_fields if field])
        newRecord["Description"] = ''.join(field for field in html_base_fields if field)
        newRecord["Currency"] = "USD"
        newRecord["ListingType"] = "Auction"
        newRecord["Seller"] = "702Auctions"
        newRecord["EventID"] = Auction_ID
        
        newRecord["Region"] = "88850842" if selected_warehouse == "Maule Warehouse" else "88850843" if selected_warehouse == "Sunrise Warehouse" else ""

        newRecord["Source"] = "AMZ FC"
        newRecord["IsTaxable"] = "TRUE"
        newRecord["Quantity"] = "1"
        
        title = airtable_record["fields"]["Product Name"]
        if selected_warehouse == "Sunrise Warehouse":
            title = "OFFSITE " + title
        newRecord["Title"] = text_shortener(title, 80)
        
        newRecord["Category"] = category_converter(newRecord.get("Category_not_formatted", ""))

        auction_count = int(newRecord.get("AuctionCount", 0))
        newRecord["Price"] = "5.00" if auction_count == 1 else "2.50" if auction_count == 2 else "1.00" if auction_count >= 3 else "5.00"

        newRecord["Subtitle"] = format_subtitle(
            int(newRecord.get("AuctionCount", 0)),
            float(newRecord.get("MSRP", 0)),
            newRecord.get("Other Notes", "")
        )

        record_id = airtable_record['id']
        if record_id in uploaded_image_urls:
            for i, url in enumerate(uploaded_image_urls[record_id], 1):
                newRecord[f'Image_{i}'] = url

        newRecord['Success'] = True
        return newRecord
    except Exception as e:
        lot_number = airtable_record.get('fields', {}).get('Lot Number', 'Unknown')
        error_message = f"Error processing Lot Number {lot_number}: {e}"
        return {'Lot Number': lot_number, 'Failure Message': error_message, 'Success': False}

def process_records_concurrently(airtable_records: List[Dict], uploaded_image_urls: Dict[str, List[str]], gui_callback, auction_id: str, selected_warehouse: str, should_stop: threading.Event) -> Tuple[List[Dict], List[Dict]]:
    gui_callback("Creating CSV...")
    processed_records = []
    failed_records = []

    with ThreadPoolExecutor() as executor:
        future_to_record = {executor.submit(process_single_record, record, uploaded_image_urls, auction_id, selected_warehouse): record for record in airtable_records}

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
    download_path = os.path.join(get_resources_dir('failed_csv'), f'{Auction_ID}-FAILED.csv')
    failed_dataframe.to_csv(download_path, index=False)
    gui_callback(f'Failed records have been saved to {download_path}.')
    return download_path

def processed_records_to_df(processed_records: List[Dict], Auction_ID: str, gui_callback) -> str:
    df = pd.DataFrame(processed_records)
    column_order = ["EventID", "LotNumber", "Seller", "Category_not_formatted", "Category", "Region", "ListingType", "Currency",
                    "Title", "Subtitle", "Description", "Price", "Quantity", "IsTaxable", "Image_1", "Image_2", "Image_3", "Image_4",
                    "Image_5", "Image_6", "Image_7", "Image_8", "Image_9", "Image_10", "YouTubeID", "PdfAttachments", "Bold", "Badge",
                    "Highlight", "ShippingOptions", "Duration", "StartDTTM", "EndDTTM", "AutoRelist", "GoodTilCanceled", "Working Condition",
                    "UPC", "Truck", "Source", "Size", "Photo Taker", "Packaging", "Other Notes", "MSRP", "Lot Number", "Location",
                    "Item Condition", "ID", "Amazon ID", "HiBid", "AuctionCount", "number"]
    df = df.reindex(columns=column_order, fill_value='')
    
    resources_dir = os.path.join(script_dir, '..', 'resources', 'processed_csv')
    os.makedirs(resources_dir, exist_ok=True)
    
    download_path = os.path.join(resources_dir, f'unformatted_{Auction_ID}.csv')
    df.to_csv(download_path, index=False)
    gui_callback(f'Successful records have been saved to {download_path}.')

    return download_path

def get_resources_dir(folder: str) -> str:
    return os.path.join('C:\\Users\\matt9\\Desktop\\Auction_script_current\\resources', folder)

def organize_images(Auction_ID: str) -> None:
    file_count = 0
    directory = get_resources_dir('product_images')
    subfolder = os.path.join(get_resources_dir('hibid_images'), f'hibid_{Auction_ID}')

    if os.path.isdir(subfolder):
        shutil.rmtree(subfolder)
    os.mkdir(subfolder)

    for file in os.listdir(directory):
        if file.endswith(("_1.jpeg", "_1.png", '_1.jpg')):
            shutil.move(os.path.join(directory, file), subfolder)
            file_count += 1
        elif file.endswith(('.jpg', "png", ".jpeg", ".webp")):
            os.remove(os.path.join(directory, file))

def check_continuation(func):
    def wrapper(*args, **kwargs):
        self = args[0]
        if not self.should_continue(self.should_stop, self.gui_callback, f"Operation stopped before {func.__name__}."):
            return
        return func(*args, **kwargs)
    return wrapper

class AuctionFormatter:
    def __init__(self, auction_id, gui_callback, should_stop, callback, selected_warehouse, show_browser):
        self.Auction_ID = auction_id
        self.gui_callback = gui_callback
        self.should_stop = should_stop
        self.callback = callback
        self.selected_warehouse = selected_warehouse
        self.show_browser = show_browser
        
        config_manager.set_active_warehouse(selected_warehouse)
        
        self.final_csv_path = None

    def configure_driver(self, url):
        firefox_options = FirefoxOptions()
        if not self.show_browser:
            firefox_options.add_argument("--headless")
        driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=firefox_options)
        driver.get(url)
        return driver

    def should_continue(self, message):
        if self.should_stop.is_set():
            self.gui_callback(message)
            return False
        return True

    def login_to_website(self, driver, username, password):
        if not self.should_continue("Login operation stopped by user."):
            return False

        self.gui_callback("Logging In...")
        try:
            # Wait for the body element to be present
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            # Explicitly wait for the username field to ensure the page is fully loaded
            self.gui_callback("Waiting for username field to be present...")
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "username")))
            
            # Locate the username field and input the username
            self.gui_callback("Locating username field...")
            username_field = driver.find_element(By.ID, "username")
            username_field.clear()
            username_field.send_keys(username)
            
            # Locate the password field and input the password
            self.gui_callback("Locating password field...")
            password_field = driver.find_element(By.ID, "password")
            password_field.clear()
            password_field.send_keys(password)

            if not self.should_continue("Login operation stopped before finalizing."):
                return False

            # Submit the login form
            self.gui_callback("Submitting login form...")
            password_field.send_keys(Keys.RETURN)
            
            # Wait for the next page to load and confirm the login was successful
            self.gui_callback("Waiting for login to complete...")
            
            # Wait for the page to load completely
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
            )
            
            # Check for elements that should be present after login
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.LINK_TEXT, "Sign Out"))
                )
                self.gui_callback("Login successful.")
                return True
            except:
                self.gui_callback("Login failed: Could not find 'Sign Out' link.")
                return False

        except Exception as e:
            self.gui_callback(f"Login failed: Unexpected error. Error: {str(e)}")
            return False

    def upload_csv_to_website(self, driver, csv_path):
        try:
            # Navigate to the ImportCSV page
            self.gui_callback("Navigating to ImportCSV page...")
            driver.get("https://bid.702auctions.com/Admin/ImportCSV")
            
            # Wait for the page to load
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
            )
            
            # Check if we're on the correct page
            if "ImportCSV" not in driver.current_url:
                self.gui_callback("Failed to navigate to ImportCSV page. Current URL: " + driver.current_url)
                return False
            
            # Wait for the file input element to be present
            self.gui_callback("Waiting for file input element...")
            file_input = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "csvFile"))
            )
            file_input.send_keys(csv_path)
            
            self.gui_callback("Locating submit button...")
            submit_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'][value='Upload']"))
            )
            submit_button.click()
            
            self.gui_callback("Waiting for upload confirmation...")
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".alert-success"))
            )
            
            self.gui_callback("CSV file uploaded successfully.")
            return True
        except Exception as e:
            self.gui_callback(f"Failed to upload CSV: {str(e)}")
            return False

    def upload_csv_to_website(self, driver, csv_path):
        try:
            # Navigate to the ImportCSV page
            self.gui_callback("Navigating to ImportCSV page...")
            driver.get("https://bid.702auctions.com/Admin/ImportCSV")
            
            # Wait for the page to load
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
            )
            
            # Check if we're on the correct page
            if "ImportCSV" not in driver.current_url:
                self.gui_callback("Failed to navigate to ImportCSV page. Current URL: " + driver.current_url)
                return False
            
            # Update the email address
            self.gui_callback("Updating email address...")
            try:
                email_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "Text1"))
                )
                email_input.clear()
                email_input.send_keys("matthew@702auctions.com")
                time.sleep(2)  # Wait for 2 seconds after updating the email
                self.gui_callback("Email address updated successfully.")
            except Exception as e:
                self.gui_callback(f"Failed to update email address: {str(e)}")
            
            # Locate and interact with the file input
            self.gui_callback("Uploading CSV file...")
            try:
                # Try multiple methods to locate the file input
                file_input = None
                try:
                    file_input = driver.find_element(By.ID, "CSVFile")
                except:
                    try:
                        file_input = driver.find_element(By.NAME, "CSVFile")
                    except:
                        file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
                
                if file_input:
                    # Ensure the file input is visible and enabled
                    driver.execute_script("arguments[0].style.display = 'block';", file_input)
                    driver.execute_script("arguments[0].style.visibility = 'visible';", file_input)
                    driver.execute_script("arguments[0].style.opacity = 1;", file_input)
                    
                    # Send the file path
                    file_input.send_keys(csv_path)
                    self.gui_callback("CSV file selected successfully.")
                else:
                    raise Exception("Could not locate file input element")
            except Exception as e:
                self.gui_callback(f"Failed to select CSV file: {str(e)}")
                return False
            
            # Click the "Upload CSV" button
            self.gui_callback("Clicking Upload CSV button...")
            try:
                upload_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'][value='Upload CSV']"))
                )
                ActionChains(driver).move_to_element(upload_button).click().perform()
                self.gui_callback("Upload CSV button clicked.")
            except Exception as e:
                self.gui_callback(f"Failed to click Upload CSV button: {str(e)}")
                return False
            
            # Wait for the upload confirmation
            self.gui_callback("Waiting for upload confirmation...")
            try:
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".alert-success"))
                )
                self.gui_callback("CSV file uploaded successfully.")
                return True
            except Exception as e:
                self.gui_callback(f"Failed to confirm CSV upload: {str(e)}")
                return False

        except Exception as e:
            self.gui_callback(f"Failed to upload CSV: {str(e)}")
            return False

    def run_auction_formatter(self):
        try:
            airtable_records = get_airtable_records_list(
                config_manager.get_warehouse_var('airtable_inventory_base_id'),
                config_manager.get_warehouse_var('airtable_inventory_table_id'),
                config_manager.get_warehouse_var('airtable_send_to_auction_view_id'),
                self.gui_callback,
                config_manager.get_warehouse_var('airtable_api_key')
            )

            download_tasks = collect_image_urls(airtable_records, self.should_stop)
            downloaded_images = download_images_bulk(download_tasks, self.gui_callback, self.should_stop)
            processed_images = process_images_in_bulk(downloaded_images, self.gui_callback, self.should_stop)
            uploaded_image_urls = upload_images_and_get_urls(processed_images, self.gui_callback, self.should_stop)

            processed_records, failed_records = process_records_concurrently(
                airtable_records, uploaded_image_urls, self.gui_callback, 
                self.Auction_ID, self.selected_warehouse, self.should_stop
            )

            if processed_records:
                csv_path = processed_records_to_df(processed_records, self.Auction_ID, self.gui_callback)
                self.final_csv_path = self.format_final_csv(csv_path)

            if self.final_csv_path:
                website_url = "https://bid.702auctions.com/Account/LogOn"
                username = config_manager.get_warehouse_var('bid_username')
                password = config_manager.get_warehouse_var('bid_password')

                driver = self.configure_driver(website_url)
                
                login_success = self.login_to_website(driver, username, password)
                
                if login_success:
                    self.gui_callback("Login successful. Attempting to upload CSV...")
                    csv_filename = f"{self.Auction_ID}.csv"
                    csv_path = os.path.join("C:\\Users\\matt9\\Desktop\\auction_webapp\\auction\\resources\\processed_csv", csv_filename)
                    if os.path.exists(csv_path):
                        upload_success = self.upload_csv_to_website(driver, csv_path)
                        if upload_success:
                            self.gui_callback(f"CSV uploaded successfully to 702 Auctions.")
                        else:
                            self.gui_callback(f"CSV upload to 702 Auctions failed.")
                    else:
                        self.gui_callback(f"CSV file not found at {csv_path}")
                else:
                    self.gui_callback(f"Login to 702 Auctions failed.")
                
                self.gui_callback("Final URL: " + driver.current_url)
                driver.quit()

            if failed_records:
                self.failed_records_csv_filepath = failed_records_csv(failed_records, self.Auction_ID, self.gui_callback)

            organize_images(self.Auction_ID)
        except Exception as e:
            self.gui_callback(f"Error: {e}")
        finally:
            self.callback()

    def format_final_csv(self, file_path):
        try:
            data = pd.read_csv(file_path)
            self.gui_callback(f"Initial data loaded with {len(data)} records.")

            data['UPC'] = data['UPC'].astype(str)
            data['MSRP'] = pd.to_numeric(data['MSRP'], errors='coerce').round(2)

            sorted_data = data.sort_values(by='MSRP', ascending=False)

            top_50_items = sorted_data[~sorted_data['Subtitle'].str.contains('missing|damaged|no', case=False, na=False)].head(50)

            remaining_items = sorted_data[~sorted_data.index.isin(top_50_items.index)].sample(frac=1).reset_index(drop=True)

            final_data = pd.concat([top_50_items, remaining_items]).reset_index(drop=True)
            
            resources_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'resources', 'processed_csv')
            os.makedirs(resources_dir, exist_ok=True)

            output_file_path = os.path.join(resources_dir, f'{self.Auction_ID}.csv')
            final_data.to_csv(output_file_path, index=False)

            self.gui_callback(f"Formatted data saved to {output_file_path}")
            return output_file_path
        except Exception as e:
            self.gui_callback(f"Error formatting final CSV: {e}")
            return None