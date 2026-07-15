import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import aiohttp
import aiofiles
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import logging

# --- New Google GenAI SDK ---
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logging.warning("google.genai not installed. AI features disabled.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


class CourseFolderBot:
    def __init__(self, config_path: str = 'config.json'):
        self.config = self._load_config(config_path)
        self.posted = self._load_posted()
        self.session = None
        self.browser = None
        self.context = None

        # Init Gemini (new SDK) with model set to Gemini 3.1 Lite
        if self.config.get('AI_ENABLED', True) and GENAI_AVAILABLE:
            try:
                self.ai_client = genai.Client(api_key=self.config['GEMINI_API_KEY'])
                self.ai_model = self.config.get('AI_MODEL', 'gemini-3.1-lite')  # as requested
                logger.info(f"Gemini AI initialized with model: {self.ai_model}")
            except Exception as e:
                logger.error(f"Gemini init failed: {e}")
                self.config['AI_ENABLED'] = False
        else:
            self.config['AI_ENABLED'] = False

    def _load_config(self, path: str) -> Dict:
        default = {
            'BOT_TOKEN': None,
            'CHANNEL_ID': None,
            'CHANNEL_INVITE': 'https://t.me/your_channel',
            'GEMINI_API_KEY': None,          # primary env var
            'GEMINI_KEY': None,              # fallback (legacy)
            'CHECK_INTERVAL': 300,
            'COURSE_LIMIT': 50,
            'PLAYWRIGHT_HEADLESS': True,
            'AI_ENABLED': True,
            'AI_MODEL': 'gemini-3.1-lite',   # default model
            'AI_TEMPERATURE': 0.7,
            'AI_MAX_TOKENS': 200,
            'PLAYWRIGHT_TIMEOUT': 45000
        }

        # 1. Read from environment
        # We'll look for GEMINI_API_KEY first, then GEMINI_KEY
        for key in default.keys():
            env_val = None
            if key == 'GEMINI_API_KEY':
                env_val = os.environ.get('GEMINI_API_KEY') or os.environ.get('GEMINI_KEY')
            else:
                env_val = os.environ.get(key)

            if env_val is not None:
                if key in ['CHECK_INTERVAL', 'COURSE_LIMIT', 'AI_MAX_TOKENS', 'PLAYWRIGHT_TIMEOUT']:
                    try:
                        default[key] = int(env_val)
                    except ValueError:
                        pass
                elif key == 'AI_TEMPERATURE':
                    try:
                        default[key] = float(env_val)
                    except ValueError:
                        pass
                elif key in ['PLAYWRIGHT_HEADLESS', 'AI_ENABLED']:
                    default[key] = env_val.lower() == 'true'
                else:
                    default[key] = env_val

        # 2. Override with config.json if exists (for local testing)
        try:
            with open(path, 'r') as f:
                file_cfg = json.load(f)
                for key, value in file_cfg.items():
                    if key in default:
                        default[key] = value
            logger.info(f"Loaded config from {path}")
        except FileNotFoundError:
            logger.info("No config.json, using environment variables only")

        # Validate that we have an API key (either GEMINI_API_KEY or GEMINI_KEY)
        api_key = default.get('GEMINI_API_KEY') or default.get('GEMINI_KEY')
        if not api_key:
            raise ValueError("Missing Gemini API key. Set GEMINI_API_KEY or GEMINI_KEY in environment or config.json.")
        # Normalize: set GEMINI_API_KEY to the found value
        default['GEMINI_API_KEY'] = api_key

        # Validate other required
        required = ['BOT_TOKEN', 'CHANNEL_ID']
        missing = [k for k in required if default.get(k) is None]
        if missing:
            raise ValueError(f"Missing required config: {missing}. Set as env vars or in config.json.")

        return default

    def _load_posted(self) -> Dict:
        try:
            with open('posted.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    async def _save_posted(self):
        cutoff = time.time() - 30 * 86400
        self.posted = {k: v for k, v in self.posted.items() if v > cutoff}
        async with aiofiles.open('posted.json', 'w') as f:
            await f.write(json.dumps(self.posted, indent=2))

    def _is_duplicate(self, coupon_code: str, course_id: str, course_slug: str) -> bool:
        for key in [coupon_code, course_id, course_slug]:
            if key and key in self.posted:
                return True
        return False

    async def _get(self, url: str) -> Optional[str]:
        try:
            async with self.session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.text()
                logger.error(f"HTTP {resp.status} for {url}")
        except Exception as e:
            logger.error(f"Request error {url}: {e}")
        return None

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
                    '/free-udemy-coupon.php', '/udemy-coupon-codes.php',
                    '.css', '.js', '/preview/', '/preview-embed/'
                ])):
                urls.append(href)
        # Deduplicate
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

        # JSON‑LD first
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
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'udemy.com' in href and 'couponCode=' in href and '/preview/' not in href:
                    data['udemy_url'] = href
                    break

        if not data['udemy_url']:
            logger.warning(f"No Udemy coupon URL in {coursefolder_url}")
            return None

        code_match = re.search(r'couponCode=([^&]+)', data['udemy_url'])
        if code_match:
            data['coupon_code'] = code_match.group(1)
        else:
            logger.warning(f"No coupon code found in {data['udemy_url']}")
            return None

        course_path = re.search(r'/course/([^/?]+)', data['udemy_url'])
        if course_path:
            data['course_slug'] = course_path.group(1)
            data['course_id'] = data['course_slug']

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

        # Rating – use string= instead of text=
        rating_tag = soup.find(string=re.compile(r'\d+\.\d+\s+stars'))
        if rating_tag:
            data['rating'] = rating_tag.strip()
        else:
            meta = soup.find('meta', {'itemprop': 'ratingValue'})
            if meta and meta.get('content'):
                data['rating'] = meta['content']

        # Students – use string=
        students_tag = soup.find(string=re.compile(r'(\d+[,.]?\d*)\s*students', re.I))
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

    async def verify_coupon(self, coupon_url: str) -> Tuple[bool, Dict]:
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
            timeout = self.config.get('PLAYWRIGHT_TIMEOUT', 45000)
            await page.goto(coupon_url, wait_until='domcontentloaded', timeout=timeout)
            await page.wait_for_timeout(3000)

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
            logger.error(f"Playwright error for {coupon_url}: {e}")
            return False, {'error': str(e)}

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
            response = self.ai_client.models.generate_content(
                model=self.ai_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=self.config.get('AI_TEMPERATURE', 0.7),
                    max_output_tokens=self.config.get('AI_MAX_TOKENS', 200)
                )
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"AI formatting error: {e}")
            return self._manual_format(course_data)

    def _manual_format(self, data: Dict) -> str:
        return f"""🎓 *{data.get('title', 'Unknown')}*

⭐ Rating: {data.get('rating', 'N/A')} | 👨‍🎓 {data.get('students', 'N/A')} students
🌐 Language: {data.get('language', 'N/A')} | 📂 Category: {data.get('category', 'N/A')}

🔥 100% FREE – click the button below to enroll!"""

    async def send_to_telegram(self, course_data: Dict, caption: str):
        try:
            buttons = {
                'inline_keyboard': [
                    [{'text': '🎓 Enroll Now', 'url': course_data['udemy_url']}],
                    [{'text': '📢 Join Channel', 'url': self.config.get('CHANNEL_INVITE', 'https://t.me/your_channel')}]
                ]
            }

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

            # Fallback text
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

    async def process_course(self, course_url: str) -> bool:
        try:
            data = await self.extract_course_data(course_url)
            if not data:
                return False

            if self._is_duplicate(data.get('coupon_code'), data.get('course_id'), data.get('course_slug')):
                logger.info(f"Duplicate: {data.get('title')}")
                return False

            valid, _ = await self.verify_coupon(data['udemy_url'])
            if not valid:
                logger.info(f"Invalid coupon: {data.get('title')}")
                return False

            caption = await self.ai_format_post(data)
            await self.send_to_telegram(data, caption)

            for key in [data['coupon_code'], data['course_id'], data['course_slug']]:
                if key:
                    self.posted[key] = time.time()
            await self._save_posted()
            return True
        except Exception as e:
            logger.error(f"Process error for {course_url}: {e}")
            return False

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
                            await asyncio.sleep(5)
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
