import os
import logging
import sys
import time
from datetime import datetime, timedelta
from google.adk.agents import LlmAgent
from google.adk.runners import Runner, InMemorySessionService
from google.genai import types
from playwright.async_api import async_playwright
import asyncio
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

from google.cloud import secretmanager
import google.auth

from flask import Flask, request, jsonify, render_template
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log")
    ]
)
logger = logging.getLogger("BookingAgent")

_secrets_cache = {}
_sm_client = None
_gcp_project = None

def get_config(key, default=None):
    global _sm_client, _gcp_project
    if key in _secrets_cache:
        return _secrets_cache[key]
    
    try:
        # Initialize client lazily
        if not _sm_client:
            _, _gcp_project = google.auth.default()
            if _gcp_project:
                _sm_client = secretmanager.SecretManagerServiceClient()
                
        if _sm_client and _gcp_project:
            secret_name = f"projects/{_gcp_project}/secrets/stev_smash_{key}/versions/latest"
            response = _sm_client.access_secret_version(request={"name": secret_name})
            val = response.payload.data.decode("UTF-8")
            _secrets_cache[key] = val
            return val
    except Exception as e:
        logger.debug(f"Could not fetch {key} from Secret Manager: {e}")
        
    val = os.getenv(key, default)
    _secrets_cache[key] = val
    return val

async def login_to_website(page, url: str = None, username: str = None, password: str = None):
    """
    Automates logging into Everyone Active.
    """
    url = url or get_config("LOGIN_URL")
    username = username or get_config("LOGIN_USERNAME")
    password = password or get_config("LOGIN_PASSWORD")

    if not url:
        logger.error("No LOGIN_URL provided.")
        return False, "Error: No URL provided for login."

    logger.info(f"Navigating to login page: {url}")
    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        logger.info(f"Page loaded. Title: '{await page.title()}'")
        
        # Handle cookie banner
        try:
            cookie_button = page.locator("button:has-text('Allow all')")
            if await cookie_button.is_visible(timeout=5000):
                await cookie_button.click()
                logger.info("Cookie banner dismissed.")
        except Exception as e:
            logger.debug(f"Could not dismiss cookie banner (might not be present): {e}")

        # Fill login form
        email_field = page.locator("#emailAddress")
        password_field = page.locator("#password")
        login_btn = page.locator("button.primary-button")

        if await email_field.is_visible(timeout=10000):
            logger.info(f"Filling login form for user: {username}")
            await email_field.fill(username)
            await password_field.fill(password)
            await login_btn.click()
            
            logger.info("Login form submitted. Waiting for navigation...")
            await page.wait_for_load_state("networkidle", timeout=60000)
            
            # Verify login success
            current_url = page.url.lower()
            if "login" not in current_url or await page.locator("a:has-text('Log out')").is_visible(timeout=5000):
                logger.info(f"Login successful. Current URL: {page.url}")
                return True, "Login successful."
            else:
                logger.warning(f"Login might have failed. Current URL: {page.url}")
                return False, f"Login failed. Still on: {page.url}"
        else:
            logger.error("Login fields not found on the page.")
            return False, "Login fields not found."
    except Exception as e:
        logger.error(f"Error during login: {str(e)}", exc_info=True)
        return False, f"Login error: {str(e)}"

async def jump_to_portal(page):
    """Transition from account.everyoneactive.com to book.everyoneactive.com."""
    logger.info("Navigating to booking portal landing page...")
    try:
        await page.goto("https://book.everyoneactive.com/connect/landing.aspx", wait_until="networkidle", timeout=60000)
        
        # Check for SSO button or Login button
        for _ in range(2):
            login_btn = page.locator("a:has-text('Login'), a:has-text('Log in'), button:has-text('Login'), button:has-text('Log in'), input[value='Login'], input[value='Log in']").first
            if await login_btn.is_visible(timeout=5000):
                await login_btn.click()
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)
            else:
                break
        
        # Fallback: manual credentials on portal login page
        if "mrmlogin.aspx" in page.url.lower():
            logger.info("Stuck on portal login page. Filling credentials...")
            user = get_config("LOGIN_USERNAME")
            pw = get_config("LOGIN_PASSWORD")
            if user and pw:
                email_field = page.locator("input[placeholder*='Email'], #txtEmail, #EmailAddress").first
                pw_field = page.locator("input[placeholder*='Password'], #txtPassword").first
                submit_btn = page.locator("button:has-text('Login'), input[value='Login'], #btnLogin").first
                
                if await email_field.is_visible(timeout=10000):
                    await email_field.fill(user)
                    await pw_field.fill(pw)
                    await submit_btn.click()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)
        
        logger.info(f"Portal transition finished. Current URL: {page.url}")
        return True
    except Exception as e:
        logger.error(f"Portal transition failed: {e}")
        return False

