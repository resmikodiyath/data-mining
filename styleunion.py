import json
import random
import re
from urllib.parse import urljoin, urlparse
import scrapy


class StyleUnionSpider(scrapy.Spider):
    name = "styleunion"
    allowed_domains = ["styleunion.in"]

    start_url = "https://styleunion.in/collections/new-in-women?page=1"
    max_pages = 2
    products_per_page = 10

    custom_settings = {
        "FEEDS": {
            "styleunion_products.json": {
                "format": "json",
                "encoding": "utf8",
                "indent": 2,
                "mode": "overwrite",
            },
            "styleunion_products.csv": {
                "format": "csv",
                "encoding": "utf8",
                "mode": "overwrite",
            },
        },
        "FEED_EXPORT_FIELDS": [
            "product_url",
            "product_name",
            "price",
            "sku",
            "size",
            "color",
            "size_list",
            "color_list",
            "description",
            "wash_and_care",
            "image_urls",
            "product_details",
            "currency",
        ],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visited_products = set()

    async def start(self):
        yield scrapy.Request(
            self.start_url,
            callback=self.parse_listing,
            meta={
                "playwright": True,
                "page_number": 1,
                "handle_httpstatus_list": [403, 429],
            },
        )

    def parse_listing(self, response):
        page_number = response.meta["page_number"]

        if response.status in (403, 429):
            self.logger.warning(
                "Rate limited on listing page %s (HTTP %s). Retrying...",
                page_number,
                response.status,
            )
            yield response.request.copy()
            return

        self.logger.info("Reading listing page %s: %s", page_number, response.url)

        links = response.css('a[href*="/products/"]::attr(href)').getall()
        selected_urls = []

        for link in links:
            product_url = urljoin(response.url, link).split("?")[0]

            if (
                urlparse(product_url).netloc.endswith("styleunion.in")
                and "/products/" in urlparse(product_url).path
                and product_url not in self.visited_products
            ):
                self.visited_products.add(product_url)
                selected_urls.append(product_url)

            if len(selected_urls) >= self.products_per_page:
                break

        for product_url in selected_urls:
            yield scrapy.Request(
                product_url,
                callback=self.parse_product,
                meta={
                    "playwright": True,
                    "handle_httpstatus_list": [403, 429],
                },
            )

        next_page = page_number + 1
        if next_page <= self.max_pages:
            next_url = f"https://styleunion.in/collections/new-in-women?page={next_page}"
            yield scrapy.Request(
                next_url,
                callback=self.parse_listing,
                meta={
                    "playwright": True,
                    "page_number": next_page,
                    "handle_httpstatus_list": [403, 429],
                },
            )

    def parse_product(self, response):
        if response.status in (403, 429):
            self.logger.warning("Rate limited on product page %s. Retrying...", response.url)
            yield response.request.copy()
            return

        jsonld_products = self.get_jsonld_products(response)
        shopify_product = self.get_shopify_product(response)

        # Product Name
        product_name = self.first_value(
            shopify_product.get("title"),
            self.get_jsonld_value(jsonld_products, "name"),
            response.css("h1::text").get(),
        )

        variants = shopify_product.get("variants", [])
        first_variant = variants[0] if variants else {}

        options = shopify_product.get("options", [])
        option_map = {}
        if isinstance(options, list) and options and isinstance(options[0], dict):
            option_map = {
                str(opt.get("name", "")).strip().lower(): opt.get("values", [])
                for opt in options
            }

        size_list = self.find_option_values(option_map, "size")
        color_list = self.find_option_values(option_map, "color")

        variant_options = first_variant.get("options", [])
        size, color = "", ""

        for index, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            opt_name = str(option.get("name", "")).lower()
            opt_val = variant_options[index] if index < len(variant_options) else ""
            if "size" in opt_name:
                size = str(opt_val or "")
            elif "color" in opt_name or "colour" in opt_name:
                color = str(opt_val or "")

        # SKU Extraction (Safe Fallback)
        sku = self.first_value(
            first_variant.get("sku"),
            self.get_jsonld_value(jsonld_products, "sku"),
            response.css('[class*="sku"]::text').get(),
        )
        if sku:
            sku_match = re.search(r'([A-Za-z0-9\-]+)', str(sku))
            sku = sku_match.group(1) if sku_match else sku

        # Price Extraction
        price_value = self.first_value(
            first_variant.get("price"),
            self.get_jsonld_price(jsonld_products),
            response.css('.price::text, span.money::text, [class*="price"]::text').get(),
        )
        price = self.to_float(price_value)

        currency_code = self.first_value(
            first_variant.get("currency"),
            self.get_jsonld_currency(jsonld_products),
            "INR",
        )
        currency = "₹" if str(currency_code).upper() == "INR" else currency_code

        # Description & Wash and Care
        raw_desc = self.first_value(
            shopify_product.get("body_html"),
            self.get_jsonld_value(jsonld_products, "description"),
            response.css('[class*="description"], [id*="description"]').xpath("string(.)").get(),
            ""
        )
        cleaned_desc = self.clean_html(raw_desc)
        wash_and_care = self.extract_wash_and_care(cleaned_desc, response)

        item = {
            "product_url": response.url,
            "product_name": self.clean_text(product_name),
            "price": price,
            "sku": self.clean_text(sku),
            "size": self.clean_text(size),
            "color": self.clean_text(color),
            "size_list": size_list,
            "color_list": color_list,
            "description": cleaned_desc,
            "wash_and_care": wash_and_care,
            "image_urls": self.extract_images(response, shopify_product),
            "product_details": self.extract_product_details(response),
            "currency": currency,
        }

        self.logger.info("Extracted product: %s [Price: %s]", item["product_name"], item["price"])
        yield item

    def get_jsonld_products(self, response):
        products = []
        for script in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(script)
                self.collect_jsonld_products(data, products)
            except (json.JSONDecodeError, TypeError):
                continue
        return products

    def collect_jsonld_products(self, data, products):
        if isinstance(data, list):
            for value in data:
                self.collect_jsonld_products(value, products)
        elif isinstance(data, dict):
            if data.get("@type") == "Product" or "Product" in str(data.get("@type", "")):
                products.append(data)
            if "@graph" in data:
                self.collect_jsonld_products(data["@graph"], products)

    def get_jsonld_value(self, products, key):
        for p in products:
            if p.get(key):
                return p.get(key)
        return ""

    def get_jsonld_price(self, products):
        for p in products:
            offers = p.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            for o in offers:
                if o.get("price") is not None:
                    return o.get("price")
        return None

    def get_jsonld_currency(self, products):
        for p in products:
            offers = p.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            for o in offers:
                if o.get("priceCurrency"):
                    return o.get("priceCurrency")
        return "INR"

    def get_shopify_product(self, response):
        for script in response.css("script::text").getall():
            script = script.strip()
            if not script.startswith("{"):
                continue
            try:
                data = json.loads(script)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("variants"), list):
                if "title" in data or "options" in data:
                    return data
        return {}

    def extract_images(self, response, shopify_product):
        images = set()
        for img in shopify_product.get("images", []):
            if isinstance(img, str):
                images.add(urljoin(response.url, img))
            elif isinstance(img, dict) and img.get("src"):
                images.add(urljoin(response.url, img["src"]))
        
        for src in response.css('img[src*="products"]::attr(src), .product__media img::attr(src), img::attr(data-src)').getall():
            full_url = urljoin(response.url, src).split("?")[0]
            if "styleunion.in" in full_url or full_url.startswith("http"):
                images.add(full_url)

        return sorted(images)

    def extract_wash_and_care(self, description_text, response):
        match = re.search(r"(Wash and Care.*)", description_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    def extract_product_details(self, response):
        details = {}
        for row in response.css("table tr, .product-details__item, [class*='detail'] li"):
            k = self.clean_text(row.css("th::text, td:first-child::text, span:first-child::text").get())
            v = self.clean_text(row.css("td:last-child::text, span:last-child::text").get())
            if k and v and k.lower() != v.lower():
                details[k] = v
        return details

    def find_option_values(self, option_map, keyword):
        for name, vals in option_map.items():
            if keyword in name and isinstance(vals, list):
                return [self.clean_text(v) for v in vals if self.clean_text(v)]
        return []

    def first_value(self, *values):
        for v in values:
            if v is not None and str(v).strip():
                return v
        return ""

    def to_float(self, value):
        if value is None or value == "":
            return None
        try:
            cleaned = re.sub(r"[^\d.]", "", str(value))
            return float(cleaned) if cleaned else None
        except (ValueError, TypeError):
            return None

    def clean_text(self, value):
        if value is None:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value))).strip()

    def clean_html(self, value):
        if not value:
            return ""
        return self.clean_text(scrapy.Selector(text=str(value)).xpath("string(.)").get())
