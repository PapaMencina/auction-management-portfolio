import time
from datetime import datetime
import pandas as pd
import os
import re
import json
import threading
from django.conf import settings
from auction.utils import config_manager
import logging
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, expect
from auction.utils.progress_tracker import with_progress_tracking

logger = logging.getLogger(__name__)

config_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'config.json')
config_manager.load_config(config_path)

BASE_URL = "https://www.auctionflex360.com/#/organization/5676/auctions/new"

@with_progress_tracking
def upload_to_hibid_main(auction_id, ending_date, auction_title, gui_callback, should_stop, callback, selected_warehouse, update_progress):
    config_manager.set_active_warehouse(selected_warehouse)
    run_upload_to_hibid(auction_id, ending_date, auction_title, gui_callback, should_stop, callback, selected_warehouse, update_progress)

if __name__ == "__main__":
    upload_to_hibid_main("sample_auction_id", "2023-12-31 18:30:00", "Sample Auction Title", print, threading.Event(), lambda: print("Callback"), "Maule Warehouse", lambda x, y: print(f"Progress: {x}%, Message: {y}"))

def get_resource_path(resource_type, filename=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    base_path = os.path.join(project_root, 'auction', 'resources')
    
    resource_paths = {
        'processed_csv': os.path.join(base_path, 'processed_csv'),
        'hibid_csv': os.path.join(base_path, 'hibid_csv'),
        'hibid_images': os.path.join(base_path, 'hibid_images'),
        'bid_stock_photo': os.path.join(base_path, 'bid_stock_photo'),
        'downloads': os.path.join(base_path, 'downloads'),
    }
    
    if resource_type not in resource_paths:
        raise ValueError(f"Unknown resource type: {resource_type}")
    
    path = resource_paths[resource_type]
    
    if filename:
        path = os.path.join(path, filename)
    
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    return path

def get_resources_dir(folder):
    base_dir = os.path.abspath("C:\\Users\\matt9\\Desktop\\Auction_script_current\\resources")
    return os.path.join(base_dir, folder)

fixed_lines_df = pd.DataFrame({
    "Lot Number": [1, 2],
    "Seller Code": [1234, 1234],
    "Title": ["BIDDING LIVE AT 702AUCTIONS.COM", "PICKUP ONLY. NO SHIPPING OFFERED"],
    "Description": [
        "BIDDING LIVE AT bid.702auctions.com! Click the link to bid now.",
        "PICKUP ONLY. NO SHIPPING OFFERED."
    ],
    "Quantity": [1, 1],
    "Start Bid Each": [5, 5],
    "Image_1": ["", ""],
    "Sale Order": [1, 2]
})

def login(page, username, password, gui_callback, should_stop):
    try:
        page.goto("https://www.auctionflex360.com/#/login")
        gui_callback("Navigating to login page.")

        page.wait_for_load_state("networkidle")
        gui_callback("Page loaded.")

        if "login" not in page.url.lower():
            gui_callback(f"Unexpected URL after navigation: {page.url}")
            return False

        if username is None or password is None:
            gui_callback("Error: Username or password is None. Please check your configuration.")
            return False

        email_field = page.wait_for_selector("input[name='email']", state="visible", timeout=30000)
        gui_callback("Email field found.")

        email_field.fill(username)
        gui_callback(f"Email entered: {username}")

        password_field = page.wait_for_selector("input[name='password']", state="visible", timeout=30000)
        gui_callback("Password field found.")

        password_field.fill(password)
        gui_callback("Password entered.")

        page.wait_for_selector(".preloader", state="hidden", timeout=30000)
        gui_callback("Preloader disappeared.")

        login_button = page.wait_for_selector("input[type='submit'][value='Log In']", state="visible", timeout=30000)
        login_button.click()
        gui_callback("Login button clicked.")

        page.wait_for_url("**/organization/**", timeout=30000)
        gui_callback("Login successful.")
        return True

    except Exception as e:
        gui_callback(f"Unexpected error during login: {str(e)}")
        import traceback
        gui_callback(f"Traceback: {traceback.format_exc()}")
        return False

def select_dropdown_options(page, mappings, gui_callback):
    for label_text, value in mappings.items():
        try:
            select_element = page.wait_for_selector(f"//label[contains(text(), '{label_text}')]/following-sibling::div/select", timeout=15000)
            select_element.select_option(value=value)
        except Exception as e:
            gui_callback(f"An error occurred while selecting '{label_text}': {e}")

mappings = {
    "Lot Number": "LotNumber",
    "Seller Code": "SellerCode",
    "Title": "Title",
    "Description": "Description",
    "Quantity": "Quantity",
    "Start Bid Each": "StartBidEach",
    "Sale Order": "SaleOrder"
}

def remove_special_characters(text):
    return re.sub(r'[^\w\s]', ' ', text)

def truncate_title(title):
    title = title.replace('|', ' ')
    if len(title) > 49:
        return ' '.join(title[:49].split(' ')[:-1])
    return title

def transform_csv_with_fixed_lines(input_csv_path):
    try:
        input_df = pd.read_csv(input_csv_path)
        print("CSV loaded successfully.")
    except Exception as e:
        print(f"Failed to read CSV: {e}")
        return None, None, None, None

    if input_df is None or input_df.empty:
        print("Input DataFrame is None or empty.")
        return None, None, None, None

    auction_id = input_df.iloc[0]['EventID'] if 'EventID' in input_df.columns else None 

    if auction_id is None:
        print("Auction ID is None.")
        return None, None, None, None

    input_df['Title'] = input_df['Title'].apply(lambda x: truncate_title(remove_special_characters(x.replace('|', ' '))))
    input_df['Description'] = input_df['HiBid'].apply(lambda x: remove_special_characters(x.replace('|', ' ')))

    transformed_df = pd.DataFrame({
        'Lot Number': input_df['LotNumber'],
        'Seller Code': 1234,
        'Description': input_df['Description'],
        'Quantity': 1,
        'Start Bid Each': 5,
        'Sale Order': input_df.index + 3,
        'Title': input_df['Title']
    })

    final_df = pd.concat([fixed_lines_df, transformed_df], ignore_index=True)
    lot_number_list = list(final_df['Lot Number'])

    if len(lot_number_list) == 0:
        print("Lot number list is empty.")
        return None, None, None, None

    hibid_csv_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'resources', 'hibid_csv')
    os.makedirs(hibid_csv_dir, exist_ok=True)
    output_csv_filename = os.path.join(hibid_csv_dir, f'{auction_id}_hibid.csv')
    transformed_csv_path = os.path.abspath(output_csv_filename)
    final_df.to_csv(transformed_csv_path, index=False)

    print(f"Transformed CSV saved to {transformed_csv_path}")

    return len(input_df), auction_id, transformed_csv_path, lot_number_list

