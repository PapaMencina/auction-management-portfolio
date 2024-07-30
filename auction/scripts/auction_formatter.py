from typing import List, Dict
import ftplib
import os
import requests
import threading
import json
import pandas as pd
import concurrent.futures
import shutil
from PIL import Image, ExifTags
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import re
from auction.utils import config_manager

# Define the config path
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, '..', 'utils', 'config.json')

# Ensure global variables are set after loading config
config_manager.load_config(config_path)  # Load the configuration here

# Now get the global variables
AIRTABLE_TOKEN = config_manager.get_global_var('airtable_api_key')
AIRTABLE_INVENTORY_BASE_ID = config_manager.get_global_var('airtable_inventory_base_id')
AIRTABLE_INVENTORY_TABLE_ID = config_manager.get_global_var('airtable_inventory_table_id')
AIRTABLE_SEND_TO_AUCTION_VIEW = config_manager.get_global_var('airtable_send_to_auction_view_id')

newRecord = {}
otherParams = {}

def auction_formatter_main(auction_id, selected_warehouse, gui_callback, should_stop, callback):
    if not isinstance(should_stop, threading.Event):
        should_stop = threading.Event()

    config_manager.set_active_warehouse(selected_warehouse)

    AIRTABLE_TOKEN = config_manager.get_warehouse_var('airtable_api_key')
    AIRTABLE_INVENTORY_BASE_ID = config_manager.get_warehouse_var('airtable_inventory_base_id')
    AIRTABLE_INVENTORY_TABLE_ID = config_manager.get_warehouse_var('airtable_inventory_table_id')
    AIRTABLE_SEND_TO_AUCTION_VIEW = config_manager.get_warehouse_var('airtable_send_to_auction_view_id')

    print(f"AIRTABLE_TOKEN: {AIRTABLE_TOKEN}")
    print(f"AIRTABLE_INVENTORY_BASE_ID: {AIRTABLE_INVENTORY_BASE_ID}")
    print(f"AIRTABLE_INVENTORY_TABLE_ID: {AIRTABLE_INVENTORY_TABLE_ID}")
    print(f"AIRTABLE_SEND_TO_AUCTION_VIEW: {AIRTABLE_SEND_TO_AUCTION_VIEW}")

    formatter = AuctionFormatter(
        auction_id, 
        gui_callback, 
        should_stop, 
        callback, 
        selected_warehouse,
        AIRTABLE_TOKEN,
        AIRTABLE_INVENTORY_BASE_ID,
        AIRTABLE_INVENTORY_TABLE_ID,
        AIRTABLE_SEND_TO_AUCTION_VIEW
    )
    formatter.run_auction_formatter()

def get_extension_from_content_disposition(content_disposition):
    filename_match = re.search(r'filename="([^"]+)"', content_disposition)
    if filename_match:
        filename = filename_match.group(1)
        return os.path.splitext(filename)[1][1:]
    return None

def get_image_orientation(img):
    try:
        if hasattr(img, '_getexif'):
            exif = img._getexif()
            if exif is not None:
                for tag, value in exif.items():
                    decoded_tag = ExifTags.TAGS.get(tag, tag)
                    if decoded_tag == 'Orientation':
                        return value
        return None
    except IOError:
        print(f"Error opening image file {img}")
        return None

def download_image(url, file_name, gui_callback):
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
            if not file_extension.lower() in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
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

def process_image(file_path, gui_callback, width_threshold=1024, dpi_threshold=72):
    try:
        with Image.open(file_path) as img:
            exif = img._getexif()
            orientation = None
            if exif is not None:
                for tag, value in exif.items():
                    decoded_tag = ExifTags.TAGS.get(tag, tag)
                    if decoded_tag == 'Orientation':
                        orientation = value
                        break

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
        error_msg = f"Error processing image {file_path}: {e}"
        gui_callback(error_msg)
        pass

    return None

def convert_webp_to_jpeg(file_path, gui_callback):
    try:
        with Image.open(file_path) as im:
            if im.mode == 'P':
                im = im.convert("RGB")
            new_file_path = os.path.splitext(file_path)[0] + ".jpg"
            im.save(new_file_path, "JPEG")
            return new_file_path
    except Exception as e:
        error_msg = (f"Error converting WebP to JPEG: {e}")
        gui_callback(error_msg)
        pass