async def _do_book_activity(target_date: str = None, target_time: str = None):
    center = get_config("DEFAULT_CENTER", "Stevenage Arts & L C")
    activity_type = get_config("DEFAULT_ACTIVITY_TYPE", "Sports Hall")
    activity = get_config("DEFAULT_ACTIVITY", "Badminton (55 Min)")

    logger.info(f"Starting booking for {activity} at {center} on {target_date or 'today'} {f'at {target_time}' if target_time else ''}")
    
    try:
        async with async_playwright() as p:
            logger.info("Launching browser...")
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1280, 'height': 800})
            page = await context.new_page()
            
            # 1. Login
            success, msg = await login_to_website(page)
            if not success:
                logger.error(f"Stopping automation: {msg}")
                await browser.close()
                return msg

            # 2. Navigate to Booking Section
            await jump_to_portal(page)
            await page.screenshot(path="portal_ready.png")

            booking_page = page

            # 3. Search and Selection
            logger.info("Selecting filters...")
            
            # IDs from inspection
            start_date_id = "#ctl00_MainContent__advanceSearchUserControl_startDate"
            end_date_id = "#ctl00_MainContent__advanceSearchUserControl_endDate"
            sites_selector = "#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced"
            group_selector = "#ctl00_MainContent__advanceSearchUserControl_ActivityGroups"
            act_selector = "#ctl00_MainContent__advanceSearchUserControl_Activities"
            
            try:
                # 3a. Expand Advanced Search
                # Try multiple headers - in some versions it's an accordion or a div
                adv_header_selector = "h3:has-text('Advanced Search'), .accordion-header:has-text('Advanced Search'), #headingAdvancedSearch, .collapsible-header:has-text('Advanced Search')"
                
                if not await booking_page.locator(sites_selector).is_visible(timeout=5000):
                    logger.info("Expanding 'Advanced Search'...")
                    expand_btn = booking_page.locator(adv_header_selector).first
                    if await expand_btn.is_visible():
                        await expand_btn.click()
                        await asyncio.sleep(2)
                    else:
                        # Fallback: find by text content
                        await booking_page.evaluate("""() => {
                            const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, div, span'));
                            const adv = headers.find(h => h.innerText && h.innerText.includes('Advanced Search'));
                            if (adv) adv.click();
                        }""")
                        await asyncio.sleep(2)

                # Wait for any form element
                await booking_page.locator(sites_selector).wait_for(state="visible", timeout=10000)
                
                # Fill Dates
                if target_date:
                    # EA portal may expect YYYY-MM-DD for <input type="date"> 
                    # Our target_date is DD/MM/YYYY.
                    for selector in [start_date_id, end_date_id]:
                        field = booking_page.locator(selector)
                        if await field.is_visible(timeout=3000):
                            # Check input type (case-insensitive)
                            input_type = (await field.get_attribute("type") or "").lower()
                            value_to_fill = target_date
                            
                            if input_type == "date":
                                try:
                                    # Convert DD/MM/YYYY to YYYY-MM-DD
                                    parts = target_date.split("/")
                                    if len(parts) == 3:
                                        value_to_fill = f"{parts[2]}-{parts[1]}-{parts[0]}"
                                except Exception as de:
                                    logger.error(f"Date conversion error: {de}")
                            
                            logger.info(f"Filling {selector} (type={input_type}) with {value_to_fill}")
                            await field.fill(value_to_fill)
                            await booking_page.evaluate(f"sel => {{ const el = document.querySelector(sel); if(el) el.dispatchEvent(new Event('change', {{bubbles: true}})); }}", selector)
                            await asyncio.sleep(1) # Wait for potential postback
                
                # Apply Dropdowns
                logger.info(f"Setting center: {center}")
                await booking_page.select_option(sites_selector, label=center)
                await booking_page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)

                logger.info(f"Setting activity type: {activity_type}")
                await booking_page.select_option(group_selector, label=activity_type)
                await booking_page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)

                logger.info(f"Setting activity: {activity}")
                await booking_page.select_option(act_selector, label=activity)
                await booking_page.wait_for_load_state("networkidle")
                
                await booking_page.screenshot(path="filters_applied.png")
            except Exception as e:
                logger.error(f"Error applying filters: {e}")
                await booking_page.screenshot(path="filter_error.png")

            # 4. Search
            logger.info("Triggering search...")
            search_btn_selector = "#ctl00_MainContent__advanceSearchUserControl__searchBtn"
            try:
                search_btn = booking_page.locator(search_btn_selector)
                await search_btn.click()
                logger.info("Search clicked. Waiting for results to stabilize...")
                
                # Wait for the "Loading..." state to finish
                await asyncio.sleep(2)
                try:
                    # Wait for button to NOT be in loading state or just wait for network idle
                    await booking_page.wait_for_function(f"() => !document.querySelector('{search_btn_selector}') || !document.querySelector('{search_btn_selector}').innerText.includes('Loading')", timeout=30000)
                except:
                    logger.debug("Timeout waiting for loading text to disappear, continuing...")
                
                await booking_page.wait_for_load_state("networkidle", timeout=60000)
                await booking_page.screenshot(path="search_performed.png")
            except Exception as e:
                logger.error(f"Search failed: {e}")
            
            # 5. Availability
            logger.info("Checking availability results...")
            # Use :visible to avoid hidden mobile/responsive elements
            space_button_selector = "[id*='btnAvailability']:visible, a:has-text('Space'):visible, a:has-text('Full'):visible, a.availabilitybutton:visible"
            
            try:
                # Wait for at least one availability button to appear
                await booking_page.locator(space_button_selector).first.wait_for(state="visible", timeout=20000)
                
                space_btn = booking_page.locator(space_button_selector).first
                btn_text = (await space_btn.inner_text()).strip()
                logger.info(f"Availability button found with text: '{btn_text}'")
                
                if btn_text.upper() in ["FULL", "FULLY BOOKED"] and "Space" not in btn_text:
                    logger.warning("Activity is marked as FULL.")
                
                logger.info("Clicking activity button to view time slots...")
                await space_btn.click()
                await booking_page.wait_for_load_state("networkidle")
                await asyncio.sleep(5) 
            except Exception as e:
                logger.error(f"Availability detection/click failed: {e}")
                await booking_page.screenshot(path="availability_error.png")
                await browser.close()
                return f"Error checking availability: {e}"

            # 6. Grid
            grid_selector = "#ctl00_MainContent_grdResourceView"
            try:
                await booking_page.wait_for_selector(grid_selector, timeout=20000)
                logger.info("Time-slot grid loaded.")
            except Exception as e:
                logger.error(f"Grid timeout: {e}")
                await booking_page.screenshot(path="grid_timeout.png")
                await browser.close()
                return "Time-slot grid failed to load."

            # Extraction
            available_slots_data = await booking_page.evaluate("""() => {
                const grid = document.getElementById('ctl00_MainContent_grdResourceView');
                if (!grid) return [];
                const results = [];
                const buttons = Array.from(grid.querySelectorAll('input.btn-custom-success, input.btn-resource-success, input.btn-success'));
                
                // Try to find column headers (usually court names)
                let headers = Array.from(grid.querySelectorAll('.resourceViewHeader, th, .header')).map(h => h.innerText.trim());
                if (headers.length === 0) {
                    const firstRow = grid.querySelector('tr');
                    if (firstRow) headers = Array.from(firstRow.querySelectorAll('td, th')).map(h => h.innerText.trim());
                }

                buttons.forEach(btn => {
                    const cell = btn.closest('td');
                    if (!cell) return;
                    const columnIndex = Array.from(cell.parentElement.children).indexOf(cell);
                    results.push({
                        time: btn.value.trim(),
                        court: headers[columnIndex] || "Unknown Court",
                        id: btn.id
                    });
                });
                return results;
            }""")
            
            logger.info(f"Found {len(available_slots_data)} available slots.")
            if not available_slots_data:
                logger.warning("No slots parsed. Checking raw elements...")
                slots = await booking_page.query_selector_all("input.btn-custom-success, input.btn-resource-success, input.btn-success")
                for s in slots:
                    available_slots_data.append({
                        "time": (await s.get_attribute("value")).strip(),
                        "court": "Unknown Court",
                        "id": await s.get_attribute("id")
                    })
                logger.info(f"Found {len(available_slots_data)} slots via raw element query.")

            if not available_slots_data:
                await browser.close()
                return f"No slots available for {target_date or 'today'}."

            # Scoring/Prioritization
            def get_score(slot):
                try:
                    time_str = slot.get('time', '')
                    court = slot.get('court', '')
                    is_pref_hall = "main" in court.lower() or "hall" in court.lower() or "badminton" in court.lower()
                    
                    if target_time:
                        # Exact match
                        if time_str == target_time: return 1 if is_pref_hall else 2
                        # 1 hour window
                        try:
                            s_hour = int(time_str.split(":")[0])
                            t_hour = int(target_time.split(":")[0])
                            if abs(s_hour - t_hour) <= 1: return 3 if is_pref_hall else 4
                        except: pass
                        return 999

                    # Morning/Evening preferences
                    if time_str in ["19:30", "20:30"]: return 1 if is_pref_hall else 2
                    if time_str == "18:30": return 3 if is_pref_hall else 4
                    if time_str == "21:30": return 5 if is_pref_hall else 6
                    return 999
                except: return 999

            scored = sorted([(get_score(s), s) for s in available_slots_data if get_score(s) < 10], key=lambda x: x[0])
            
            if not scored:
                logger.warning("No slots matched preference criteria.")
                await browser.close()
                return "No available slots matched your time preference."

            best_score, best_slot = scored[0]
            logger.info(f"Selected Best Match: {best_slot['time']} at {best_slot['court']} (Priority Score: {best_score})")
            
            # Click and select
            try:
                target_selector = f"#{best_slot['id']}" if best_slot.get('id') else f"input[value='{best_slot['time']}']"
                logger.info(f"Interacting with slot via selector: {target_selector}")
                
                # Check if we can find it
                slot_btn = booking_page.locator(target_selector).first
                if await slot_btn.is_visible():
                    await slot_btn.click()
                else:
                    logger.warning("Selector not found, trying click via evaluate...")
                    await booking_page.evaluate("""({tid, time}) => {
                        const btn = document.getElementById(tid) || Array.from(document.querySelectorAll('input')).find(i => i.value && i.value.includes(time));
                        if (btn) btn.click();
                    }""", {"tid": best_slot.get('id', ''), "time": best_slot['time']})

                logger.info("Selecting slot... waiting for redirect.")
                await booking_page.wait_for_load_state("networkidle", timeout=30000)
                await asyncio.sleep(3) 
                
                final_url = booking_page.url
                await booking_page.screenshot(path="after_slot_selection.png")
                
                if "Confirm" in final_url or "Basket" in final_url or "confirm" in final_url.lower() or "basket" in final_url.lower():
                    logger.info("Reached checkout/confirmation page. Looking for final 'Book' button...")
                    
                    # Final "Book" button on mrmConfirmBooking.aspx
                    # We use a broad set of selectors to ensure we catch the red button
                    final_book_selectors = [
                        "#ctl00_MainContent_btnBasket",
                        "#ctl00_MainContent_btnBook",
                        "a:has-text('Book')", 
                        "button:has-text('Book')", 
                        ".btn-danger:has-text('Book')", 
                        "input[value='Book']",
                        "input[value='Confirm']",
                        "a.btn-danger",
                        "button.btn-danger"
                    ]
                    
                    logger.info("Waiting for final 'Book' button to be stable...")
                    await asyncio.sleep(2) # Give page a moment to settle
                    
                    final_book_btn = None
                    for selector in final_book_selectors:
                        btn = booking_page.locator(selector).first
                        if await btn.is_visible(timeout=2000):
                            final_book_btn = btn
                            logger.info(f"Found final 'Book' button with selector: {selector}")
                            break
                    
                    if final_book_btn:
                        logger.info("Clicking final 'Book' button...")
                        # Try regular click first
                        try:
                            await final_book_btn.click(timeout=5000)
                        except Exception as ce:
                            logger.warning(f"Regular click failed, trying JS click: {ce}")
                            await booking_page.evaluate("selector => document.querySelector(selector).click()", await final_book_btn.evaluate("el => el.id") or "a:contains('Book')")
                        
                        await booking_page.wait_for_load_state("networkidle", timeout=60000)
                        # Wait for a success indicator or simply a transition
                        await asyncio.sleep(5)
                        await booking_page.screenshot(path="post_final_click.png")
                    else:
                        logger.warning("Final 'Book' button not found. Checking if already confirmed...")
                    
                    # 7. Verification (Manage Bookings)
                    logger.info("Verifying booking in Manage Bookings...")
                    
                    # Be extra persistent with Manage Bookings
                    for attempt in range(2):
                        try:
                            # Re-ensure login if we've been redirected or if the session is stale
                            current_url = booking_page.url.lower()
                            if "login" in current_url or "landing" in current_url or "error" in current_url:
                                logger.info(f"Session issue detected (URL: {booking_page.url}). Re-logging in...")
                                await jump_to_portal(booking_page)
                            
                            # Navigate to Manage Bookings
                            manage_url = "https://book.everyoneactive.com/Connect/mrmMemberBookings.aspx"
                            await booking_page.goto(manage_url, wait_until="networkidle")
                            await asyncio.sleep(3)
                            
                            if "mrmMemberBookings.aspx" not in booking_page.url:
                                logger.warning(f"Failed to reach Manage Bookings, at {booking_page.url}. Trying alternate navigation...")
                                manage_lnk = booking_page.locator("a:has-text('Manage Bookings'), #navManageBookings").first
                                if await manage_lnk.is_visible(timeout=5000):
                                    await manage_lnk.click()
                                    await booking_page.wait_for_load_state("networkidle")
                            
                            await booking_page.screenshot(path=f"manage_bookings_attempt_{attempt}.png")
                            content = await booking_page.content()
                            
                            if best_slot['time'] in content or "Thank you" in content:
                                result = f"Confirmed! Found booking for {best_slot['time']} on {target_date or 'today'}."
                                logger.info(result)
                                break
                            else:
                                # Sometimes it's successful but Manage Bookings is slow
                                if "mrmBookingConfirmed.aspx" in booking_page.url or "Thank you for your booking" in content:
                                    result = f"Confirmed! Transition to confirmation page detected for {best_slot['time']}."
                                    logger.info(result)
                                    break
                                
                                if attempt == 0:
                                    logger.info("Slot not found on first attempt, trying one re-login/refresh...")
                                    continue
                                result = f"Booking for {best_slot['time']} not found in Manage Bookings after verification attempts. Please check manually."
                        except Exception as ve:
                            logger.error(f"Verification attempt {attempt} failed: {ve}")
                            if attempt == 1:
                                result = f"Verification failed after 2 attempts: {ve}"
                    
                    logger.info(f"Final result: {result}")
                else:
                    result = f"Slot {best_slot['time']} clicked, but final state unclear. Current URL: {final_url}"
                    logger.warning(result)
            except Exception as e:
                logger.error(f"Error while selecting/finalizing slot: {e}")
                result = f"Error during final booking steps: {e}"
            
            await browser.close()
            return result
    except Exception as e:
        logger.error(f"Fatal error in _do_book_activity: {str(e)}", exc_info=True)
        return f"Automation error: {str(e)}"

