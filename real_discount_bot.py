import re
import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.sync_api import sync_playwright

# --- Configuration ---
# Replace with the list of course URLs from real.discount you want to check
REAL_DISCOUNT_URLS = [
    "https://real.discount/offer/claude-ai-for-data-analysis-business-intelligence-491",
    "https://real.discount/topics/top_free_courses/Python",
]

# --- Helper: URL Cleaning Function (copied from your bots) ---
def clean_udemy_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    allowed = {}
    if "couponCode" in params:
        allowed["couponCode"] = params["couponCode"][0]
    new_query = urlencode(allowed)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

def scrape_real_discount(page, course_url: str):
    print(f"🌐 Processing: {course_url}")
    # 1. Go to the course page and wait for it to load
    page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle")

    # 2. Find the 'Get Course' button and get its href
    # The selector may need adjusting if the site's HTML changes.
    button = page.locator("a:has-text('Get Course')").first
    if not button:
        print(f"   ⚠️ Could not find 'Get Course' button on {course_url}")
        return None

    affiliate_link = button.get_attribute("href")
    if not affiliate_link:
        print(f"   ⚠️ Found button but no href on {course_url}")
        return None

    print(f"   🧩 Found affiliate/tracking link: {affiliate_link}")

    # 3. Extract the final Udemy URL from the affiliate link
    parsed_aff_link = urlparse(affiliate_link)
    query_params = parse_qs(parsed_aff_link.query)

    final_udemy_url = None
    if "murl" in query_params:
        # This is the standard pattern for Linksynergy links
        encoded_murl = query_params["murl"][0]
        final_udemy_url = encoded_murl
        print(f"   🎯 Extracted murl parameter: {final_udemy_url}")

    if not final_udemy_url:
        # Fallback: maybe it's a direct Udemy link
        if "udemy.com/course/" in affiliate_link:
            final_udemy_url = affiliate_link

    if not final_udemy_url:
        print(f"   ❌ Could not extract a final Udemy URL from: {course_url}")
        return None

    # Decode the URL (Linksynergy links are URL-encoded)
    import urllib.parse
    final_udemy_url = urllib.parse.unquote(final_udemy_url)
    print(f"   🔗 Decoded Udemy URL: {final_udemy_url}")

    # Clean the URL, keeping only the couponCode
    cleaned_url = clean_udemy_url(final_udemy_url)
    print(f"   ✨ Cleaned Udemy URL: {cleaned_url}")
    return cleaned_url

def main():
    print("🚀 Real.discount Scraper Started")
    
    # Launch the browser once
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        all_cleaned_links = []
        for course_url in REAL_DISCOUNT_URLS:
            cleaned_link = scrape_real_discount(page, course_url)
            if cleaned_link:
                all_cleaned_links.append(cleaned_link)
        
        browser.close()

    # --- Optional: Integrate with your existing bot logic ---
    # Here, you would loop through all_cleaned_links,
    # check them against posted_courses.txt, validate them with Playwright,
    # and then post to Telegram.
    print("\n✅ Scraping completed. Found Udemy links:")
    for link in all_cleaned_links:
        print(f"   {link}")

if __name__ == "__main__":
    main()