def is_div_empty(page):
    try:
        div_element = page.wait_for_selector(".image-list.list-group", timeout=15000)
        images = div_element.query_selector_all("img")
        return len(images) == 0
    except:
        return True

def wait_and_find(page, selector, timeout=15000):
    return page.wait_for_selector(selector, state="visible", timeout=timeout)

def fill_text_field(page, selector, text, press_return=False):
    element = wait_and_find(page, selector)
    element.fill(str(text))
    if press_return:
        element.press("Enter")

def fill_file_input(page, selector, file_path):
    element = wait_and_find(page, selector)
    element.set_input_files(file_path)

def click_button(page, selector):
    button = wait_and_find(page, selector)
    button.click()

def wait_for_element_invisibility(page, selector, timeout=30000):
    page.wait_for_selector(selector, state="hidden", timeout=timeout)

def click_off(page):
    page.keyboard.press("Escape")
    time.sleep(1)

def description(number_of_lots, ending_date, selected_warehouse):
    if selected_warehouse == "Maule Warehouse":
        return (
            f'DIRECT LINKS TO EACH PRODUCT ARE IN THE DESCRIPTIONS\n'
            f'BIDDING NOW! First Lot Closes {ending_date}\n'
            f'REGISTER AND BID AT 702AUCTIONS.COM\n'
            f'{number_of_lots} lots. Major online retailer returns.\n'
            f'All bidders must pick up their items from 1889 E. MAULE AVE. SUITE F Las Vegas, NV 89119 within 10 days.\n'
            f'702 Auctions offers returns on items that are misdescribed within 10 days of the date you picked up your items.\n'
            f'PICKUP ONLY! NO SHIPPING OFFERED.'
        )
    elif selected_warehouse == "Sunrise Warehouse":
        return (
            f'OFFSITE AUCTION\n'
            f'BIDDING NOW! First Lot Closes {ending_date}\n'
            f'REGISTER AND BID AT 702AUCTIONS.COM\n'
            f'{number_of_lots} lots. Major online retailer returns.\n'
            f'All bidders must pick up their items from 3201 Sunrise Ave, Las Vegas, NV 89101 within 10 days.\n'
            f'702 Auctions offers returns on items that are misdescribed within 10 days of the date you picked up your items.\n'
            f'PICKUP ONLY! NO SHIPPING OFFERED.'
        )
    else:
        return (
            f'DIRECT LINKS TO EACH PRODUCT ARE IN THE DESCRIPTIONS\n'
            f'BIDDING NOW! First Lot Closes {ending_date}\n'
            f'REGISTER AND BID AT 702AUCTIONS.COM\n'
            f'{number_of_lots} lots. Major online retailer returns.\n'
            f'All bidders must pick up their items from the specified location within 10 days.\n'
            f'702 Auctions offers returns on items that are misdescribed within 10 days of the date you picked up your items.\n'
            f'PICKUP ONLY! NO SHIPPING OFFERED.'
        )