def book_activity_task(target_date: str = None, target_time: str = None):
    """
    Automates the booking flow. If target_date is None, books for today.
    target_date should be in DD/MM/YYYY format (e.g. '23/02/2026').
    target_time should be in HH:MM format (e.g. '21:30').
    """
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        if loop.is_running():
            # If the loop is already running (within ADK), use a thread to wait for it
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _do_book_activity(target_date, target_time))
                return future.result()
        else:
            return loop.run_until_complete(_do_book_activity(target_date, target_time))
    except Exception as e:
        logger.error(f"Booking automation failed: {e}", exc_info=True)
        return f"Error during booking: {e}"

def _setup_vertex_ai():
    """Configure environment for Vertex AI backend instead of Gemini API (AI Studio)."""
    # Tell google-genai / ADK to use Vertex AI backend
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

    # Set project and location from Secret Manager / env, with sensible defaults
    project = get_config("GOOGLE_CLOUD_PROJECT") or get_config("GCP_PROJECT")
    if not project:
        # Fallback: read from google.auth.default()
        try:
            _, project = google.auth.default()
        except Exception:
            pass
    if project:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
        logger.info(f"Vertex AI project: {project}")

    location = get_config("GOOGLE_CLOUD_LOCATION", "us-central1")
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", location)
    logger.info(f"Vertex AI location: {location}")

