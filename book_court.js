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
    targetDate: "05/03/2026",
    prefTime: "19:30"
};

async function book() {
    console.log(`Starting Puppeteer booking with Home Page Delay...`);
    const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
    });
    const page = (await browser.pages())[0];
    await page.setViewport({ width: 1280, height: 800 });

    try {
        console.log(`Navigating to ${config.url}...`);
        await page.goto(config.url, { waitUntil: 'networkidle2' });

        // Login Selectors
        const emailSelector = '#ctl00_MainContent_InputLogin';
        const passSelector = '#ctl00_MainContent_InputPassword';
        const loginBtnSelector = '#ctl00_MainContent_btnLogin';

        console.log("Filling login form...");
        await page.waitForSelector(emailSelector, { timeout: 10000 });
        await page.type(emailSelector, config.username);
        await page.type(passSelector, config.password);

        console.log("Submitting login...");
        await Promise.all([
            page.waitForNavigation({ waitUntil: 'networkidle2' }),
            page.click(loginBtnSelector)
        ]);

        console.log(`Logged in. Current URL: ${page.url()}`);

        // RELEVANT PART: Stay on home page and wait
        console.log("Waiting 10 seconds for Advanced Search to activate...");
        await new Promise(r => setTimeout(r, 10000));

        await page.screenshot({ path: 'home_after_delay.png' });
        console.log("Saved home_after_delay.png");

        // Try to expand Advanced Search if it's there
        console.log("Looking for Advanced Search header...");
        const clicked = await page.evaluate(() => {
            const elements = Array.from(document.querySelectorAll('h1, h2, h3, h4, a, .collapsible-header, #headingAdvancedSearch, strong'));
            const header = elements.find(el => el.innerText.includes('Advanced Search'));
            if (header) {
                header.click();
                return true;
            }
            return false;
        });

        if (clicked) {
            console.log("Clicked Advanced Search header.");
            await new Promise(r => setTimeout(r, 3000));
        }

        const sitesSelector = '#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced';
        const sitesDropdown = await page.$(sitesSelector);

        if (sitesDropdown) {
            const isVisible = await page.evaluate(el => {
                const style = window.getComputedStyle(el);
                return style && style.display !== 'none' && style.visibility !== 'hidden' && el.offsetWidth > 0;
            }, sitesDropdown);
            console.log(`Sites dropdown found. Visible: ${isVisible}`);

            if (isVisible) {
                console.log("Attempting to fill filters...");
                await page.evaluate((center) => {
                    const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_SitesAdvanced');
                    const opt = Array.from(sel.options).find(o => o.text.includes(center));
                    if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
                }, config.center);
                await new Promise(r => setTimeout(r, 2000));

                console.log("Selecting Activity Group (Sports Hall)...");
                await page.evaluate(() => {
                    const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_ActivityGroups');
                    if (sel) {
                        const opt = Array.from(sel.options).find(o => o.text.includes('Sports Hall'));
                        if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
                    }
                });
                await new Promise(r => setTimeout(r, 2000));

                console.log(`Selecting Activity: ${config.activity}`);
                await page.evaluate((activity) => {
                    const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_Activities');
                    if (sel) {
                        const opt = Array.from(sel.options).find(o => o.text.includes(activity));
                        if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
                    }
                }, config.activity);

                await new Promise(r => setTimeout(r, 2000));

                console.log(`Setting Date: ${config.targetDate}`);
                await page.evaluate((date) => {
                    const sel = document.querySelector('#ctl00_MainContent__advanceSearchUserControl_startDate');
                    if (sel) {
                        sel.value = date;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }, config.targetDate);
                await new Promise(r => setTimeout(r, 1000));

                console.log("Clicking Search...");
                await page.click('#ctl00_MainContent__advanceSearchUserControl__searchBtn');

                // Wait for the results to appear (manual wait as it might be a postback)
                console.log("Waiting for results to load...");
                await new Promise(r => setTimeout(r, 10000));

                await page.screenshot({ path: 'after_search_click.png' });
                console.log("Saved after_search_click.png");

                // Check if results appeared
                const availabilityBtn = await page.$('a[id*="btnAvailability"]');
                if (availabilityBtn) {
                    console.log("Success: Results/Availability found!");
                    await page.screenshot({ path: 'puppeteer_success_home.png' });
                } else {
                    console.log("No availability button found after search.");
                }
            } else {
                console.log("Dropdown is in DOM but NOT visible yet.");
            }
        } else {
            console.log("Sites dropdown NOT found on home page even after delay.");
            // Fallback to direct search navigation if not on home page
            if (page.url().includes('memberHomePage.aspx')) {
                console.log("Navigating to direct search page as a fallback...");
                await page.goto("https://book.everyoneactive.com/Connect/mrmmemberbooking.aspx", { waitUntil: 'networkidle2' });
                await new Promise(r => setTimeout(r, 5000));
                await page.screenshot({ path: 'direct_search_page.png' });
            }
        }

    } catch (e) {
        console.error(`Error: ${e.message}`);
        await page.screenshot({ path: 'puppeteer_home_error.png' });
    } finally {
        await browser.close();
        console.log("Browser closed.");
    }
}

book();
