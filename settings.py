BOT_NAME = "styleunion_scraper"

SPIDER_MODULES = ["styleunion_scraper.spiders"]
NEWSPIDER_MODULE = "styleunion_scraper.spiders"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

ROBOTSTXT_OBEY = False
COOKIES_ENABLED = True

# Playwright Integration Settings
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
}

CONCURRENT_REQUESTS = 1
DOWNLOAD_DELAY = 5

FEED_EXPORT_ENCODING = "utf-8"