def check_image(auction_id, current_lot, gui_callback):
    possible_extensions = [".jpeg", ".jpg", ".JPEG", ".JPG", ".png"]
    folder = 'hibid stock' if current_lot in [1, 2] else f'hibid_{auction_id}'
    base_file_path = os.path.join(get_resource_path('hibid_images', folder), f'{current_lot}_1')

    for ext in possible_extensions:
        file_path = base_file_path + ext
        if os.path.exists(file_path):
            return file_path

    gui_callback(f'Image for lot {current_lot} not found(function).')
    return None

def handle_url_check(page, fallback_url, gui_callback, should_stop, username, password):
    current_url = page.url
    if should_stop.is_set():
        return
    if 'login' in current_url:
        gui_callback("Detected login page. Re-logging in.")
        login(page, username, password, gui_callback, should_stop)
        time.sleep(2)
        if 'organization' not in current_url:
            try:
                select_org_button = page.wait_for_selector("button.af-view-button[title='Select Organization']", timeout=10000)
                gui_callback("Select Organization button found. Clicking it.")
                select_org_button.click()
            except:
                gui_callback("Select Organization button not found after login.")
        gui_callback("Navigating back to the last known good URL.")
        page.goto(fallback_url)
        time.sleep(2)
    else:
        gui_callback("Current URL seems fine. Continuing with the process.")

