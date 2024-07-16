import time
from datetime import datetime
import pandas as pd
import os
import re
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.support.ui import Select
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from webdriver_manager.firefox import GeckoDriverManager
from auction.utils import config_manager

# Load configurations from the config_manager
USER_NAME = config_manager.get_global_var('hibid_user_name')
PASSWORD = config_manager.get_global_var('hibid_password')
BASE_URL = "https://www.auctionflex360.com/#/organization/5676/auctions/new"

def upload_to_hibid_main(auction_id, ending_date, auction_title, gui_callback, should_stop, callback, show_browser, selected_warehouse):
    run_upload_to_hibid(auction_id, ending_date, auction_title, gui_callback, should_stop, callback, show_browser, USER_NAME, PASSWORD, selected_warehouse)

if __name__ == "__main__":
    upload_to_hibid_main("sample_auction_id", "2023-12-31 18:30:00", "Sample Auction Title", print, lambda: False, lambda: print("Callback"), 1, "Maule Warehouse")

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

def configure_driver(url, show_browser):
    firefox_options = FirefoxOptions()
    firefox_options.set_preference("browser.download.folderList", 2)
    firefox_options.set_preference("browser.download.manager.showWhenStarting", False)
    firefox_options.set_preference("browser.download.dir", get_resources_dir('downloads'))
    firefox_options.set_preference("browser.helperApps.neverAsk.saveToDisk", "image/jpeg")

    if show_browser == 0:
        firefox_options.add_argument("--headless")
        firefox_options.add_argument("--window-size=1920x1080")

    driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=firefox_options)
    driver.get(url)
    return driver

def login(driver, username, password, gui_callback, should_stop):
    try:
        driver.get("https://www.auctionflex360.com/#/login")
        gui_callback("Page loaded.")
        print("Page loaded.")

        # Wait for the email field to be present
        email_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        gui_callback("Email field found.")
        print("Email field found.")
        
        # Enter the email
        email_field.clear()
        email_field.send_keys(username)
        gui_callback(f"Email entered: {username}")
        print(f"Email entered: {username}")

        # Wait for the password field to be present
        password_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "password"))
        )
        gui_callback("Password field found.")
        print("Password field found.")

        # Enter the password
        password_field.clear()
        password_field.send_keys(password)
        gui_callback("Password entered.")
        print("Password entered.")

        # Wait for the preloader to disappear
        WebDriverWait(driver, 15).until(
            EC.invisibility_of_element_located((By.CLASS_NAME, "preloader"))
        )
        gui_callback("Preloader disappeared.")
        print("Preloader disappeared.")

        # Wait for the login button to be clickable and click it
        login_button = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//input[@type='submit'][@value='Log In']"))
        )
        login_button.click()
        gui_callback("Login button clicked.")
        print("Login button clicked.")

        # Wait for some time to allow login to process
        time.sleep(5)

        # Check for any validation messages
        validation_error = driver.execute_script("return document.querySelector('.login-error')?.innerText")
        if validation_error:
            gui_callback(f"Login failed with validation error: {validation_error}")
            print(f"Login failed with validation error: {validation_error}")
            return False

        # Check if login was successful by examining the current URL
        current_url = driver.current_url
        gui_callback(f"Current URL after login check: {current_url}")
        print(f"Current URL after login check: {current_url}")
        if "/organization/" in current_url:
            return True
        else:
            gui_callback("Login failed or redirection issue.")
            print("Login failed or redirection issue.")
            return False

    except TimeoutException:
        gui_callback("Login fields or button not found within the timeout period.")
        print("Login fields or button not found within the timeout period.")
        return False
    except Exception as e:
        gui_callback(f"Login Exception: {e}")
        print(f"Login Exception: {e}")
        return False

