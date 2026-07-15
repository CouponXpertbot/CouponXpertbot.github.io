import asyncio
import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import aiohttp
import aiofiles
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import logging
import google.generativeai as genai

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


class CourseFolderBot:
    def __init__(self, config_path: str = 'config.json'):
        self.config = self._load_config(config_path)
        self.posted = self._load_posted()  # {coupon_code, course_id, course_slug}
        self.session = None
        self.browser = None
        self.context = None

        # Init Gemini if enabled
        if self.config.get('AI_ENABLED', True):
            try:
                genai.configure(api_key=self.config['GEMINI_KEY'])
                self.ai_model = genai.GenerativeModel(
                    self.config.get('AI_MODEL', 'gemini-pro')
                )
                logger.info("Gemini AI initialized")
            except Exception as e:
                logger.error(f"Gemini init failed: {e}")
                self.config['AI_ENABLED'] = False

    # ---------- Configuration ----------
    def _load_config(self, path: str) -> Dict:
        default = {
            'BOT_TOKEN': 'BOT_TOKEN',
            'CHANNEL_ID': '@your_channel',
            'CHANNEL_INVITE': 'https://t.me/your_channel',  # for button
            'GEMINI_KEY': 'YOUR_GEMINI_API_KEY',
            'CHECK_INTERVAL': 300,
            'COURSE_LIMIT': 50,
            'PLAYWRIGHT_HEADLESS': True,
            'AI_ENABLED': True,
            'AI_MODEL': 'gemini-pro',
            'AI_TEMPERATURE': 0.7,
            'AI_MAX_TOKENS': 200
        }
        try:
            with open(path, 'r') as f:
                cfg = json.load(f)
                default.update(cfg)  # merge
            return default
        except FileNotFoundError:
            with open(path, 'w') as f:
                json.dump(default, f, indent=2)
            return default

    # ---------- Database ----------
    def _load_posted(self) -> Dict:
        try:
            with open('posted.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    async def _save_posted(self):
        # Keep last 30 days
        cutoff = time.time() - 30 * 86400
        self.posted = {k: v for k, v in self.posted.items() if v > cutoff}
        async with aiofiles.open('posted.json', 'w') as f:
            await f.write(json.dumps(self.posted, indent=2))

    def _is_duplicate(self, coupon_code: str, course_id: str, course_slug: str) -> bool:
        # Check all three identifiers
        for key in [coupon_code, course_id, course_slug]:
            if key and key in self.posted:
                return True
        return False

    # ---------- HTTP ----------
    async def _get(self, url: str) -> Optional[str]:
        try:
            async with self.session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.text()
                logger.error(f"HTTP {resp.status} for {url}")
        except Exception as e:
            logger.error(f"Request error {url}: {e}")
        return None

    # ---------- Extraction ----------
    def extract_course_urls(self, html: str) -> List[str]:
        soup = BeautifulSoup(html, 'html.parser')
        urls = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if (href.startswith('https://coursefolder.net/') and
                not href.endswith('/') and
                not any(skip in href for skip in [
                    '/category/', '/about.php', '/contact.php', '/courses.php',
                    '/blog.php', '/compare.php', '/liveCategory/',
                    '/free-udemy-coupon.php', '.css', '.js', '/preview/'
                ])):
                urls.append(href)
        # dedupe
        seen = set()
        return [u for u in urls if not (u in seen or seen.add(u))]

    async def extract_course_data(self, coursefolder_url: str) -> Optional[Dict]:
        html = await self._get(coursefolder_url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        data = {
            'coursefolder_url': coursefolder_url,
            'title': None,
            'description': None,
            'image': None,
            'rating': None,
            'students': None,
            'language': None,
            'category': None,
            'udemy_url': None,
            'coupon_code': None,
            'course_id': None,
            'course_slug': None,
        }

        # ----- 1. Extract Udemy coupon URL from JSON-LD (preferred) -----
        json_ld_coupon = None
        for script in soup.find_all('script', type='application/ld+json'):
            if 'couponCode=' in script.text:
                match = re.search(r'https://www\.udemy\.com/course/[^"\']+', script.text)
                if match:
                    json_ld_coupon = match.group(0)
                    break

        if json_ld_coupon:
            data['udemy_url'] = json_ld_coupon
        else:
            # Fallback: scan <a> tags
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'udemy.com' in href and 'couponCode=' in href and '/preview/' not in href:
                    data['udemy_url'] = href
                    break

        if not data['udemy_url']:
            logger.warning(f"No Udemy coupon URL in {coursefolder_url}")
            return None

        # Extract coupon code, course ID, and slug
        data['coupon_code'] = re.search(r'couponCode=([^&]+)', data['udemy_url']).group(1)
        course_path = re.search(r'/course/([^/?]+)', data['udemy_url'])
        if course_path:
            data['course_slug'] = course_path.group(1)
            data['course_id'] = data['course_slug']  # use slug as ID (or could be numeric)

        # ----- 2. Other fields -----
        # Title
        h1 = soup.find('h1')
        if h1:
            data['title'] = h1.text.strip()

        # Image
        img = (soup.find('img', {'class': re.compile(r'course.*image')}) or
               soup.find('img', {'class': re.compile(r'.*thumb.*')}) or
               soup.find('img', {'class': re.compile(r'.*featured.*')}))
        if img and img.get('src'):
            src = img['src']
            if src.startswith('//'):
                src = 'https:' + src
            data['image'] = src

        # Description
        desc = (soup.find('div', {'class': re.compile(r'.*description.*')}) or
                soup.find('div', {'class': re.compile(r'.*content.*')}) or
                soup.find('div', {'class': re.compile(r'.*course.*info.*')}))
        if desc:
            data['description'] = desc.text.strip()[:400]

        # Rating
        rating_tag = soup.find(text=re.compile(r'\d+\.\d+\s+stars'))
        if rating_tag:
            data['rating'] = rating_tag.strip()
        else:
            meta = soup.find('meta', {'itemprop': 'ratingValue'})
            if meta and meta.get('content'):
                data['rating'] = meta['content']

        # Students
        students_tag = soup.find(text=re.compile(r'(\d+[,.]?\d*)\s*students', re.I))
        if students_tag:
            data['students'] = students_tag.strip()

        # Language
        for lang in ['English', 'Spanish', 'French', 'German', 'Chinese', 'Japanese', 'Korean']:
            if lang in soup.text:
                data['language'] = lang
                break

        # Category
        for cat in ['Development', 'Business', 'IT', 'Design', 'Marketing', 'Finance', 'Health']:
            if cat in soup.text:
                data['category'] = cat
                break

        data['timestamp'] = datetime.now().isoformat()
        return data

    # ---------- Playwright Verification ----------
    async def verify_coupon(self, coupon_url: str) -> Tuple[bool, Dict]:
        """Returns (is_valid, verification_data)"""
        try:
            if not self.browser:
                p = await async_playwright().start()
                self.browser = await p.chromium.launch(
                    headless=self.config.get('PLAYWRIGHT_HEADLESS', True)
                )
                self.context = await self.browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )

            page = await self.context.new_page()
            await page.goto(coupon_url, wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(2000)

            # Check price and coupon status
            price_free = await page.evaluate("""
                () => document.body.textContent.includes('Free') ||
                     document.body.textContent.includes('₹0') ||
                     document.body.textContent.includes('$0')
            """)
            coupon_applied = await page.evaluate("""
                () => document.body.textContent.includes('Coupon applied') ||
                     document.body.textContent.includes('Applied')
            """)
            expired = await page.evaluate("""
                () => document.body.textContent.toLowerCase().includes('expired') ||
                     document.body.textContent.toLowerCase().includes('invalid')
            """)
            has_enroll = await page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('button, a, input[type="submit"]');
                    for (const b of btns) {
                        const text = (b.textContent || b.value || '').toLowerCase();
                        if (text.includes('enroll') || text.includes('buy') || text.includes('purchase'))
                            return true;
                    }
                    return false;
                }
            """)

            await page.close()

            valid = price_free and coupon_applied and not expired and has_enroll
            return valid, {
                'is_free': price_free,
                'coupon_applied': coupon_applied,
                'is_expired': expired,
                'has_enroll': has_enroll,
                'verified_at': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Playwright error: {e}")
            return False, {'error': str(e)}

    # ---------- AI Formatting ----------
    async def ai_format_post(self, course_data: Dict) -> str:
        if not self.config.get('AI_ENABLED', False):
            return self._manual_format(course_data)

        try:
            prompt = f"""Format this Udemy course for a Telegram post. Keep it short and engaging.
Use emojis. Include the coupon URL as the enroll link.

Title: {course_data.get('title', 'N/A')}
Category: {course_data.get('category', 'N/A')}
Language: {course_data.get('language', 'N/A')}
Rating: {course_data.get('rating', 'N/A')}
Students: {course_data.get('students', 'N/A')}
Coupon URL: {course_data.get('udemy_url', 'N/A')}

Format like:
🎓 [Title]

⭐ Rating: [Rating] | 👨‍🎓 [Students] students
🌐 Language: [Language] | 📂 Category: [Category]

🔥 100% FREE – click the button below to enroll!
"""
            response = self.ai_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=self.config.get('AI_TEMPERATURE', 0.7),
                    max_output_tokens=self.config.get('AI_MAX_TOKENS', 200)
                )
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"AI error: {e}")
            return self._manual_format(course_data)

    def _manual_format(self, data: Dict) -> str:
        return f"""🎓 *{data.get('title', 'Unknown')}*

⭐ Rating: {data.get('rating', 'N/A')} | 👨‍🎓 {data.get('students', 'N/A')} students
🌐 Language: {data.get('language', 'N/A')} | 📂 Category: {data.get('category', 'N/A')}

🔥 100% FREE – click the button below to enroll!"""

    # ---------- Telegram ----------
    async def send_to_telegram(self, course_data: Dict, caption: str):
        try:
            # Two buttons: Enroll Now, Join Channel
            buttons = {
                'inline_keyboard': [
                    [{'text': '🎓 Enroll Now', 'url': course_data['udemy_url']}],
                    [{'text': '📢 Join Channel', 'url': self.config.get('CHANNEL_INVITE', 'https://t.me/your_channel')}]
                ]
            }

            # Send photo with caption (using URL directly, no download)
            if course_data.get('image'):
                url = f"https://api.telegram.org/bot{self.config['BOT_TOKEN']}/sendPhoto"
                data = {
                    'chat_id': self.config['CHANNEL_ID'],
                    'photo': course_data['image'],
                    'caption': caption,
                    'parse_mode': 'Markdown',
                    'reply_markup': json.dumps(buttons)
                }
                async with self.session.post(url, json=data) as resp:
                    if resp.status == 200:
                        logger.info(f"Posted {course_data.get('title')}")
                        return

            # Fallback: text only
            url = f"https://api.telegram.org/bot{self.config['BOT_TOKEN']}/sendMessage"
            data = {
                'chat_id': self.config['CHANNEL_ID'],
                'text': caption + f"\n\n🔗 {course_data['udemy_url']}",
                'parse_mode': 'Markdown',
                'reply_markup': json.dumps(buttons)
            }
            async with self.session.post(url, json=data) as resp:
                if resp.status == 200:
                    logger.info(f"Posted (text) {course_data.get('title')}")
                else:
                    logger.error(f"Telegram error: {resp.status}")
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    # ---------- Main Processing ----------
    async def process_course(self, course_url: str) -> bool:
        try:
            data = await self.extract_course_data(course_url)
            if not data:
                return False

            # Duplicate check
            if self._is_duplicate(data.get('coupon_code'), data.get('course_id'), data.get('course_slug')):
                logger.info(f"Duplicate: {data.get('title')}")
                return False

            # Verify only if not seen before
            valid, _ = await self.verify_coupon(data['udemy_url'])
            if not valid:
                logger.info(f"Invalid coupon: {data.get('title')}")
                return False

            # Format and send
            caption = await self.ai_format_post(data)
            await self.send_to_telegram(data, caption)

            # Mark as posted
            for key in [data['coupon_code'], data['course_id'], data['course_slug']]:
                if key:
                    self.posted[key] = time.time()
            await self._save_posted()
            return True
        except Exception as e:
            logger.error(f"Process error for {course_url}: {e}")
            return False

    # ---------- Main Loop ----------
    async def run(self):
        logger.info("Bot started")
        async with aiohttp.ClientSession() as session:
            self.session = session
            while True:
                try:
                    html = await self._get('https://coursefolder.net/live-free-udemy-coupon.php')
                    if not html:
                        await asyncio.sleep(60)
                        continue

                    urls = self.extract_course_urls(html)
                    logger.info(f"Found {len(urls)} courses")
                    posted = 0
                    for url in urls[:self.config.get('COURSE_LIMIT', 50)]:
                        if await self.process_course(url):
                            posted += 1
                            await asyncio.sleep(5)  # rate limit
                    logger.info(f"Posted {posted} new courses")

                    await asyncio.sleep(self.config.get('CHECK_INTERVAL', 300))
                except Exception as e:
                    logger.error(f"Loop error: {e}")
                    await asyncio.sleep(60)

    async def cleanup(self):
        if self.browser:
            await self.browser.close()


async def main():
    bot = CourseFolderBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        await bot.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