def details_page(page, auction_title, auction_id, formatted_date_only, number_of_lots, formatted_ending_date, gui_callback, selected_warehouse, update_progress):
    try:
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        update_progress(50, 'Loading Details page...')
        auction_link_url = f'https://bid.702auctions.com/Event/Details/{auction_id}?utm_source=auction&utm_medium=linkclick&utm_campaign=hibid'
        browse_link_url = f'https://bid.702auctions.com/Browse?utm_source=browse_all&utm_medium=linkclick&utm_campaign=hibid'
        file_path_702_logo = get_resource_path('bid_stock_photo', '702_logo.png')

        fill_text_field(page, "#name", auction_title)
        page.wait_for_timeout(1000)
        ending_date_field = wait_and_find(page, "input[name='newAuctionEndDate']")
        ending_date_field.evaluate("el => el.removeAttribute('readonly')")
        ending_date_field.fill(formatted_date_only)

        click_button(page, "button:has-text('Save')")
        page.wait_for_timeout(1000)
        fill_text_field(page, "#auctionCode", auction_id)
        formatted_description = description(number_of_lots, formatted_ending_date, selected_warehouse)
        fill_text_field(page, "textarea#description", formatted_description)
        
        if selected_warehouse == "Maule Warehouse":
            fill_text_field(page, "#address1", "1889 E. Maule Ave")
            fill_text_field(page, "#address2", "Suite F")
            fill_text_field(page, "#city", "Las Vegas")
            fill_text_field(page, "#state", "NV")
            fill_text_field(page, "#zip", "89119")
        elif selected_warehouse == "Sunrise Warehouse":
            fill_text_field(page, "#address1", "3201 Sunrise Ave")
            fill_text_field(page, "#address2", "")
            fill_text_field(page, "#city", "Las Vegas")
            fill_text_field(page, "#state", "NV")
            fill_text_field(page, "#zip", "89101")

        click_button(page, "button:has-text('NEW LINK')")
        fill_text_field(page, "div.modal input#link", auction_link_url)
        fill_text_field(page, "div.modal input#description", "CLICK HERE TO REGISTER AND BID")
        click_button(page, "div.modal button[type='submit']")
        wait_for_element_invisibility(page, "div#auctionlink_modal.modal.in")
        page.wait_for_timeout(2000)

        click_button(page, "button:has-text('NEW LINK')")
        fill_text_field(page, "div.modal input#link", browse_link_url)
        fill_text_field(page, "div.modal input#description", "CLICK HERE TO VIEW ALL AUCTIONS")
        click_button(page, "div.modal button[type='submit']")
        wait_for_element_invisibility(page, "div#auctionlink_modal.modal.in")
        page.wait_for_timeout(2000)

        file_input = wait_and_find(page, "input[type='file'].dz-hidden-input")
        file_input.set_input_files(file_path_702_logo)

        try:
            wait_for_element_invisibility(page, "div.vue-loading-msg")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(2000)
            save_button = page.wait_for_selector("button:has-text('SAVE')", state="visible", timeout=15000)
            save_button.click()
            page.wait_for_timeout(2000)
            page.reload()
        except Exception as e:
            gui_callback(f"Error saving details: {e}")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(2000)
            save_button = page.wait_for_selector("button:has-text('SAVE')", state="visible", timeout=15000)
            save_button.click()
            page.wait_for_timeout(2000)
            page.reload()
    except Exception as e:
        update_progress(55, f"Error in details_page: {e}")

def hibiduploadsettings_page(page, ending_date, todays_date, gui_callback, selected_warehouse):
    try:
        gui_callback('Loading Settings page...')
        
        if selected_warehouse == "Maule Warehouse":
            auction_date_times_text = f"Starts {todays_date}\nFirst Lot Closes {ending_date}"
            payment_information_text = "Payment via Credit Card. Registration on bid.702auctions.com required.\nPayment must be made within 24 hours of winning bid. Pickup within 7 days."
            shipping_pick_up_information_text = "Pickup Only Monday-Friday 9am-5pm from 1889 E. Maule Ave Ste F  Las Vegas, NV 89119"
            bidding_notice_text = "Online Only. To place a bid, visit 702auctions.com or click the link above!"
        elif selected_warehouse == "Sunrise Warehouse":
            auction_date_times_text = f"Starts {todays_date}\nFirst Lot Closes {ending_date}"
            payment_information_text = "Payment via Credit Card. Registration on bid.702auctions.com required.\nPayment must be made within 24 hours of winning bid. Pickup within 5 days."
            shipping_pick_up_information_text = "Pickup Only Tuesday-Friday 10am-4pm from 3201 Sunrise Ave Las Vegas, NV 89101"
            bidding_notice_text = "Online Only. To place a bid, visit 702auctions.com or click the link above!"
        
        timezone_script = """
        var xpath = '//*[@id="app"]/div[3]/div[2]/aside/section/section/div/div[3]/div/section/div/form/div[1]/div[2]/div[2]/div[2]/div[2]/div[2]/div[5]/div/select';
        var select = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        select.value = 'Pacific Standard Time';
        select.dispatchEvent(new Event('change'));
        """

        details_url = page.url
        hibiduploadsettings_url = details_url.replace('details', 'hibiduploadsettings')
        page.goto(hibiduploadsettings_url)
        page.wait_for_timeout(2000)

        fill_text_field(page, "#auction-date-times", auction_date_times_text)
        click_off(page)
        fill_text_field(page, "#payment-information", payment_information_text)
        click_off(page)
        fill_text_field(page, "#shipping-pick-up-information", shipping_pick_up_information_text)
        click_off(page)
        fill_text_field(page, "#bidding-notice", bidding_notice_text)
        no_registration_radio_button = wait_and_find(page, "#noRegistration")
        no_registration_radio_button.click()
        page.evaluate(timezone_script)
        fill_text_field(page, "#soft-close-seconds", "15")
        click_off(page)
        fill_text_field(page, "#close-bidding", ending_date)
        click_off(page)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(2000)
        click_button(page, "button:has-text('SAVE')")
        page.wait_for_timeout(3000)
    except Exception as e:
        gui_callback(f"Error in hibiduploadsettings_page: {e}")

