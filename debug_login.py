import asyncio
import logging
from main import login_to_website, get_config
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("DebugLogin")

async def test_login():
    async with async_playwright() as p:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=user_agent
        )
        page = await context.new_page()
        
        url = "https://account.everyoneactive.com/login/"
        user = get_config("LOGIN_USERNAME")
        # We don't print the actual password for safety
        logger.info(f"Testing login for {user} at {url}")
        
        success, msg = await login_to_website(page)
        
        await page.screenshot(path="login_debug_result.png")
        logger.info(f"Login result: {success}, Message: {msg}")
        logger.info(f"Final URL: {page.url}")
        
        content = await page.content()
        if "Invalid" in content or "incorrect" in content:
            logger.error("Page contains error messages indicating bad credentials.")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_login())
