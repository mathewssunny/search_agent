const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());
require('dotenv').config();

const config = {
    username: process.env.stev_smash_LOGIN_USERNAME,
    password: process.env.stev_smash_LOGIN_PASSWORD,
    url: process.env.stev_smash_LOGIN_URL || "https://book.everyoneactive.com/Connect/mrmlogin.aspx",
    center: process.env.stev_smash_DEFAULT_CENTER || "Stevenage Arts & L C",
    activity: "Badminton (55 Min)",
    targetDate: "27/02/2026",  // For testing; will use 7-day logic in cloud
    prefTime: process.env.stev_smash_PREF_SLOT || "19:30"
};

// Slot priority: 19:30 > 20:30 > 21:30, courts: Main Hall or Bowls Hall
const SLOT_PRIORITIES = [
    { time: "19:30", courts: ["Main Hall", "Bowls Hall"] },
    { time: "20:30", courts: ["Main Hall", "Bowls Hall"] },
    { time: "21:30", courts: ["Main Hall", "Bowls Hall"] },
];

function pickBestSlot(availableSlots) {
    for (const pref of SLOT_PRIORITIES) {
        for (const court of pref.courts) {
            const match = availableSlots.find(
                s => s.time.includes(pref.time) && s.court.includes(court)
            );
            if (match) {
                console.log(`✓ Best match: ${pref.time} on ${court} (ID: ${match.id})`);
                return match;
            }
        }
    }
    return null;
}

