import os
import logging
import sys
import time
from datetime import datetime, timedelta
from google.adk.agents import LlmAgent
from google.adk.tools import google_search
from google.adk.runners import Runner, InMemorySessionService
from google.genai import types
from playwright.sync_api import sync_playwright
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

from google.cloud import secretmanager
import google.auth

from flask import Flask, request, jsonify
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

def login_to_website(page, url: str = None, username: str = None, password: str = None):
    """
    Automates logging into Everyone Active.
    """
    url = url or get_config("LOGIN_URL")
    username = username or get_config("LOGIN_USERNAME")
    password = password or get_config("LOGIN_PASSWORD")

    if not url:
        return False, "Error: No URL provided for login."

    logger.info(f"Attempting to login to {url}...")
    try:
        page.goto(url)
        logger.info(f"Page loaded: {page.title()}")
        
        # Handle cookie banner
        try:
            if page.is_visible("button:has-text('Allow all')"):
                page.click("button:has-text('Allow all')")
                logger.info("Cookie banner dismissed.")
        except Exception:
            pass

        # Fill login form
        if page.is_visible("#emailAddress"):
            page.fill("#emailAddress", username)
            page.fill("#password", password)
            page.click("button.primary-button")
            page.wait_for_load_state("networkidle")
            
            # Verify login success
            if "login" not in page.url.lower() or page.is_visible("a:has-text('Log out')"):
                logger.info(f"Login successful. Current URL: {page.url}")
                return True, "Login successful."
            else:
                return False, f"Login failed. Still on: {page.url}"
        else:
            return False, "Login fields not found."
    except Exception as e:
        logger.error(f"Login error: {e}")
        return False, str(e)