def process_image_wrapper(image_path, gui_callback, should_stop):
    if should_stop.is_set():
        return image_path
    if image_path.endswith(".webp"):
        image_path = convert_webp_to_jpeg(image_path, gui_callback)
    process_image(image_path, gui_callback)
    return image_path

def process_images_in_bulk(downloaded_images_bulk, gui_callback, should_stop):
    gui_callback("Processing Images...")
    all_image_paths = []
    record_id_map = {}

    for record_id, image_paths in downloaded_images_bulk.items():
        for image_path in image_paths:
            all_image_paths.append(image_path)
            record_id_map[image_path] = record_id

    processed_images = {}
    with ThreadPoolExecutor() as executor:
        future_to_image = {}

        for img_path in all_image_paths:
            if should_stop.is_set():
                break

            future = executor.submit(process_image_wrapper, img_path, gui_callback, should_stop)
            future_to_image[future] = img_path

        for future in as_completed(future_to_image):
            if should_stop.is_set():
                break

            img_path = future_to_image[future]
            try:
                result = future.result()
                record_id = record_id_map[img_path]
                if record_id not in processed_images:
                    processed_images[record_id] = []
                processed_images[record_id].append(result)
            except Exception as e:
                gui_callback(f"Error processing image {img_path}: {e}")

    return processed_images

def upload_image(file_path, gui_callback, should_stop, max_retries=3, retry_delay=10):
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

def format_subtitle(auction_count, msrp, other_notes):
    msrp = str(msrp)
    if auction_count >= 4:
        final_msrp = f"MSRP: ${msrp}"
    elif auction_count == 3:
        final_msrp = f"MSRP: ${msrp} ---"
    elif auction_count == 2:
        final_msrp = f"MSRP: ${msrp} --"
    else:
        final_msrp = f"MSRP: ${msrp} -"
        
    notes_str = f"NOTES: {other_notes}" if other_notes and other_notes != " " else ""
    final_note = str(final_msrp + " " + notes_str)[:80]
    return final_note

