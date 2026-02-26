import asyncio
import logging
from main import login_to_website, get_config
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Discover")

async def discover():
    async with async_playwright() as p:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800}, user_agent=user_agent)
        page = await context.new_page()
        
        success, msg = await login_to_website(page)
        if not success:
            logger.error(f"Login failed: {msg}")
            await browser.close()
            return

        # 1. Go to landing page
        await page.goto("https://book.everyoneactive.com/connect/landing.aspx", wait_until="networkidle")
        logger.info(f"At landing: {page.url}")
        await page.screenshot(path="discover_landing.png")
        
        # 2. Look for "Make a Booking" or similar
        content = await page.content()
        if "MemberBooking" in content or "mrmmemberbooking.aspx" in content:
            logger.info("Found reference to member booking.")
            await page.goto("https://book.everyoneactive.com/Connect/mrmmemberbooking.aspx", wait_until="networkidle")
        else:
            # Try to find any booking link
            booking_link = page.locator("a:has-text('Booking'), a:has-text('Make a Booking')").first
            if await booking_link.is_visible():
                await booking_link.click()
                await page.wait_for_load_state("networkidle")
        
        logger.info(f"Final URL: {page.url}")
        await page.screenshot(path="discover_final.png")
        
        # List all IDs on the page to find the dropdowns
        ids = await page.evaluate("() => Array.from(document.querySelectorAll('[id]')).map(el => el.id)")
        logger.info(f"Found {len(ids)} elements with IDs. Some IDs: {ids[:20]}")
        
        # Check for our specific selectors
        for sel in ["SitesAdvanced", "ActivityGroups", "Activities", "startDate"]:
            matching = [i for i in ids if sel in i]
            logger.info(f"IDs matching '{sel}': {matching}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(discover())