def select_dropdown_options(driver, mappings, gui_callback):
    for label_text, value in mappings.items():
        try:
            xpath = f"//label[contains(text(), '{label_text}')]/following-sibling::div/select"
            select_element = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, xpath)))
            select = Select(select_element)
            select.select_by_value(value)
        except TimeoutException:
            gui_callback(f"Dropdown for '{label_text}' not found or not clickable.")
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

    transformed_df = pd.DataFrame()
    transformed_df['Lot Number'] = input_df['LotNumber']
    transformed_df['Seller Code'] = 1234
    transformed_df['Description'] = input_df['Description']
    transformed_df['Quantity'] = 1
    transformed_df['Start Bid Each'] = 5
    transformed_df['Sale Order'] = input_df.index + 3
    transformed_df['Title'] = input_df['Title']

    final_df = pd.concat([fixed_lines_df, transformed_df], ignore_index=True)
    lot_number_list = list(final_df['Lot Number'])

    if len(lot_number_list) == 0:
        print("Lot number list is empty.")
        return None, None, None, None

    output_csv_filename = get_resources_dir('hibid_csv') + f'/{auction_id}_hibid.csv'
    transformed_csv_path = os.path.abspath(output_csv_filename)
    final_df.to_csv(transformed_csv_path, index=False)

    print(f"Transformed CSV saved to {transformed_csv_path}")

    return len(input_df), auction_id, transformed_csv_path, lot_number_list

def is_div_empty(driver):
    try:
        div_element = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".image-list.list-group")))
        images = div_element.find_elements(By.TAG_NAME, "img")
        return len(images) == 0
    except TimeoutException:
        return True
    except StaleElementReferenceException:
        return is_div_empty(driver)

def wait_and_find(driver, by, identifier, duration=15):
    return WebDriverWait(driver, duration).until(EC.presence_of_element_located((by, identifier)))

def fill_text_field(driver, by, identifier, text, press_return=False):
    element = wait_and_find(driver, by, identifier)
    element.clear()
    element.send_keys(str(text))
    if press_return:
        element.send_keys(Keys.RETURN)

def fill_file_input(driver, by, identifier, file_path):
    element = wait_and_find(driver, by, identifier)
    driver.execute_script("arguments[0].style.display = 'block';", element)
    element.send_keys(file_path)

def click_button(driver, by, identifier):
    button = wait_and_find(driver, by, identifier)
    driver.execute_script("arguments[0].click();", button)

def wait_for_element_invisibility(driver, by, locator, timeout=30):
    WebDriverWait(driver, timeout).until(EC.invisibility_of_element((by, locator)))

def click_off(driver):
    time.sleep(1)
    driver.find_element(By.TAG_NAME, "body").click()
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
    base_file_path = os.path.join(get_resources_dir('hibid_images'), folder, f'{current_lot}_1')

    for ext in possible_extensions:
        file_path = base_file_path + ext
        if os.path.exists(file_path):
            return file_path

    gui_callback(f'Image for lot {current_lot} not found(function).')
    return None

def handle_url_check(driver, page, fallback_url, gui_callback, should_stop):
    current_url = driver.current_url
    if should_stop.is_set():
        return
    if 'login' in current_url:
        gui_callback("Detected login page. Re-logging in.")
        login(driver, USER_NAME, PASSWORD, gui_callback, should_stop)
        time.sleep(2)
        if 'organization' not in current_url:
            try:
                select_org_button = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//button[contains(@class, 'af-view-button') and contains(@title, 'Select Organization')]"))
                )
                gui_callback("Select Organization button found. Clicking it.")
                select_org_button.click()
            except TimeoutException:
                gui_callback("Select Organization button not found after login.")
        gui_callback("Navigating back to the last known good URL.")
        driver.get(fallback_url)
        time.sleep(2)
    else:
        gui_callback("Current URL seems fine. Continuing with the process.")