def create_agent():
    """Initializes the Google ADK Booking Agent."""
    try:
        logger.info("Initializing Agent...")

        # Configure Vertex AI backend (uses ADC for auth – no API key needed)
        _setup_vertex_ai()

        agent = LlmAgent(
            name="Booking_Agent",
            instruction=(
                "You are an automation assistant for Everyone Active bookings. "
                "Use the book_activity_task tool to navigate the Everyone Active site and set up a booking. "
                "The tool accepts a target_date argument in DD/MM/YYYY format if the user specifies a date, "
                "and a target_time argument in HH:MM format if the user specifies a time. "
                "By default, you should look for Badminton at Stevenage Arts & LC under Sports Hall. "
                "Report back the final status of the search or booking."
            ),
            tools=[book_activity_task],
            model="gemini-2.0-flash"
        )

        logger.info("Agent initialization successful.")
        return agent
    except Exception as e:
        logger.error(f"Failed to initialize agent: {str(e)}", exc_info=True)
        raise

def run_agent(query):
    """Runs the agent for a given query."""
    try:
        agent = create_agent()
        session_service = InMemorySessionService()
        runner = Runner(
            agent=agent, 
            app_name="BookingApp", 
            session_service=session_service,
            auto_create_session=True
        )
        
        logger.info(f"Processing query: {query}")
        
        # Format query as types.Content
        new_message = types.Content(
            role="user",
            parts=[types.Part(text=query)]
        )
        
        # Run the agent (it's a generator)
        events = runner.run(
            user_id="user_123",
            session_id="session_123",
            new_message=new_message
        )
        
        final_response = "No response from agent."
        for event in events:
            logger.debug(f"Agent event: {event}")
            # The author can be 'model' or 'agent' depending on the stage
            if event.content:
                # Extract text parts
                parts = []
                for p in event.content.parts:
                    if hasattr(p, 'text') and p.text:
                        parts.append(p.text)
                
                if parts:
                    final_response = " ".join(parts)
                    logger.info(f"Model response part: {final_response}")
        
        logger.info(f"Final response: {final_response}")
        return final_response
    except Exception as e:
        logger.error(f"Error during agent execution: {str(e)}", exc_info=True)
        return f"An error occurred: {str(e)}"

