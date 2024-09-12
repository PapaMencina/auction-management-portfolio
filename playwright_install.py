import os
from playwright.sync_api import sync_playwright

def install_browsers():
    if os.path.exists('/.playwright_needs_install'):
        print("Installing Playwright browsers...")
        with sync_playwright() as p:
            for browser_type in [p.chromium, p.firefox, p.webkit]:
                browser_type.install()
        os.remove('/.playwright_needs_install')
        print("Playwright browsers installed successfully.")
    else:
        print("Playwright browsers already installed.")

if __name__ == "__main__":
    install_browsers()