def details_page(driver, auction_title, auction_id, formatted_date_only, number_of_lots, formatted_ending_date, gui_callback, selected_warehouse):
    try:
        driver.get(BASE_URL)
        time.sleep(5)
        gui_callback('Loading Details page...')
        auction_link_url = f'https://bid.702auctions.com/Event/Details/{auction_id}?utm_source=auction&utm_medium=linkclick&utm_campaign=hibid'
        browse_link_url = f'https://bid.702auctions.com/Browse?utm_source=browse_all&utm_medium=linkclick&utm_campaign=hibid'
        file_path_702_logo = get_resources_dir('bid_stock_photo\\702_logo.png')

        fill_text_field(driver, By.ID, "name", auction_title)
        time.sleep(1)
        ending_date_field = wait_and_find(driver, By.NAME, "newAuctionEndDate")
        driver.execute_script("arguments[0].removeAttribute('readonly')", ending_date_field)
        ending_date_field.clear()
        ending_date_field.send_keys(formatted_date_only)

        click_button(driver, By.XPATH, "//button[.//i[contains(@class,'fa-floppy-o')]]")
        time.sleep(1)
        fill_text_field(driver, By.ID, "auctionCode", auction_id)
        formatted_description = description(number_of_lots, formatted_ending_date, selected_warehouse)
        fill_text_field(driver, By.XPATH, "//div[@class='form-group']/div/textarea[@id='description']", formatted_description)
        
        if selected_warehouse == "Maule Warehouse":
            fill_text_field(driver, By.ID, "address1", "1889 E. Maule Ave")
            fill_text_field(driver, By.ID, "address2", "Suite F")
            fill_text_field(driver, By.ID, "city", "Las Vegas")
            fill_text_field(driver, By.ID, "state", "NV")
            fill_text_field(driver, By.ID, "zip", "89119")
        elif selected_warehouse == "Sunrise Warehouse":
            fill_text_field(driver, By.ID, "address1", "3201 Sunrise Ave")
            fill_text_field(driver, By.ID, "address2", "")
            fill_text_field(driver, By.ID, "city", "Las Vegas")
            fill_text_field(driver, By.ID, "state", "NV")
            fill_text_field(driver, By.ID, "zip", "89101")

        click_button(driver, By.XPATH, "//button[contains(.,'NEW LINK')]")
        fill_text_field(driver, By.XPATH, "//div[contains(@class, 'modal')]//input[@id='link']", auction_link_url)
        fill_text_field(driver, By.XPATH, "//div[contains(@class, 'modal')]//input[@id='description'][@name='description']", "CLICK HERE TO REGISTER AND BID")
        click_button(driver, By.XPATH, "//div[contains(@class, 'modal')]//button[@type='submit']")
        wait_for_element_invisibility(driver, By.XPATH, "//div[@id='auctionlink_modal'][contains(@class, 'modal in')]")
        time.sleep(2)

        click_button(driver, By.XPATH, "//button[contains(.,'NEW LINK')]")
        fill_text_field(driver, By.XPATH, "//div[contains(@class, 'modal')]//input[@id='link']", browse_link_url)
        fill_text_field(driver, By.XPATH, "//div[contains(@class, 'modal')]//input[@id='description'][@name='description']", "CLICK HERE TO VIEW ALL AUCTIONS")
        click_button(driver, By.XPATH, "//div[contains(@class, 'modal')]//button[@type='submit']")
        wait_for_element_invisibility(driver, By.XPATH, "//div[@id='auctionlink_modal'][contains(@class, 'modal in')]")
        time.sleep(2)

        file_input = wait_and_find(driver, By.CSS_SELECTOR, "input[type='file'].dz-hidden-input")
        file_input.send_keys(file_path_702_logo)

        try:
            wait_for_element_invisibility(driver, By.CSS_SELECTOR, "div.vue-loading-msg")
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(2)
            save_button = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'SAVE')]")))
            save_button.click()
            time.sleep(2)
            driver.refresh()
        except Exception as e:
            gui_callback(f"Error saving details: {e}")
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(2)
            save_button = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'SAVE')]")))
            save_button.click()
            time.sleep(2)
            driver.refresh()
    except Exception as e:
        gui_callback(f"Error in details_page: {e}")

def hibiduploadsettings_page(driver, ending_date, todays_date, gui_callback, selected_warehouse):
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

        details_url = driver.current_url
        hibiduploadsettings_url = details_url.replace('details', 'hibiduploadsettings')
        driver.get(hibiduploadsettings_url)
        time.sleep(2)

        fill_text_field(driver, By.ID, "auction-date-times", auction_date_times_text)
        click_off(driver)
        fill_text_field(driver, By.ID, "payment-information", payment_information_text)
        click_off(driver)
        fill_text_field(driver, By.ID, "shipping-pick-up-information", shipping_pick_up_information_text)
        click_off(driver)
        fill_text_field(driver, By.ID, "bidding-notice", bidding_notice_text)
        no_registration_radio_button = wait_and_find(driver, By.ID, "noRegistration")
        driver.execute_script("arguments[0].click();", no_registration_radio_button)
        driver.execute_script(timezone_script)
        fill_text_field(driver, By.ID, "soft-close-seconds", "15")
        click_off(driver)
        fill_text_field(driver, By.ID, "close-bidding", ending_date)
        click_off(driver)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
        click_button(driver, By.XPATH, "//button[contains(.,'SAVE')]")
        time.sleep(3)
    except Exception as e:
        gui_callback(f"Error in hibiduploadsettings_page: {e}")

