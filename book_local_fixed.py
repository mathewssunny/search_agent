import asyncio
import logging
from main import login_to_website, get_config
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LocalBooking")

async def book_explicitly_async(target_date="05/03/2026", pref_time="19:30"):
    center = get_config("DEFAULT_CENTER", "Stevenage Arts & L C")
    activity_type = get_config("DEFAULT_ACTIVITY_TYPE", "Sports Hall")
    activity = get_config("DEFAULT_ACTIVITY", "Badminton (55 Min)")

    logger.info(f"Booking {activity} at {center} on {target_date}...")
    
    async with async_playwright() as p:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=user_agent,
            extra_http_headers={
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            }
        )
        page = await context.new_page()
        
        success, msg = await login_to_website(page)
        if not success:
            logger.error(f"Login failed: {msg}")
            await browser.close()
            return

        # Navigate to home/landing
        await page.goto("https://book.everyoneactive.com/Connect/memberHomePage.aspx", wait_until="networkidle")
        logger.info(f"At home page: {await page.title()}")
        
        # Navigate to Search/Booking
        await page.goto("https://book.everyoneactive.com/Connect/mrmmemberbooking.aspx", wait_until="networkidle")
        
        try:
            # Expand Advanced Search if needed
            adv_header = "h3:has-text('Advanced Search'), .collapsible-header:has-text('Advanced Search')"
            if await page.locator(adv_header).is_visible():
                await page.click(adv_header)
                await asyncio.sleep(1)

            # Fill Details
            await page.select_option("#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced", label=center)
            await page.wait_for_load_state("networkidle")
            
            await page.select_option("#ctl00_MainContent__advanceSearchUserControl_ActivityGroups", label=activity_type)
            await page.wait_for_load_state("networkidle")

            await page.select_option("#ctl00_MainContent__advanceSearchUserControl_Activities", label=activity)
            await page.wait_for_load_state("networkidle")

            # Date
            date_field = "#ctl00_MainContent__advanceSearchUserControl_startDate"
            if await page.locator(date_field).is_visible():
                await page.fill(date_field, target_date)
                # Dispatch change
                await page.evaluate(f"() => {{ const el = document.querySelector('{date_field}'); if(el) el.dispatchEvent(new Event('change', {{bubbles: true}})); }}")

            # Search
            await page.click("#ctl00_MainContent__advanceSearchUserControl__searchBtn")
            await page.wait_for_load_state("networkidle")
            
            # Availability
            space_btn = "a[id*='btnAvailability']"
            await page.wait_for_selector(space_btn, timeout=20000)
            await page.locator(space_btn).first.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(5) # Wait for grid

            # Grid check
            slots_data = await page.evaluate("""() => {
                const grid = document.getElementById('ctl00_MainContent_grdResourceView');
                if (!grid) return [];
                const results = [];
                const buttons = Array.from(grid.querySelectorAll('input.btn-custom-success, input.btn-resource-success, input.btn-success'));
                let headers = Array.from(grid.querySelectorAll('.resourceViewHeader, th, .header')).map(h => h.innerText.trim());
                buttons.forEach(btn => {
                    const cell = btn.closest('td');
                    const columnIndex = Array.from(cell.parentElement.children).indexOf(cell);
                    results.push({ time: btn.value, court: headers[columnIndex] || "Unknown", id: btn.id });
                });
                return results;
            }""")

            logger.info(f"Available slots: {slots_data}")
            
            # Scorage/Pick
            scored = []
            for s in slots_data:
                if s['time'] == pref_time:
                    scored.append((1, s))
                elif abs(int(s['time'].split(":")[0]) - int(pref_time.split(":")[0])) <= 1:
                    scored.append((2, s))
            
            scored.sort(key=lambda x: x[0])
            
            if scored:
                best = scored[0][1]
                logger.info(f"Picking {best['time']} at {best['court']}...")
                await page.click(f"#{best['id']}")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)
                
                logger.info(f"Final URL before 'Book': {page.url}")
                await page.screenshot(path="local_booking_final.png")
            else:
                logger.warning("No suitable slots found.")
                
        except Exception as e:
            logger.error(f"Error during booking steps: {e}")
            await page.screenshot(path="local_error.png")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(book_explicitly_async())