async function book() {
    console.log("=== Puppeteer Court Booking ===");
    console.log(`Slot priority: ${SLOT_PRIORITIES.map(p => p.time).join(" > ")}`);
    console.log(`Preferred courts: Main Hall, Bowls Hall`);
    console.log(`Date: ${config.targetDate}`);
    console.log("");

    const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
    });
    const page = (await browser.pages())[0];
    await page.setViewport({ width: 1280, height: 800 });

    try {
        // ========== STEP 1: LOGIN ==========
        console.log("[1/6] Logging in...");
        await page.goto(config.url, { waitUntil: 'networkidle2' });
        await page.waitForSelector('#ctl00_MainContent_InputLogin', { timeout: 10000 });
        await page.type('#ctl00_MainContent_InputLogin', config.username);
        await page.type('#ctl00_MainContent_InputPassword', config.password);
        await Promise.all([
            page.waitForNavigation({ waitUntil: 'networkidle2' }),
            page.click('#ctl00_MainContent_btnLogin')
        ]);
        console.log(`   Logged in → ${page.url()}`);

        // ========== STEP 2: WAIT FOR HOME PAGE TO ACTIVATE ==========
        console.log("[2/6] Waiting for home page to activate...");
        await new Promise(r => setTimeout(r, 10000));

        // ========== STEP 3: EXPAND ADVANCED SEARCH & FILL FILTERS ==========
        console.log("[3/6] Expanding Advanced Search...");
        await page.evaluate(() => {
            const el = Array.from(document.querySelectorAll('h1,h2,h3,h4,a,.collapsible-header,#headingAdvancedSearch,strong'))
                .find(e => e.innerText.includes('Advanced Search'));
            if (el) el.click();
        });
        await new Promise(r => setTimeout(r, 3000));

        // Select Center
        console.log(`   Center: ${config.center}`);
        await page.evaluate((center) => {
            const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced');
            const opt = Array.from(sel.options).find(o => o.text.includes(center));
            if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
        }, config.center);
        await new Promise(r => setTimeout(r, 2000));

        // Select Activity Group (Sports Hall)
        console.log("   Activity Group: Sports Hall");
        await page.evaluate(() => {
            const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_ActivityGroups');
            if (sel) {
                const opt = Array.from(sel.options).find(o => o.text.includes('Sports Hall'));
                if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
            }
        });
        await new Promise(r => setTimeout(r, 2000));

        // Select Activity (Badminton 55 Min)
        console.log(`   Activity: ${config.activity}`);
        await page.evaluate((activity) => {
            const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_Activities');
            if (sel) {
                const opt = Array.from(sel.options).find(o => o.text.includes(activity));
                if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
            }
        }, config.activity);
        await new Promise(r => setTimeout(r, 2000));

        // Set Date
        console.log(`   Date: ${config.targetDate}`);
        await page.evaluate((date) => {
            const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_startDate');
            if (sel) { sel.value = date; sel.dispatchEvent(new Event('change', { bubbles: true })); }
        }, config.targetDate);
        await new Promise(r => setTimeout(r, 1000));

        // ========== STEP 4: CLICK SEARCH & GET RESULTS ==========
        console.log("[4/6] Searching for availability...");
        await page.click('#ctl00_MainContent__advanceSearchUserControl__searchBtn');
        await new Promise(r => setTimeout(r, 10000));
        await page.screenshot({ path: 'after_search.png' });

        // Click Availability button
        const availBtn = await page.$('a[id*="btnAvailability"]');
        if (!availBtn) {
            console.log("✗ No availability button found. No results for this date/activity.");
            await page.screenshot({ path: 'no_results.png' });
            return;
        }
        console.log("   Clicking Availability...");
        await page.evaluate(() => {
            const btn = document.querySelector('a[id*="btnAvailability"]');
            if (btn) btn.click();
        });
        // Wait for the postback navigation to complete
        await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => { });
        await new Promise(r => setTimeout(r, 5000));
        await page.screenshot({ path: 'availability_grid.png' });
        console.log("   Saved availability_grid.png");

        // ========== STEP 5: PARSE SLOTS & PICK BEST ==========
        console.log("[5/6] Parsing available slots...");
        const slots = await page.evaluate(() => {
            const grid = document.getElementById('ctl00_MainContent_grdResourceView');
            if (!grid) return [];

            // Get court names from headers
            const headerRow = grid.querySelector('tr');
            const headers = headerRow
                ? Array.from(headerRow.querySelectorAll('th, td')).map(h => h.innerText.trim())
                : [];

            const results = [];
            const rows = Array.from(grid.querySelectorAll('tr')).slice(1); // skip header row
            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll('td'));
                for (let i = 0; i < cells.length; i++) {
                    const btns = cells[i].querySelectorAll('input.btn-success, input.btn-custom-success, input[type="submit"]');
                    for (const btn of btns) {
                        const val = btn.value || btn.getAttribute('value') || '';
                        if (val.match(/\d{2}:\d{2}/)) {
                            results.push({
                                time: val.trim(),
                                court: headers[i] || `Court ${i}`,
                                id: btn.id,
                                name: btn.name
                            });
                        }
                    }
                }
            }
            return results;
        });

        if (slots.length === 0) {
            console.log("✗ No bookable slots found in the grid.");
            await page.screenshot({ path: 'empty_grid.png' });
            return;
        }

        console.log(`   Found ${slots.length} available slot(s):`);
        slots.forEach(s => console.log(`   - ${s.time} | ${s.court} | ${s.id}`));

        const bestSlot = pickBestSlot(slots);
        if (!bestSlot) {
            console.log("✗ None of the preferred slots (19:30/20:30/21:30 Main Hall/Bowls Hall) are available.");
            return;
        }

        // ========== STEP 6: BOOK THE SLOT ==========
        console.log(`[6/6] Booking: ${bestSlot.time} on ${bestSlot.court}...`);

        // Click the green slot button (btn-success / btn-custom-success)
        console.log("   Clicking green slot button...");
        await page.evaluate((slotName, slotId) => {
            let btn = slotName ? document.querySelector(`input[name="${slotName}"]`) : null;
            if (!btn && slotId) btn = document.getElementById(slotId);
            if (btn) btn.click();
        }, bestSlot.name, bestSlot.id);

        // Green click triggers a postback navigation to mrmConfirmBooking.aspx
        await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => { });
        await new Promise(r => setTimeout(r, 3000));
        await page.screenshot({ path: 'after_green_click.png' });
        console.log(`   After green click → ${page.url()}`);
        console.log("   Saved after_green_click.png");

        // Click the red Book button on the confirm page
        console.log("   Looking for red Book button...");
        const redBookBtn = await page.evaluate(() => {
            // Try btn-danger first
            const dangerBtns = Array.from(document.querySelectorAll('input.btn-danger, button.btn-danger, a.btn-danger'));
            if (dangerBtns.length > 0) {
                dangerBtns[0].click();
                return dangerBtns[0].value || dangerBtns[0].innerText || 'clicked';
            }
            // Fallback: any button with value "Book"
            const allBtns = Array.from(document.querySelectorAll('input[type="submit"], button'));
            const bookBtn = allBtns.find(b => (b.value || b.innerText || '').trim() === 'Book');
            if (bookBtn) {
                bookBtn.click();
                return bookBtn.value || bookBtn.innerText || 'clicked';
            }
            return null;
        });

        if (redBookBtn) {
            console.log(`   ✅ Clicked red Book button: "${redBookBtn}"`);
            await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => { });
            await new Promise(r => setTimeout(r, 5000));
        } else {
            console.log("   ⚠ Red Book button not found. Dumping page buttons...");
            const buttons = await page.evaluate(() => {
                return Array.from(document.querySelectorAll('input[type="submit"], button, a.btn'))
                    .map(b => ({ tag: b.tagName, type: b.type, value: b.value, text: b.innerText?.trim(), class: b.className }));
            });
            console.log("   Buttons on page: " + JSON.stringify(buttons));
        }

        await page.screenshot({ path: 'booking_final.png' });
        const finalBody = await page.evaluate(() => document.body.innerText);

        if (finalBody.toLowerCase().includes('confirmed') || finalBody.toLowerCase().includes('booked') || finalBody.toLowerCase().includes('successful')) {
            console.log("🎉 BOOKING CONFIRMED!");
        } else {
            console.log("⚠ Booking status unclear. Check booking_final.png");
        }

        console.log(`   Final URL: ${page.url()}`);

    } catch (e) {
        console.error(`Error: ${e.message}`);
        await page.screenshot({ path: 'booking_error.png' });
    } finally {
        await browser.close();
        console.log("Browser closed.");
    }
}

book();