def click_import_lots_button(driver):
    try:
        button = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'btn') and contains(@class, 'af-page-header-button') and contains(text(), 'IMPORT LOTS')]")))
        driver.execute_script("arguments[0].click();", button)
    except TimeoutException:
        print(f"TimeoutException: Could not find or click the 'Import Lots' button")
    except Exception as e:
        print(f"Exception: {e}")

def save_screenshot(driver, name="screenshot.png"):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filepath = os.path.join(os.path.expanduser('~'), 'Downloads', f"{name}_{timestamp}.png")
    driver.save_screenshot(filepath)
    print(f"Screenshot saved to {filepath}")

def wait_for_element_to_be_clickable(driver, by, locator, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, locator)))

def lots_page(driver, transformed_csv_path, auction_id, lot_numbers_list, gui_callback, should_stop):
    gui_callback('Loading Lots page...')
    hibiduploadsettings_url = driver.current_url
    lots_url = hibiduploadsettings_url.replace('hibiduploadsettings', 'lots')
    driver.get(lots_url)
    time.sleep(2)
    gui_callback('Importing CSV...')

    try:
        click_import_lots_button(driver)
    except Exception as e:
        print(f"Error clicking 'Import Lots' button: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    try:
        print(transformed_csv_path)
        fill_file_input(driver, By.ID, "files", os.path.abspath(transformed_csv_path))
    except Exception as e:
        print(f"Error filling 'files' field: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    time.sleep(2)

    try:
        click_button(driver, By.XPATH, "//div[contains(@class, 'modal-buttons')]//button[@id='submit']")
    except Exception as e:
        print(f"Error clicking 'submit' button: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    time.sleep(2)

    try:
        select_dropdown_options(driver, mappings, gui_callback)
    except Exception as e:
        print(f"Error selecting dropdown options: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    time.sleep(2)

    try:
        click_button(driver, By.ID, "import")
    except Exception as e:
        print(f"Error clicking 'import' button: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    try:
        click_button(driver, By.XPATH, "//button[contains(text(), 'IMPORT')]")
    except Exception as e:
        print(f"Error clicking 'IMPORT' button: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    stop_import_xpath = "//button[@type='button' and contains(@class, 'btn') and contains(@class, 'af-action-button') and .//span[contains(text(), 'STOP IMPORT')]]"

    try:
        wait_for_element_invisibility(driver, By.XPATH, stop_import_xpath, timeout=90)
        print("Stop Import has disappeared")
    except Exception as e:
        print(f"Error waiting for 'Stop Import' text to disappear: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    time.sleep(1)
    close_button_css_selector = 'button[data-dismiss="modal"].btn.af-cancel-button'

    # Debugging: Check if the CLOSE button is present
    try:
        close_button = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, close_button_css_selector)))
        print("CLOSE button is present")
    except TimeoutException:
        print("TimeoutException: CLOSE button is not present")
        save_screenshot(driver, "close_button_not_present")
        return
    
    # Scroll into view and click using JavaScript
    try:
        driver.execute_script("arguments[0].scrollIntoView(true);", close_button)
        driver.execute_script("arguments[0].click();", close_button)
        print("CLOSE button force-clicked using JavaScript")
    except Exception as js_click_exception:
        print(f"Exception during force-click using JavaScript: {js_click_exception}")
        save_screenshot(driver, "js_click_exception_close_button")

    try:
        click_button(driver, By.XPATH, '//*[@id="lotTable_1"]/thead/tr/th[3]')
    except Exception as e:
        print(f"Error clicking 'sort by lot order' button: {e}")
        driver.save_screenshot("error_screenshot.png")
        return

    process_lots(driver, auction_id, lots_url, lot_numbers_list, gui_callback, should_stop)

def process_lots(driver, auction_id, lots_url, lot_numbers, gui_callback, should_stop):
    gui_callback('Importing Images and link...')
    last_successful_url = lots_url
    time.sleep(2)
    click_button(driver, By.LINK_TEXT, "BIDDING LIVE AT 702AUCTIONS.COM")
    lot_count = 0

    for current_lot in lot_numbers:
        try:
            if should_stop.is_set():
                return

            fill_text_field(driver, By.ID, "goToLot", current_lot, press_return=True)
            no_items_element = wait_and_find(driver, By.XPATH, '/html/body/div[1]/div[3]/div[2]/aside/section/div/section[2]/form/div[1]/div[3]/div[1]/div[2]/div[2]/div[3]/div[2]/div/div/table/tbody/tr/td/em')

            if "No items to display." in no_items_element.text:
                click_button(driver, By.XPATH, '//*[@id="app"]/div[3]/div[2]/aside/section/div/section[2]/form/div[1]/div[3]/div[1]/div[2]/div[2]/div[3]/div[2]/div/div/button')

                lot_url = (
                    'https://bid.702auctions.com/Browse&utm_source=auction&utm_medium=itemlinkclick&utm_campaign=hibid'
                    if current_lot in [1, 2]
                    else f'https://bid.702auctions.com/Browse?CategoryID=9&StatusFilter=active_only&Lot%20Number_Min={current_lot}&Lot%20Number_Max={current_lot}&utm_source=auction&utm_medium=itemlinkclick&utm_campaign=hibid'
                )

                fill_text_field(driver, By.XPATH, "//div[contains(@class, 'modal')]//input[@id='url'][@name='url']", lot_url)
                fill_text_field(driver, By.XPATH, "//div[contains(@class, 'modal')]//input[@id='description'][@name='description']", "CLICK HERE VIEW AND BID ON THIS ITEM")
                click_button(driver, By.XPATH, "/html/body/div[1]/div[3]/div[2]/aside/section/div/section[2]/form/div[1]/div[3]/div[1]/div[2]/div[2]/div[1]/div/div/form/div[2]/button[1]")
                wait_for_element_invisibility(driver, By.XPATH, "//div[@id='auctionlink_modal'][contains(@class, 'modal in')]")
                time.sleep(1)
        except NoSuchElementException:
            gui_callback(f"Element not found for lot {current_lot}. Going to next lot")
            handle_url_check(driver, 'lots/details', last_successful_url, gui_callback, should_stop)
            continue

        except Exception as e:
            gui_callback(f"Exception in link handling for lot {current_lot}: {e}.  Going to next lot")
            handle_url_check(driver, 'lots/details', last_successful_url, gui_callback, should_stop)
            continue

        try:
            if is_div_empty(driver):
                file_path = check_image(auction_id, current_lot, gui_callback)
                if file_path:
                    file_input = wait_and_find(driver, By.CSS_SELECTOR, "input[type='file'].dz-hidden-input")
                    file_input.send_keys(file_path)
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".dropzone.text-center")))
                    WebDriverWait(driver, 30).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".dropzone.text-center")))
        except Exception as e:
            gui_callback(f"Exception in image upload for lot {current_lot}: {e}")
            handle_url_check(driver, 'lots/details', last_successful_url, gui_callback)
            continue

        last_successful_url = driver.current_url
        time.sleep(1)
        lot_count += 1
        if lot_count % 10 == 0:
            gui_callback(f"Processed {lot_count} lots so far.")

    gui_callback(f"Total lots processed: {lot_count} of {len(lot_numbers)}")
    driver.get(lots_url)