def category_converter(category):
    category_dict = {
        2830472: "appliances",
        2830485: ["arts, crafts & sewing","arts,crafts & sewing", "arts & crafts", "arts"],
        339711: ["automotive", "automotive parts & accessories"],
        339747: "furniture",
        2830498: "baby products",
        2830511: "beauty & personal care",
        2830524: "cell phones & accessories",
        2830537: ["clothing", "clothing,shoes & jewelry","clothing, shoes & jewelry"],
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

def get_airtable_records_list(BASE: str, TABLE: str, VIEW: str, gui_callback, airtable_token) -> List[Dict]:
    gui_callback("Getting Airtable Records...")
    responseList = []
    pages = [str(x) for x in range(1, 16)]
    offset = ""
    data = {}
    myHeaders = {
        "Authorization": f"Bearer {airtable_token}",
        "Content-Type": "application/json",
    }

    for page in pages:
        try:
            url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?view={VIEW}"
            if offset:
                url += f"&offset={offset}"

            gui_callback(f"Requesting URL: {url}")
            print(f"Requesting URL: {url}")
            response = requests.get(url, params=data, headers=myHeaders)
            if response.status_code != 200:
                gui_callback(f"Error fetching data from Airtable: {response.status_code} {response.text}")
                print(f"Error fetching data from Airtable: {response.status_code} {response.text}")
                break

            response_json = response.json()
            records = response_json.get("records", [])
            responseList.extend(records)
            gui_callback(f"Page {page}: Retrieved {len(records)} records")

            offset = response_json.get("offset", "")
            if not offset:
                break
        except Exception as e:
            gui_callback(f"Exception occurred: {e}")
            print(f"Exception occurred: {e}")
            break

    gui_callback(f"Retrieved a total of {len(responseList)} records from Airtable")
    print(f"Retrieved a total of {len(responseList)} records from Airtable")
    return responseList

def text_shortener(inputText, strLen):
    if len(inputText) > strLen:
        end = inputText.rfind(' ', 0, strLen)
        if end == -1:
            return inputText[:strLen].strip()
        return inputText[:end].strip()
    return inputText

def format_msrp(msrp):
    try:
        if msrp >= 15:
            return "5.00"
        elif msrp <= 10:
            return "1.00"
        else:
            return "2.50"
    except ValueError:
        return ""

def collect_image_urls(airtable_records, should_stop):
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

def download_images_bulk(download_tasks, gui_callback, should_stop):
    gui_callback("Downloading Images...")
    image_paths = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
        futures = []

        for record_id, image_url, file_name in download_tasks:
            if should_stop.is_set():
                break

            future = executor.submit(download_image, image_url, file_name, gui_callback)
            futures.append((record_id, future))

        for record_id, future in futures:
            if should_stop.is_set():
                break

            try:
                downloaded_path = future.result()
                if downloaded_path:
                    if record_id not in image_paths:
                        image_paths[record_id] = []
                    image_paths[record_id].append(downloaded_path)
            except Exception as e:
                gui_callback(f"Error downloading image: {e}")

    return image_paths

def upload_images_and_get_urls(downloaded_images, gui_callback, should_stop):
    gui_callback("Uploading Images...")
    uploaded_image_urls = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = []

        for record_id, image_paths in downloaded_images.items():
            for image_path in image_paths:
                if should_stop.is_set():
                    return uploaded_image_urls

                future = executor.submit(upload_image, image_path, gui_callback, should_stop)
                futures.append((record_id, future))

        for record_id, future in futures:
            if should_stop.is_set():
                return uploaded_image_urls

            try:
                url = future.result()
                if url:
                    if not url.startswith("https://"):
                        url = "https://" + url
                    uploaded_image_urls.setdefault(record_id, []).append(url)
            except Exception as e:
                gui_callback(f"Error uploading image {image_path}: {e}")

    return uploaded_image_urls

def format_field(label, value):
    return f"{label}: {value}" if value is not None and str(value).strip() else ""

def get_image_url(airtable_record, count):
    key = f"Image {count}"
    return airtable_record.get("fields", {}).get(key, [{}])[0].get("url", "")

def upload_file_via_ftp(file_name, local_file_path, gui_callback, should_stop, max_retries=3, remote_file_path="/airtableimages.702auctions.com/public_html/", server="702auctions.com", username="702auctionsftp@702auctions.com", password="Ronch420$"):
    retries = 0
    while retries < max_retries and not should_stop.is_set():
        try:
            ftp = ftplib.FTP(server, username, password)
            ftp.set_pasv(True)
            ftp.cwd('/')
            ftp.sendcmd('TYPE I')
            remote_path_full = os.path.join(remote_file_path, file_name)

            with open(local_file_path, 'rb') as file:
                ftp.storbinary(f'STOR {remote_path_full}', file)
            ftp.quit()

            formatted_url = remote_path_full.replace("/public_html", "", 1).lstrip('/')
            return f"https://{formatted_url}"

        except ftplib.error_temp as e:
            print(f"Temporary FTP error: {e}. Retrying in 5 seconds...")
            retries += 1
            time.sleep(5)
        except Exception as e:
            print(f"FTP upload error: {e}.")
            break
        finally:
            if 'ftp' in locals() and ftp.sock:
                ftp.quit()
    print("Failed to upload after maximum retries.")
    return None

def format_html_field(field_name, value):
    if value:
        return f"<b>{field_name}</b>: {value}<br>"
    return ""

def process_single_record(airtable_record, uploaded_image_urls, Auction_ID, selected_warehouse):
    try:
        record_template = {}
        newRecord = dict(record_template)
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
        
        # Set Region based on selected warehouse
        if selected_warehouse == "Maule Warehouse":
            newRecord["Region"] = "88850842"
        elif selected_warehouse == "Sunrise Warehouse":
            newRecord["Region"] = "88850843"
        else:
            newRecord["Region"] = ""

        newRecord["Source"] = "AMZ FC"
        newRecord["IsTaxable"] = "TRUE".upper()
        newRecord["Quantity"] = "1"
        newRecord["Seller"] = "702Auctions"
        
        title = airtable_record["fields"]["Product Name"]
        if selected_warehouse == "Sunrise Warehouse":
            title = "OFFSITE " + title
        title = text_shortener(title, 80)
        
        newRecord["Title"] = title
        category_airtable = category_converter(newRecord.get("Category_not_formatted", ""))
        newRecord["Category"] = category_airtable if category_airtable else ""

        auction_count = newRecord.get("AuctionCount", 0)
        if auction_count == 1:
            newRecord["Price"] = "5.00"
        elif auction_count == 2:
            newRecord["Price"] = "2.50"
        elif auction_count >= 3:
            newRecord["Price"] = "1.00"
        else:
            newRecord["Price"] = "5.00"

        formatted_subtitle = format_subtitle(
            newRecord.get("AuctionCount", ""),
            newRecord.get("MSRP", ""),
            newRecord.get("Other Notes", "")
        )
        newRecord["Subtitle"] = formatted_subtitle if formatted_subtitle else ""

        record_id = airtable_record['id']
        if record_id in uploaded_image_urls:
            for url in uploaded_image_urls[record_id]:
                image_number = url.split('_')[-1].split('.')[0]
                newRecord[f'Image_{image_number}'] = url

        newRecord['Success'] = True
        return newRecord
    except Exception as e:
        lot_number = airtable_record.get('fields', {}).get('Lot Number', 'Unknown')
        error_message = f"Error processing Lot Number {lot_number}: {e}"
        return {'Lot Number': lot_number, 'Failure Message': error_message, 'Success': False}

def process_records_concurrently(airtable_records, uploaded_image_urls, gui_callback, auction_id, selected_warehouse, should_stop):
    gui_callback("Creating CSV...")
    processed_records = []
    failed_records = []

    with ThreadPoolExecutor() as executor:
        futures = []

        for record in airtable_records:
            if should_stop.is_set():
                return processed_records, failed_records

            future = executor.submit(process_single_record, record, uploaded_image_urls, auction_id, selected_warehouse)
            futures.append(future)

        for future in as_completed(futures):
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

def failed_records_csv(failed_records, Auction_ID, gui_callback):
    failed_dataframe = pd.DataFrame(failed_records, columns=['Lot Number', 'Failure Message'])
    download_path = os.path.join(get_resources_dir('failed_csv'), f'{Auction_ID}-FAILED.csv')
    failed_dataframe.to_csv(download_path, index=False)
    gui_callback(f'Failed records have been saved to {download_path}.')

def processed_records_to_df(processed_records, Auction_ID, gui_callback):
    df = pd.DataFrame(processed_records)
    column_order = ["EventID", "LotNumber", "Seller", "Category_not_formatted", "Category", "Region", "ListingType", "Currency",
                    "Title", "Subtitle", "Description", "Price", "Quantity", "IsTaxable", "Image_1", "Image_2", "Image_3", "Image_4",
                    "Image_5", "Image_6", "Image_7", "Image_8", "Image_9", "Image_10", "YouTubeID", "PdfAttachments", "Bold", "Badge",
                    "Highlight", "ShippingOptions", "Duration", "StartDTTM", "EndDTTM", "AutoRelist", "GoodTilCanceled", "Working Condition",
                    "UPC", "Truck", "Source", "Size", "Photo Taker", "Packaging", "Other Notes", "MSRP", "Lot Number", "Location",
                    "Item Condition", "ID", "Amazon ID", "HiBid", "AuctionCount", "number"]
    df = df.reindex(columns=column_order, fill_value='')
    
    # Define the directory path
    resources_dir = os.path.join(script_dir, '..', 'resources', 'processed_csv')
    os.makedirs(resources_dir, exist_ok=True)
    
    download_path = os.path.join(resources_dir, f'unformatted_{Auction_ID}.csv')
    df.to_csv(download_path, index=False)
    gui_callback(f'Successful records have been saved to {download_path}.')

    return download_path

def get_resources_dir(folder):
    return os.path.join('C:\\Users\\matt9\\Desktop\\Auction_script_current\\resources', folder)

def organize_images(Auction_ID):
    file_count = 0
    directory = get_resources_dir('product_images')
    subfolder = get_resources_dir('hibid_images') + f'/hibid_{Auction_ID}'

    if os.path.isdir(subfolder):
        shutil.rmtree(subfolder)
    os.mkdir(subfolder)

    for file in os.listdir(directory):
        if file.endswith("_1.jpeg") or file.endswith("_1.png") or file.endswith('_1.jpg'):
            shutil.move(os.path.join(directory, file), subfolder)
            file_count += 1
        elif file.endswith('.jpg') or file.endswith("png") or file.endswith(".jpeg") or file.endswith(".webp"):
            os.remove(os.path.join(directory, file))

def check_continuation(func):
    def wrapper(*args, **kwargs):
        self = args[0]
        if not self.should_continue(self.should_stop, self.gui_callback, f"Operation stopped before {func.__name__}."):
            return
        return func(*args, **kwargs)
    return wrapper

class AuctionFormatter:
    def __init__(self, auction_id, gui_callback, should_stop, callback, selected_warehouse, airtable_token, inventory_base_id, inventory_table_id, send_to_auction_view):
        self.Auction_ID = auction_id
        self.gui_callback = gui_callback
        self.should_stop = should_stop if isinstance(should_stop, threading.Event) else threading.Event()
        self.callback = callback
        self.selected_warehouse = selected_warehouse
        
        self.AIRTABLE_TOKEN = airtable_token
        self.AIRTABLE_INVENTORY_BASE_ID = inventory_base_id
        self.AIRTABLE_INVENTORY_TABLE_ID = inventory_table_id
        self.AIRTABLE_SEND_TO_AUCTION_VIEW = send_to_auction_view

    def should_continue(self, should_stop, gui_callback, message):
        if should_stop.is_set():
            gui_callback(message)
            return False
        return True

    @check_continuation
    def run_auction_formatter(self):
        try:
            print(f"AIRTABLE_TOKEN: {self.AIRTABLE_TOKEN}")
            print(f"AIRTABLE_INVENTORY_BASE_ID: {self.AIRTABLE_INVENTORY_BASE_ID}")
            print(f"AIRTABLE_INVENTORY_TABLE_ID: {self.AIRTABLE_INVENTORY_TABLE_ID}")
            print(f"AIRTABLE_SEND_TO_AUCTION_VIEW: {self.AIRTABLE_SEND_TO_AUCTION_VIEW}")

            airtable_records = get_airtable_records_list(
                self.AIRTABLE_INVENTORY_BASE_ID, 
                self.AIRTABLE_INVENTORY_TABLE_ID, 
                self.AIRTABLE_SEND_TO_AUCTION_VIEW, 
                self.gui_callback, 
                self.AIRTABLE_TOKEN
            )
            if not airtable_records:
                self.gui_callback("No records retrieved from Airtable.")
                return

            download_tasks = collect_image_urls(airtable_records, self.should_stop)
            downloaded_images_bulk = download_images_bulk(download_tasks, self.gui_callback, self.should_stop)
            processed_images = process_images_in_bulk(downloaded_images_bulk, self.gui_callback, self.should_stop)
            uploaded_image_urls = upload_images_and_get_urls(processed_images, self.gui_callback, self.should_stop)
            processed_records, failed_records = process_records_concurrently(
                airtable_records, uploaded_image_urls, self.gui_callback, self.Auction_ID, self.selected_warehouse, self.should_stop
            )

            if not processed_records:
                self.gui_callback("No records processed successfully.")
                return

            unformatted_csv_path = processed_records_to_df(processed_records, self.Auction_ID, self.gui_callback)
            self.successful_records_csv_filepath = self.format_final_csv(unformatted_csv_path)

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

            data['UPC'] = pd.to_numeric(data['UPC'], errors='coerce').fillna('').astype(str)
            data['MSRP'] = pd.to_numeric(data['MSRP'], errors='coerce').round(2)

            sorted_data = data.sort_values(by='MSRP', ascending=False)

            top_50_items = sorted_data[~sorted_data['Subtitle'].str.contains('missing|damaged|no', case=False, na=False)].head(50)

            remaining_items = sorted_data[~sorted_data.index.isin(top_50_items.index)].sample(frac=1).reset_index(drop=True)

            final_data = pd.concat([top_50_items, remaining_items]).reset_index(drop=True)
            
            # Define the directory path
            resources_dir = os.path.join(script_dir, '..', 'resources', 'processed_csv')
            os.makedirs(resources_dir, exist_ok=True)

            output_file_path = os.path.join(resources_dir, f'{self.Auction_ID}.csv')
            final_data.to_csv(output_file_path, index=False)

            self.gui_callback(f"Formatted data saved to {output_file_path}")
            return output_file_path
        except Exception as e:
            self.gui_callback(f"Error formatting final CSV: {e}")
            return None
