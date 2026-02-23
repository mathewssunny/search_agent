from main import login_to_website, get_config
from playwright.sync_api import sync_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LocalBooking")

def book_explicitly(target_date="24/02/2026", pref_time="21:30"):
    center = get_config("DEFAULT_CENTER", "Stevenage Arts & L C")
    activity_type = get_config("DEFAULT_ACTIVITY_TYPE", "Sports Hall")
    activity = get_config("DEFAULT_ACTIVITY", "Badminton (55 Min)")

    logger.info(f"Booking {activity} at {center} on {target_date}...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        success, msg = login_to_website(page)
        if not success:
            logger.error(msg)
            return

        page.goto("https://book.everyoneactive.com/connect/landing.aspx")
        page.wait_for_load_state("networkidle")
        
        try:
            if page.is_visible("#ctl00_MainContent__advanceSearchUserControl_specificDate"):
                page.fill("#ctl00_MainContent__advanceSearchUserControl_specificDate", target_date)
                page.evaluate(f"() => document.getElementById('ctl00_MainContent__advanceSearchUserControl_specificDate').dispatchEvent(new Event('change', {{bubbles: true}}))")
        except: pass

        try:
            adv_header = "h3:has-text('Advanced Search'), .collapsible-header:has-text('Advanced Search')"
            page.wait_for_selector(adv_header, timeout=10000)
            if not page.is_visible("#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced"):
                page.click(adv_header)
                page.wait_for_timeout(1000)
        except: pass

        if page.is_visible("#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced"):
            page.select_option("#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced", label=center)
        if page.is_visible("#ctl00_MainContent__advanceSearchUserControl_ActivityGroups"):
            page.select_option("#ctl00_MainContent__advanceSearchUserControl_ActivityGroups", label=activity_type)
        if page.is_visible("#ctl00_MainContent__advanceSearchUserControl_Activities"):
            page.select_option("#ctl00_MainContent__advanceSearchUserControl_Activities", label=activity)

        search_btn = "#ctl00_MainContent__advanceSearchUserControl__searchBtn"
        try:
            page.wait_for_selector(search_btn, timeout=10000)
            page.click(search_btn)
            page.wait_for_load_state("networkidle")
        except: pass

        space_btn = "a[id*='btnAvailability'], #ctl00_MainContent__advanceSearchResultsUserControl_Activities_ctrl0_btnAvailability_lg"
        try:
            page.wait_for_selector(space_btn, timeout=20000)
            page.evaluate(f"selector => {{ const el = document.querySelector(selector); if(el) el.click(); }}", space_btn)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(5000)
            page.wait_for_selector("#ctl00_MainContent_grdResourceView", timeout=30000)
        except: pass

        slots_data = page.evaluate("""() => {
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

        if not slots_data:
            logger.info("No slots found using javascript evaluation, reading fallbacks.")
            element_handles = page.query_selector_all("input.btn-custom-success, input.btn-resource-success, input.btn-success")
            slots_data = [{"time": s.get_attribute("value"), "court": "Unknown", "id": s.get_attribute("id")} for s in element_handles]

        logger.info(f"Available slots: {slots_data}")
        # Prioritize pref_time
        scored = []
        for s in slots_data:
            time_val = s.get('time', '')
            if time_val == pref_time:
                scored.append((1, s))
            elif abs(int(time_val.split(":")[0]) - int(pref_time.split(":")[0])) <= 1:
                scored.append((2, s))
        
        scored.sort(key=lambda x: x[0])
        
        if scored:
            best = scored[0][1]
            logger.info(f"Booking {best['time']} at {best['court']}...")
            page.click(f"#{best['id']}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)
            logger.info(f"Current URL after selecting slot: {page.url}")
            
            if "mrmConfirmBooking" in page.url:
               logger.info("Clicking actual confirm button to finish booking!")
               try:
                   page.click("input[name*='btnConfirm']")
                   page.wait_for_load_state("networkidle")
                   page.wait_for_timeout(2000)
                   logger.info(f"Final URL: {page.url}")
               except Exception as e:
                   logger.info(f"Could not submit final confirmation: {e}")
            else:
               logger.info("Did not reach confirm page.")
        else:
            logger.info("No matching slots found.")
        browser.close()

book_explicitly("24/02/2026", "21:30")