def click_import_lots_button(page):
    try:
        button = page.wait_for_selector("button.btn.af-page-header-button:has-text('IMPORT LOTS')", state="visible", timeout=30000)
        button.click()
    except Exception as e:
        print(f"Exception: {e}")

def save_screenshot(page, name="screenshot.png"):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filepath = get_resource_path('downloads', f"{name}_{timestamp}.png")
    page.screenshot(path=filepath)
    logger.info(f"Screenshot saved to {filepath}")

def wait_for_element_to_be_clickable(page, selector, timeout=30000):
    return page.wait_for_selector(selector, state="visible", timeout=timeout)

def lots_page(page, transformed_csv_path, auction_id, lot_numbers_list, gui_callback, should_stop, update_progress):
    update_progress(70, 'Loading Lots page...')
    hibiduploadsettings_url = page.url
    lots_url = hibiduploadsettings_url.replace('hibiduploadsettings', 'lots')
    page.goto(lots_url)
    page.wait_for_timeout(2000)
    update_progress(72, 'Importing CSV...')

    try:
        click_import_lots_button(page)
    except Exception as e:
        update_progress(73, f"Error clicking 'Import Lots' button: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    try:
        print(transformed_csv_path)
        fill_file_input(page, "#files", os.path.abspath(transformed_csv_path))
    except Exception as e:
        update_progress(74, f"Error filling 'files' field: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    page.wait_for_timeout(2000)

    try:
        click_button(page, "div.modal-buttons button#submit")
    except Exception as e:
        update_progress(75, f"Error clicking 'submit' button: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    page.wait_for_timeout(2000)

    try:
        select_dropdown_options(page, mappings, gui_callback)
    except Exception as e:
        update_progress(76, f"Error selecting dropdown options: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    page.wait_for_timeout(2000)

    try:
        click_button(page, "#import")
    except Exception as e:
        update_progress(77, f"Error clicking 'import' button: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    try:
        click_button(page, "button:has-text('IMPORT')")
    except Exception as e:
        update_progress(78, f"Error clicking 'IMPORT' button: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    stop_import_xpath = "//button[@type='button' and contains(@class, 'btn') and contains(@class, 'af-action-button') and .//span[contains(text(), 'STOP IMPORT')]]"

    try:
        page.wait_for_selector(stop_import_xpath, state="hidden", timeout=90000)
        update_progress(80, "Stop Import has disappeared")
    except Exception as e:
        update_progress(80, f"Error waiting for 'Stop Import' text to disappear: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    page.wait_for_timeout(1000)
    close_button_selector = 'button[data-dismiss="modal"].btn.af-cancel-button'

    try:
        close_button = page.wait_for_selector(close_button_selector, state="visible", timeout=10000)
        update_progress(81, "CLOSE button is present")
    except:
        update_progress(81, "TimeoutException: CLOSE button is not present")
        save_screenshot(page, "close_button_not_present")
        return
    
    try:
        close_button.scroll_into_view_if_needed()
        close_button.click()
        update_progress(82, "CLOSE button clicked")
    except Exception as js_click_exception:
        update_progress(82, f"Exception during click: {js_click_exception}")
        save_screenshot(page, "js_click_exception_close_button")

    try:
        click_button(page, '#lotTable_1 thead tr th:nth-child(3)')
        update_progress(83, "Clicked 'sort by lot order' button")
    except Exception as e:
        update_progress(83, f"Error clicking 'sort by lot order' button: {e}")
        page.screenshot(path="error_screenshot.png")
        return

    update_progress(84, "Starting to process lots...")
    process_lots(page, auction_id, lots_url, lot_numbers_list, gui_callback, should_stop, update_progress)

def process_lots(page, auction_id, lots_url, lot_numbers, gui_callback, should_stop, update_progress):
    gui_callback('Importing Images and link...')
    last_successful_url = lots_url
    page.wait_for_timeout(2000)
    click_button(page, "a:has-text('BIDDING LIVE AT 702AUCTIONS.COM')")
    lot_count = 0

    for current_lot in lot_numbers:
        try:
            if should_stop.is_set():
                return

            fill_text_field(page, "#goToLot", current_lot, press_return=True)
            no_items_element = wait_and_find(page, "//div[@class='panel-body']//em[contains(text(), 'No items to display.')]")

            if "No items to display." in no_items_element.inner_text():
                click_button(page, "//button[contains(@class, 'btn') and contains(@class, 'af-action-button')]")

                lot_url = (
                    'https://bid.702auctions.com/Browse&utm_source=auction&utm_medium=itemlinkclick&utm_campaign=hibid'
                    if current_lot in [1, 2]
                    else f'https://bid.702auctions.com/Browse?CategoryID=9&StatusFilter=active_only&Lot%20Number_Min={current_lot}&Lot%20Number_Max={current_lot}&utm_source=auction&utm_medium=itemlinkclick&utm_campaign=hibid'
                )

                fill_text_field(page, "div.modal input#url", lot_url)
                fill_text_field(page, "div.modal input#description", "CLICK HERE VIEW AND BID ON THIS ITEM")
                click_button(page, "div.modal button[type='submit']")
                wait_for_element_invisibility(page, "div#auctionlink_modal.modal.in")
                page.wait_for_timeout(1000)
        except:
            gui_callback(f"Element not found for lot {current_lot}. Going to next lot")
            handle_url_check(page, 'lots/details', last_successful_url, gui_callback, should_stop)
            continue

        try:
            if is_div_empty(page):
                file_path = check_image(auction_id, current_lot, gui_callback)
                if file_path:
                    file_input = wait_and_find(page, "input[type='file'].dz-hidden-input")
                    file_input.set_input_files(file_path)
                    page.wait_for_selector(".dropzone.text-center", state="visible", timeout=10000)
                    page.wait_for_selector(".dropzone.text-center", state="hidden", timeout=30000)
        except Exception as e:
            gui_callback(f"Exception in image upload for lot {current_lot}: {e}")
            handle_url_check(page, 'lots/details', last_successful_url, gui_callback)
            continue

        last_successful_url = page.url
        page.wait_for_timeout(1000)
        lot_count += 1
        if lot_count % 10 == 0:
            gui_callback(f"Processed {lot_count} lots so far.")

    gui_callback(f"Total lots processed: {lot_count} of {len(lot_numbers)}")
    page.goto(lots_url)

def submit_auction(page, gui_callback):
    try:
        gui_callback("Attempting to find and click 'Upload Auction' button.")
        upload_button = page.wait_for_selector("button.af-page-header-button:has-text('UPLOAD AUCTION')", state="visible", timeout=30000)
        upload_button.click()
        gui_callback("'Upload Auction' button clicked.")
        page.wait_for_timeout(2000)

        gui_callback("Attempting to find and click the confirmation button in the popup.")
        confirm_button_selector = "button.btn.af-action-button:has-text('YES, QUEUE FOR UPLOAD')"
        
        for _ in range(5):  # Retry mechanism
            try:
                confirm_button = page.wait_for_selector(confirm_button_selector, state="visible", timeout=30000)
                confirm_button.scroll_into_view_if_needed()
                confirm_button.click()
                gui_callback("Confirmation button clicked.")
                page.wait_for_timeout(10000)
                gui_callback("Auction uploaded successfully.")
                return
            except:
                gui_callback("Retrying to click the confirmation button...")
            page.wait_for_timeout(2000)
        
        gui_callback("Failed to click the confirmation button after several attempts.")
    except Exception as e:
        gui_callback(f"Exception: {e}")
        save_screenshot(page, "exception_queue_for_upload")

def save_screenshot(page, name="screenshot.png"):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filepath = os.path.join(os.path.expanduser('~'), 'Downloads', f"{name}_{timestamp}.png")
    page.screenshot(path=filepath)
    print(f"Screenshot saved to {filepath}")

def format_ending_date(ending_date, gui_callback):
    gui_callback("Formatting ending date...")
    
    if isinstance(ending_date, str):
        try:
            date_object = datetime.strptime(ending_date, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            gui_callback(f"Error: Invalid date format. Expected 'YYYY-MM-DD HH:MM:SS', got '{ending_date}'")
            raise
    elif isinstance(ending_date, datetime):
        date_object = ending_date
    else:
        gui_callback(f"Error: Unexpected type for ending_date: {type(ending_date)}")
        raise ValueError(f"Unexpected type for ending_date: {type(ending_date)}")

    formatted_ending_date = date_object.strftime("%m/%d/%Y %I:%M %p")
    formatted_date_only = date_object.strftime("%m/%d/%Y")
    
    gui_callback(f"Formatted ending date: {formatted_ending_date}")
    gui_callback(f"Formatted date only: {formatted_date_only}")
    
    return formatted_ending_date, formatted_date_only

def run_upload_to_hibid(auction_id, ending_date, auction_title, gui_callback, should_stop, callback, selected_warehouse, update_progress):
    try:
        update_progress(5, "Starting the upload process...")

        username = config_manager.get_warehouse_var('hibid_user_name')
        password = config_manager.get_warehouse_var('hibid_password')

        if username is None or password is None:
            update_progress(10, "Error: Username or password is None. Please check your configuration.")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            page.goto(BASE_URL)
            update_progress(15, "Browser launched and navigated to base URL.")

            login_successful = login(page, username, password, gui_callback, should_stop)
            
            if not login_successful:
                update_progress(20, "Login failed. Stopping the process.")
                return

            if should_stop.is_set():
                update_progress(25, "Process stopped by user after login.")
                return

            input_csv_path = get_resource_path('processed_csv', f'{auction_id}.csv')
            update_progress(30, f"Input CSV Path: {input_csv_path}")

            todays_date = datetime.now().strftime("%m/%d/%Y")

            formatted_ending_date, formatted_date_only = format_ending_date(ending_date, gui_callback)
            update_progress(35, f"Formatted ending date: {formatted_ending_date}, formatted date only: {formatted_date_only}")

            if should_stop.is_set():
                update_progress(40, "Process stopped by user before CSV transformation.")
                return

            number_of_lots, auction_id, transformed_csv_path, lot_number_list = transform_csv_with_fixed_lines(input_csv_path)
            update_progress(45, f"Number of lots: {number_of_lots}, auction_id: {auction_id}, transformed_csv_path: {transformed_csv_path}")

            details_page(page, auction_title, auction_id, formatted_date_only, number_of_lots, formatted_ending_date, gui_callback, selected_warehouse, update_progress)
            update_progress(55, "Details page completed.")

            if should_stop.is_set():
                update_progress(60, "Process stopped by user before settings page.")
                return

            hibiduploadsettings_page(page, formatted_ending_date, todays_date, gui_callback, selected_warehouse)
            update_progress(65, "Settings page completed.")

            if should_stop.is_set():
                update_progress(70, "Process stopped by user before lots page.")
                return

            lots_page(page, transformed_csv_path, auction_id, lot_number_list, gui_callback, should_stop, update_progress)
            update_progress(85, "Lots page completed.")

            if should_stop.is_set():
                update_progress(90, "Process stopped by user before submitting auction.")
                return

            submit_auction(page, gui_callback)
            update_progress(95, "Auction submitted successfully.")

    except Exception as e:
        update_progress(98, f"Unexpected error: {e}")
        logger.error(f"Unexpected error: {e}")
        logger.exception("Traceback:")
    finally:
        if 'browser' in locals():
            update_progress(99, "Closing the browser...")
            browser.close()
        update_progress(100, "Upload process completed.")
        callback()