def submit_auction(driver, gui_callback):
    try:
        gui_callback("Attempting to find and click 'Upload Auction' button.")
        upload_button_xpath = '//button[contains(@class, "af-page-header-button") and .//span[text()="UPLOAD AUCTION"]]'
        upload_button = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, upload_button_xpath)))
        driver.execute_script("arguments[0].click();", upload_button)
        gui_callback("'Upload Auction' button clicked.")
        time.sleep(2)

        gui_callback("Attempting to find and click the confirmation button in the popup.")
        confirm_button_xpath = '//button[contains(@class, "btn af-action-button") and contains(text(), "YES, QUEUE FOR UPLOAD")]'
        
        for _ in range(5):  # Retry mechanism
            try:
                confirm_button = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.XPATH, confirm_button_xpath)))
                driver.execute_script("arguments[0].scrollIntoView(true);", confirm_button)
                driver.execute_script("arguments[0].click();", confirm_button)
                gui_callback("Confirmation button clicked.")
                time.sleep(10)
                gui_callback("Auction uploaded successfully.")
                return
            except TimeoutException:
                gui_callback("Retrying to click the confirmation button...")
            except Exception as e:
                gui_callback(f"Exception: {e}")
                save_screenshot(driver, "exception_queue_for_upload")
                return
            time.sleep(2)
        
        gui_callback("Failed to click the confirmation button after several attempts.")
    except TimeoutException:
        gui_callback("TimeoutException: Could not find or click the 'Upload Auction' or confirmation button.")
    except NoSuchElementException:
        gui_callback("NoSuchElementException: The 'Upload Auction' or confirmation button was not found.")
    except Exception as e:
        gui_callback(f"Exception: {e}")