def book_activity_task(target_date: str = None, target_time: str = None):
    """
    Automates the booking flow. If target_date is None, books for today.
    target_date should be in DD/MM/YYYY format (e.g. '23/02/2026').
    target_time should be in HH:MM format (e.g. '21:30').
    """
    center = get_config("DEFAULT_CENTER", "Stevenage Arts & L C")
    activity_type = get_config("DEFAULT_ACTIVITY_TYPE", "Sports Hall")
    activity = get_config("DEFAULT_ACTIVITY", "Badminton (55 Min)")

    logger.info(f"Starting Booking automation for {activity} at {center} on {target_date or 'today'}...")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            # 1. Login
            success, msg = login_to_website(page)
            if not success:
                browser.close()
                return msg

            # 2. Navigate to Booking Section
            logger.info("Opening booking portal...")
            # We skip the intermediate page and go directly to landing
            page.goto("https://book.everyoneactive.com/connect/landing.aspx")
            page.wait_for_load_state("networkidle")
            booking_page = page

            # 3. Search and Selection
            logger.info("Handling search filters...")
            
            # Primary goal: Set the date. There's a date input in the top search box
            specific_date_selector = "#ctl00_MainContent__advanceSearchUserControl_specificDate"
            try:
                if booking_page.is_visible(specific_date_selector):
                    logger.info(f"Setting date in main search bar: {target_date or 'today'}")
                    if target_date:
                        booking_page.fill(specific_date_selector, target_date)
                        booking_page.evaluate(f"() => document.getElementById('ctl00_MainContent__advanceSearchUserControl_specificDate').dispatchEvent(new Event('change', {{bubbles: true}}))")
            except Exception as e:
                logger.warning(f"Could not set date in main search bar: {e}")

            # Expand Advanced Search if needed for center/activity selection
            logger.info("Looking for Advanced Search expander...")
            adv_header_selector = "h3:has-text('Advanced Search'), .collapsible-header:has-text('Advanced Search')"
            sites_selector = "#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced"
            
            try:
                # Wait for any advanced search indicator
                booking_page.wait_for_selector(adv_header_selector, timeout=10000)
                if not booking_page.is_visible(sites_selector):
                    logger.info("Expanding Advanced Search...")
                    # Click the chevron or the header
                    booking_page.click(adv_header_selector)
                    booking_page.wait_for_timeout(1000) 
            except Exception as e:
                logger.warning(f"Advanced Search expansion issue: {e}")

            # Fill filters (Center, Activity Type, Activity)
            logger.info(f"Setting filters: {center}, {activity_type}, {activity}")
            try:
                # Center
                if booking_page.is_visible(sites_selector):
                    booking_page.select_option(sites_selector, label=center)
                    booking_page.wait_for_load_state("networkidle")

                # Activity Type
                group_selector = "#ctl00_MainContent__advanceSearchUserControl_ActivityGroups"
                if booking_page.is_visible(group_selector):
                    booking_page.select_option(group_selector, label=activity_type)
                    booking_page.wait_for_load_state("networkidle")

                # Activity
                act_selector = "#ctl00_MainContent__advanceSearchUserControl_Activities"
                if booking_page.is_visible(act_selector):
                    booking_page.select_option(act_selector, label=activity)
                    booking_page.wait_for_load_state("networkidle")
            except Exception as e:
                logger.error(f"Error setting filters: {e}")

            # 4. Click Search
            logger.info("Clicking Search...")
            search_btn = "#ctl00_MainContent__advanceSearchUserControl__searchBtn"
            try:
                booking_page.wait_for_selector(search_btn, timeout=10000)
                booking_page.click(search_btn)
                booking_page.wait_for_load_state("networkidle")
            except:
                logger.warning("Search button click failed or not found.")
            
            # 5. Click 'Space' button to see the grid
            logger.info("Looking for 'Space' (Availability) button...")
            space_button_selector = "a[id*='btnAvailability'], #ctl00_MainContent__advanceSearchResultsUserControl_Activities_ctrl0_btnAvailability_lg"
            
            try:
                booking_page.wait_for_selector(space_button_selector, timeout=20000)
                logger.info("Clicking 'Space' button via JS...")
                booking_page.evaluate(f"selector => {{ const el = document.querySelector(selector); if(el) el.click(); }}", space_button_selector)
                
                # Wait for AJAX and grid
                booking_page.wait_for_load_state("networkidle")
                booking_page.wait_for_timeout(5000)
                
                grid_selector = "#ctl00_MainContent_grdResourceView"
                logger.info("Waiting for grid load...")
                booking_page.wait_for_selector(grid_selector, timeout=30000)
                logger.info("Grid loaded.")
            except Exception as e:
                logger.warning(f"Failed to reach grid: {e}")

            # 6. Select Time Slot with Prioritization
            logger.info("Analyzing available slots with prioritization...")
            available_slots_data = booking_page.evaluate("""() => {
                const grid = document.getElementById('ctl00_MainContent_grdResourceView');
                if (!grid) return [];
                const results = [];
                const buttons = Array.from(grid.querySelectorAll('input.btn-custom-success, input.btn-resource-success, input.btn-success'));
                let headers = Array.from(grid.querySelectorAll('.resourceViewHeader, th, .header')).map(h => h.innerText.trim());
                if (headers.length === 0) {
                    const firstRow = grid.querySelector('tr');
                    if (firstRow) headers = Array.from(firstRow.querySelectorAll('td, th')).map(h => h.innerText.trim());
                }
                buttons.forEach(btn => {
                    const cell = btn.closest('td');
                    const columnIndex = Array.from(cell.parentElement.children).indexOf(cell);
                    results.push({
                        time: btn.value,
                        court: headers[columnIndex] || "Unknown",
                        id: btn.id
                    });
                });
                return results;
            }""")
            
            if not available_slots_data:
                slots = booking_page.query_selector_all("input.btn-custom-success, input.btn-resource-success, input.btn-success")
                available_slots_data = [{"time": s.get_attribute("value"), "court": "Unknown", "id": s.get_attribute("id")} for s in slots]

            def get_score(slot):
                try:
                    time_str = slot.get('time', '')
                    court = slot.get('court', '')
                    is_pref_hall = any(x in court for x in ["Main Hall", "Badminton"])
                    
                    if target_time:
                        if time_str == target_time:
                            return 1 if is_pref_hall else 2
                        elif abs(int(time_str.split(":")[0]) - int(target_time.split(":")[0])) <= 1:
                            return 3 if is_pref_hall else 4
                        else:
                            return 999

                    # Tier 1: Top Preference (19:30 or 20:30)
                    if time_str in ["19:30", "20:30"]:
                        return 1 if is_pref_hall else 2
                    
                    # Tier 2: Secondary Preference (18:30)
                    if time_str == "18:30":
                        return 3 if is_pref_hall else 4
                    
                    # Tier 3: Least Preference (21:30)
                    if time_str == "21:30":
                        return 5 if is_pref_hall else 6
                        
                    return 999
                except: return 999

            scored = sorted([(get_score(s), s) for s in available_slots_data if get_score(s) < 10], key=lambda x: (x[0], x[1].get('time', '')))
            
            if scored:
                best_score, best_slot = scored[0]
                logger.info(f"Priority Match: {best_slot['time']} at {best_slot['court']} (Score: {best_score})")
                
                booking_page.click(f"#{best_slot['id']}")
                booking_page.wait_for_load_state("networkidle")
                booking_page.wait_for_timeout(3000)
                
                if "mrmConfirmBooking" in booking_page.url:
                    result = f"Successfully selected {best_slot['time']} at {best_slot['court']} for {target_date or 'today'}. Reached confirmation page."
                else:
                    result = f"Clicked slot {best_slot['time']} but URL is: {booking_page.url}"
            else:
                result = f"No suitable slots found in preferred ranges (18:30 - 21:30) on {target_date or 'today'}."
            
            browser.close()
            return result
    except Exception as e:
        logger.error(f"Booking automation failed: {e}")
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
            tools=[google_search, book_activity_task],
            model="gemini-3-flash-preview"
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
            # Look for the last model response content
            if event.content and event.author == "model":
                # Extract text parts
                # event.content is a types.Content object
                parts = [p.text for p in event.content.parts if p.text]
                if parts:
                    final_response = " ".join(parts)
        
        logger.info("Query processed successfully.")
        return final_response
    except Exception as e:
        logger.error(f"Error during agent execution: {str(e)}", exc_info=True)
        return f"An error occurred: {str(e)}"

def scheduled_task():
    """Daily task runs at midnight to catch newly opened slots (typically 7 days ahead)."""
    logger.info("Starting scheduled daily task at midnight...")
    
    # Calculate target date: 7 days from now in UK time
    uk_tz = pytz.timezone('Europe/London')
    now_uk = datetime.now(uk_tz)
    target_date_obj = now_uk + timedelta(days=7)
    target_date_str = target_date_obj.strftime("%d/%m/%Y")
    
    activity = get_config("DEFAULT_ACTIVITY", "Badminton (55 Min)")
    pref_slot = get_config("PREF_SLOT", "21:30")
    
    logger.info(f"Targeting booking for {target_date_str} at {pref_slot}...")
    
    query = f"Book {activity} for {target_date_str}. Prefer the {pref_slot} slot, but allow 1 hour flexibility before/after if unavailable."
    response = run_agent(query)
    logger.info(f"Scheduled task result for {target_date_str}: {response}")

app = Flask(__name__)

@app.route('/')
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