def scheduled_task():
    """Daily task runs at midnight to catch newly opened slots (typically 7 days ahead)."""
    logger.info("Starting scheduled daily task at midnight...")
    
    # Wait a few seconds to ensure the website system clock has rolled over
    time.sleep(10)
    
    # Calculate target date: N days from now in UK time
    days_ahead = int(get_config("BOOKING_DAYS_AHEAD", 7))
    uk_tz = pytz.timezone('Europe/London')
    now_uk = datetime.now(uk_tz)
    target_date_obj = now_uk + timedelta(days=days_ahead)
    target_date_str = target_date_obj.strftime("%d/%m/%Y")
    
    activity = get_config("DEFAULT_ACTIVITY", "Badminton (55 Min)")
    pref_slot = get_config("PREF_SLOT", "21:30")
    
    logger.info(f"Targeting booking for {target_date_str} ({days_ahead} days ahead) at {pref_slot}...")
    
    query = f"Book {activity} for {target_date_str}. Prefer the {pref_slot} slot, but allow 1 hour flexibility before/after if unavailable."
    response = run_agent(query)
    logger.info(f"Scheduled task result for {target_date_str}: {response}")

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "agent": "Booking_Agent"}), 200

@app.route('/query', methods=['POST'])
def handle_query():
    data = request.json
    query = data.get('query')
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    response = run_agent(query)
    return jsonify({"response": response})

@app.route('/scheduled_task', methods=['POST'])
def trigger_scheduled_task():
    # This can be called by Cloud Scheduler
    scheduled_task()
    return jsonify({"status": "Scheduled task triggered"}), 200

if __name__ == "__main__":
    # Vertex AI uses ADC – just verify project is discoverable
    _setup_vertex_ai()

    timezone = get_config("TZ", "Europe/London")
    try:
        tz = pytz.timezone(timezone)
    except Exception:
        tz = pytz.timezone("Europe/London")
        
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(scheduled_task, 'cron', hour=0, minute=0)
    scheduler.start()
    logger.info(f"Scheduler started. Task scheduled for 00:00 daily (timezone: {tz}).")

    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