def save_screenshot(driver, name="screenshot.png"):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filepath = os.path.join(os.path.expanduser('~'), 'Downloads', f"{name}_{timestamp}.png")
    driver.save_screenshot(filepath)
    print(f"Screenshot saved to {filepath}")

def format_ending_date(ending_date, gui_callback):
    try:
        date_object = datetime.strptime(ending_date, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        gui_callback("The provided date string does not match the expected format.")
        return None, None

    date_with_fixed_time = date_object.replace(hour=18, minute=30)

    if date_with_fixed_time < datetime.now():
        gui_callback("The date must be after today's date.")
        return None, None

    formatted_ending_date = date_with_fixed_time.strftime('%m/%d/%Y %I:%M %p')
    formatted_date_only = date_with_fixed_time.strftime('%m/%d/%Y')

    return formatted_ending_date, formatted_date_only

from selenium.common.exceptions import WebDriverException

def run_upload_to_hibid(auction_id, ending_date, auction_title, gui_callback, should_stop, callback, show_browser, username, password, selected_warehouse):
    driver = None
    try:
        if should_stop.is_set():
            return

        input_csv_path = os.path.join(os.path.expanduser('~'), 'Downloads', f'{auction_id}.csv')
        gui_callback(f"Input CSV Path: {input_csv_path}")
        print(f"Input CSV Path: {input_csv_path}")
        todays_date = datetime.now().strftime("%m/%d/%Y")

        formatted_ending_date, formatted_date_only = format_ending_date(ending_date, gui_callback)
        print(f"Formatted ending date: {formatted_ending_date}, formatted date only: {formatted_date_only}")

        if should_stop.is_set():
            return

        number_of_lots, auction_id, transformed_csv_path, lot_number_list = transform_csv_with_fixed_lines(input_csv_path)
        print(f"Number of lots: {number_of_lots}, auction_id: {auction_id}, transformed_csv_path: {transformed_csv_path}")

        if number_of_lots is None or auction_id is None or transformed_csv_path is None or lot_number_list is None:
            gui_callback("Error transforming CSV.")
            return

        driver = configure_driver(BASE_URL, show_browser)
        login_successful = login(driver, username, password, gui_callback, should_stop)

        if should_stop.is_set() or not login_successful:
            return
        
        # Add a check right after the login
        current_url = driver.current_url
        print(f"Current URL after login check: {current_url}")
        gui_callback(f"Current URL after login check: {current_url}")

        details_page(driver, auction_title, auction_id, formatted_date_only, number_of_lots, formatted_ending_date, gui_callback, selected_warehouse)
        time.sleep(2)

        if should_stop.is_set():
            return

        hibiduploadsettings_page(driver, formatted_ending_date, todays_date, gui_callback, selected_warehouse)
        time.sleep(2)

        if should_stop.is_set():
            return

        lots_page(driver, transformed_csv_path, auction_id, lot_number_list, gui_callback, should_stop)
        time.sleep(2)

        if should_stop.is_set():
            return

        submit_auction(driver, gui_callback)
    except WebDriverException as e:
        gui_callback(f"Selenium WebDriver error: {e}")
        print(f"Selenium WebDriver error: {e}")
    except Exception as e:
        gui_callback(f"Error: {e}")
        print(f"Error: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                gui_callback("Error closing the Selenium WebDriver.")
                print("Error closing the Selenium WebDriver.")
        callback()
