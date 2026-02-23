import os
import logging
from playwright.sync_api import sync_playwright
from main import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestLogin")

def test_login():
    url = get_config("LOGIN_URL")
    username = get_config("LOGIN_USERNAME")
    password = get_config("LOGIN_PASSWORD")

    logger.info(f"Testing login for {url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto(url)
            logger.info(f"Page loaded: {page.title()}")
            
            # Handle cookie banner if it exists
            try:
                if page.is_visible("button:has-text('Allow all')"):
                    page.click("button:has-text('Allow all')")
                    logger.info("Cookie banner dismissed.")
            except:
                pass

            # Fill login form
            page.fill("#emailAddress", username)
            page.fill("#password", password)
            
            # Click login
            page.click("button.primary-button")
            
            # Wait for navigation or a specific element that appears after login
            page.wait_for_load_state("networkidle")
            
            # Check if login was successful
            # Common indicator: logout link or dashboard URL
            logger.info(f"Final URL: {page.url}")
            logger.info(f"Final Page Title: {page.title()}")
            
            if "login" not in page.url.lower() or page.query_selector("a:has-text('Log out')"):
                logger.info("Login seems successful!")
                return True
            else:
                logger.error("Login failed or stayed on login page.")
                page.screenshot(path="login_failure.png")
                return False
                
        except Exception as e:
            logger.error(f"Error during login: {e}")
            page.screenshot(path="login_error.png")
            return False
        finally:
            browser.close()

if __name__ == "__main__":
    success = test_login()
    if success:
        print("LOGIN_SUCCESS")
    else:
        print("LOGIN_FAILED")
