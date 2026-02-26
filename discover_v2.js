const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());
require('dotenv').config();

const config = {
    username: process.env.stev_smash_LOGIN_USERNAME,
    password: process.env.stev_smash_LOGIN_PASSWORD,
    url: process.env.stev_smash_LOGIN_URL || "https://account.everyoneactive.com/login/",
};

async function book() {
    console.log(`Starting Puppeteer discovery...`);
    const browser = await puppeteer.launch({ headless: true });
    const page = (await browser.pages())[0];
    await page.setViewport({ width: 1280, height: 800 });

    try {
        console.log(`Navigating to ${config.url}...`);
        await page.goto(config.url, { waitUntil: 'networkidle2' });
        console.log(`URL: ${page.url()}, Title: ${await page.title()}`);

        const links = await page.evaluate(() => {
            return Array.from(document.querySelectorAll('a')).map(a => ({
                text: a.innerText.trim(),
                href: a.href,
                id: a.id
            })).filter(l => l.text.length > 0 || l.id.length > 0);
        });

        console.log("Found links on page:");
        links.forEach(l => console.log(`- [${l.id}] ${l.text}: ${l.href}`));

        const inputs = await page.evaluate(() => {
            return Array.from(document.querySelectorAll('input, button')).map(i => ({
                type: i.type,
                id: i.id,
                name: i.name,
                placeholder: i.placeholder,
                value: i.value
            }));
        });
        console.log("Found inputs/buttons:");
        inputs.forEach(i => console.log(`- ${i.type} ID:${i.id} Name:${i.name} Placeholder:${i.placeholder}`));

    } catch (e) {
        console.error(`Error: ${e.message}`);
    } finally {
        await browser.close();
    }
}

book();
