import re
import json
import random
import asyncio
from typing import Optional, Tuple, List, Dict, Any, Callable
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from config import DATA_DIR, SCREENSHOTS_DIR

_active_logins: Dict[int, Dict[str, Any]] = {}

STEALTH_INIT_SCRIPT = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
    Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
})();
"""

def get_session_file(chat_id: int) -> Path:
    return DATA_DIR / f"session_{chat_id}.json"

def is_user_authenticated(chat_id: int) -> bool:
    session_file = get_session_file(chat_id)
    return session_file.exists() and session_file.stat().st_size > 50

async def get_browser_context(p, chat_id: Optional[int] = None) -> Tuple[Browser, BrowserContext]:
    """Создает изолированный браузерный контекст."""
    browser = await p.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--no-first-run',
            '--disable-extensions',
            '--js-flags=--max-old-space-size=128',
            '--window-size=1280,800'
        ]
    )
    
    storage_state = None
    if chat_id is not None:
        session_file = get_session_file(chat_id)
        if session_file.exists():
            storage_state = str(session_file)

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1280, "height": 800},
        storage_state=storage_state
    )

    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return browser, context

async def check_auth_status(chat_id: int) -> Tuple[bool, str]:
    """Проверяет действительность сохраненной сессии."""
    session_file = get_session_file(chat_id)
    if not session_file.exists():
        return False, "Сессия не найдена. Требуется вход."
    
    async with async_playwright() as p:
        browser, context = await get_browser_context(p, chat_id)
        page = await context.new_page()
        try:
            await page.route(lambda u: any(x in u for x in ['mc.yandex', 'mail.ru', 'analytics']), lambda r: r.abort())
            await page.goto("https://hh.ru/applicant/resumes", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1.0)
            
            if "/account/login" in page.url:
                await browser.close()
                return False, "Сессия устарела. Пожалуйста, выполните вход заново."
            
            user_el = await page.query_selector('[data-qa="mainmenu_applicantProfile"]')
            user_name = await user_el.inner_text() if user_el else "Пользователь HH"
            await browser.close()
            return True, f"Авторизован ({user_name.strip()})"
        except Exception as e:
            await browser.close()
            return False, f"Ошибка проверки сессии: {str(e)}"

async def start_phone_login(
    chat_id: int, 
    phone: str, 
    progress_callback: Optional[Callable[[str], Any]] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Шаг 1 авторизации: быстрый вход по номеру телефона с блокировкой трекеров.
    """
    async def update_status(msg: str):
        if progress_callback:
            try:
                await progress_callback(msg)
            except Exception:
                pass

    if chat_id in _active_logins:
        try:
            await _active_logins[chat_id]["browser"].close()
            await _active_logins[chat_id]["playwright"].stop()
        except Exception:
            pass
        _active_logins.pop(chat_id, None)

    digits = re.sub(r'\D', '', phone)
    if digits.startswith('7') or digits.startswith('8'):
        digits = digits[1:]
    
    if len(digits) != 10:
        return False, "Неверный формат номера. Введите номер РФ, например: +79991234567 или 9991234567", None

    await update_status("🌐 Запускаю браузер и подключаюсь к странице входа...")

    p = await async_playwright().start()
    browser, context = await get_browser_context(p)
    page = await context.new_page()

    # Блокируем внешние метрики и тяжелые шрифты для моментальной загрузки (1-2 сек)
    await page.route(
        lambda u: any(x in u for x in ['mc.yandex', 'top-fwz1.mail.ru', 'google-analytics', 'mindbox', 'sberbank']), 
        lambda r: r.abort()
    )
    await page.route(
        lambda u: any(u.endswith(ext) for ext in ['.woff', '.woff2', '.ttf']), 
        lambda r: r.abort()
    )

    try:
        await page.goto("https://hh.ru/account/login", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1.0)

        # Выбираем "Соискатель", если отображается экран выбора
        applicant_radio = await page.query_selector('[data-qa*="account-type-card-APPLICANT"]')
        if applicant_radio:
            submit_btn = await page.query_selector('[data-qa="submit-button"]')
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(1.5)

        await update_status("📝 Страница загружена. Нахожу поле телефона...")

        # Ищем поле телефона
        phone_input = await page.query_selector('[data-qa="magritte-phone-input-national-number-input"]')
        if not phone_input:
            phone_input = await page.query_selector('input[type="tel"]')

        if not phone_input:
            # Ждем появления поля еще 3 секунды
            try:
                phone_input = await page.wait_for_selector(
                    '[data-qa="magritte-phone-input-national-number-input"], input[type="tel"]', 
                    timeout=4000
                )
            except Exception:
                phone_input = None

        if not phone_input:
            scr_path = str(SCREENSHOTS_DIR / f"login_err_{chat_id}.png")
            await page.screenshot(path=scr_path)
            await browser.close()
            await p.stop()
            return False, "Не удалось найти поле телефона. Рекомендуется использовать вход по токену через кнопку «🔑 Войти по hhtoken» (без SMS и капчи)!", scr_path

        await update_status(f"📱 Ввожу номер +7{digits}...")
        await phone_input.fill("")
        await phone_input.fill(digits)
        await asyncio.sleep(0.5)

        await update_status("📨 Отправляю запрос на получение SMS-кода...")
        submit_btn = await page.query_selector('[data-qa="submit-button"]')
        if not submit_btn:
            submit_btn = await page.query_selector('button[type="submit"]')

        if submit_btn:
            await submit_btn.click()
            await asyncio.sleep(2.5)

        # Проверяем, не появилась ли капча
        captcha = await page.query_selector('.smart-captcha, iframe[src*="captcha"], [data-qa="captcha"]')
        if captcha or "captcha" in page.url.lower():
            scr_path = str(SCREENSHOTS_DIR / f"captcha_{chat_id}.png")
            await page.screenshot(path=scr_path)
            _active_logins[chat_id] = {
                "playwright": p,
                "browser": browser,
                "context": context,
                "page": page,
                "has_captcha": True
            }
            return False, "HH.ru показал проверку (капчу). Рекомендуется использовать моментальный вход по токену через кнопку «🔑 Войти по hhtoken»!", scr_path

        _active_logins[chat_id] = {
            "playwright": p,
            "browser": browser,
            "context": context,
            "page": page,
            "has_captcha": False
        }
        return True, "Код подтверждения отправлен в SMS/Push от HH.ru! Введите полученный 4-значный код:", None

    except Exception as e:
        scr_path = str(SCREENSHOTS_DIR / f"login_exc_{chat_id}.png")
        try:
            await page.screenshot(path=scr_path)
        except Exception:
            scr_path = None
        await browser.close()
        await p.stop()
        return False, f"Ошибка при подключении к HH.ru: {str(e)}", scr_path

async def submit_otp_code(chat_id: int, code: str) -> Tuple[bool, str]:
    """Шаг 2 авторизации: ввод полученного SMS кода."""
    session = _active_logins.get(chat_id)
    if not session:
        return False, "Сессия авторизации истекла. Отправьте номер телефона заново."

    page: Page = session["page"]
    browser: Browser = session["browser"]
    context: BrowserContext = session["context"]
    p = session["playwright"]

    clean_code = re.sub(r'\D', '', code)
    if not clean_code:
        return False, "Код должен содержать цифры."

    try:
        inputs = await page.query_selector_all('input[data-qa*="otp-code"], input[name*="code"], input[autocomplete="one-time-code"]')
        if not inputs:
            inputs = await page.query_selector_all('input[type="text"], input[type="tel"]')

        if len(inputs) == 4 and len(clean_code) == 4:
            for idx, digit in enumerate(clean_code):
                await inputs[idx].fill(digit)
                await asyncio.sleep(0.1)
        elif inputs:
            await inputs[0].fill(clean_code)
        else:
            await page.keyboard.type(clean_code, delay=50)

        await asyncio.sleep(2.0)

        confirm_btn = await page.query_selector('[data-qa="submit-button"], button[type="submit"]')
        if confirm_btn and await confirm_btn.is_visible():
            await confirm_btn.click()
            await asyncio.sleep(2.5)

        session_file = get_session_file(chat_id)
        await context.storage_state(path=str(session_file))

        cookies = await context.cookies()
        has_auth_cookie = any(c['name'] in ('hhtoken', 'hhrole', '_xsrf') for c in cookies)

        await browser.close()
        await p.stop()
        _active_logins.pop(chat_id, None)

        if has_auth_cookie or not "/account/login" in page.url:
            return True, "✅ Авторизация успешна! Аккаунт подключен к боту."
        else:
            return False, "Не удалось войти. Возможно, код неверный или устарел."

    except Exception as e:
        await browser.close()
        await p.stop()
        _active_logins.pop(chat_id, None)
        return False, f"Ошибка при вводе кода: {str(e)}"

async def save_cookie_login(chat_id: int, cookie_str: str) -> Tuple[bool, str]:
    """Вход по токену hhtoken."""
    token = cookie_str.strip()
    if "hhtoken=" in token:
        m = re.search(r'hhtoken=([^;\s]+)', token)
        if m:
            token = m.group(1)

    state = {
        "cookies": [
            {
                "name": "hhtoken",
                "value": token,
                "domain": ".hh.ru",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax"
            }
        ],
        "origins": []
    }
    session_file = get_session_file(chat_id)
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    ok, msg = await check_auth_status(chat_id)
    if ok:
        return True, f"✅ Успешный вход по токену! {msg}"
    else:
        if session_file.exists():
            session_file.unlink()
        return False, f"❌ Не удалось войти по данному токену: {msg}"

async def search_hh_vacancies(search_url: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Поиск вакансий в анонимном режиме."""
    target_url = search_url
    if "order_by=" not in target_url:
        sep = "&" if "?" in target_url else "?"
        target_url = f"{target_url}{sep}order_by=publication_time"

    vacancies = []
    async with async_playwright() as p:
        browser, context = await get_browser_context(p, chat_id=None)
        page = await context.new_page()
        try:
            await page.route(lambda u: any(x in u for x in ['mc.yandex', 'mail.ru', 'analytics']), lambda r: r.abort())
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1.5)

            cards = await page.query_selector_all('[data-qa="vacancy-serp__vacancy"]')
            if not cards:
                cards = await page.query_selector_all('.vacancy-card--H8Place, [data-qa="serp-item__title"]')

            for card in cards[:limit]:
                try:
                    link_el = await card.query_selector('a[data-qa="serp-item__title"], a[data-qa="vacancy-serp__vacancy-title"]')
                    if not link_el:
                        link_el = card if await card.get_attribute('href') else None

                    if not link_el:
                        continue

                    title = (await link_el.inner_text()).strip()
                    href = await link_el.get_attribute('href')
                    if not href:
                        continue

                    m = re.search(r'/vacancy/(\d+)', href)
                    if not m:
                        continue
                    vacancy_id = m.group(1)
                    clean_url = f"https://hh.ru/vacancy/{vacancy_id}"

                    salary = "Не указана"
                    salary_el = await card.query_selector('[data-qa="vacancy-serp__vacancy-compensation"], [data-qa="vacancy-salary"]')
                    if salary_el:
                        salary = (await salary_el.inner_text()).strip()

                    company = "Компания не указана"
                    company_el = await card.query_selector('a[data-qa="vacancy-serp__vacancy-employer"], [data-qa="vacancy-company-name"]')
                    if company_el:
                        company = (await company_el.inner_text()).strip()

                    vacancies.append({
                        "id": vacancy_id,
                        "title": title,
                        "company": company,
                        "salary": salary,
                        "url": clean_url
                    })
                except Exception:
                    continue

        except Exception as e:
            print(f"Error scraping search page: {e}")
        finally:
            await browser.close()

    return vacancies

async def get_vacancy_description(vacancy_id: str) -> Dict[str, Any]:
    """Получает описание вакансии в анонимном режиме."""
    async with async_playwright() as p:
        browser, context = await get_browser_context(p, chat_id=None)
        page = await context.new_page()
        data = {
            "title": "",
            "company": "",
            "salary": "Не указана",
            "description": ""
        }
        try:
            await page.route(lambda u: any(x in u for x in ['mc.yandex', 'mail.ru', 'analytics']), lambda r: r.abort())
            await page.goto(f"https://hh.ru/vacancy/{vacancy_id}", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1.0)

            title_el = await page.query_selector('[data-qa="vacancy-title"]')
            if title_el:
                data["title"] = (await title_el.inner_text()).strip()

            comp_el = await page.query_selector('[data-qa="vacancy-company-name"]')
            if comp_el:
                data["company"] = (await comp_el.inner_text()).strip()

            sal_el = await page.query_selector('[data-qa="vacancy-salary"]')
            if sal_el:
                data["salary"] = (await sal_el.inner_text()).strip()

            desc_el = await page.query_selector('[data-qa="vacancy-description"]')
            if desc_el:
                data["description"] = (await desc_el.inner_text()).strip()

        except Exception as e:
            print(f"Error fetching vacancy description: {e}")
        finally:
            await browser.close()
    return data

async def apply_to_hh_vacancy(
    chat_id: int, 
    vacancy_id: str, 
    cover_letter: str
) -> Tuple[bool, str, Optional[str]]:
    """Отклик на вакансию с человеческой имитацией."""
    if not is_user_authenticated(chat_id):
        return False, "Вы не авторизованы в HH.ru! Выполните /login или /cookie", None

    async with async_playwright() as p:
        browser, context = await get_browser_context(p, chat_id)
        page = await context.new_page()
        scr_path = str(SCREENSHOTS_DIR / f"apply_{vacancy_id}_{chat_id}.png")

        try:
            await page.route(lambda u: any(x in u for x in ['mc.yandex', 'mail.ru', 'analytics']), lambda r: r.abort())
            await page.goto(f"https://hh.ru/vacancy/{vacancy_id}", wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(2.0)

            already = await page.query_selector('[data-qa="vacancy-response-link-view"]')
            page_text = await page.content()
            if already or "Вы откликнулись" in page_text:
                await browser.close()
                return True, "Вы уже откликались на эту вакансию ранее.", None

            apply_btn = await page.query_selector('[data-qa="vacancy-response-link-top"]')
            if not apply_btn:
                apply_btn = await page.query_selector('a[href*="/applicant/vacancy_response"], button[data-qa*="vacancy-response"]')

            if not apply_btn:
                await page.screenshot(path=scr_path)
                await browser.close()
                return False, "Кнопка 'Откликнуться' не найдена. Возможно, вакансия закрыта.", scr_path

            await apply_btn.hover()
            await asyncio.sleep(0.3)
            await apply_btn.click()
            await asyncio.sleep(2.0)

            toggle_letter = await page.query_selector('[data-qa="vacancy-response-letter-toggle"]')
            if toggle_letter and await toggle_letter.is_visible():
                await toggle_letter.click()
                await asyncio.sleep(0.8)

            letter_input = await page.query_selector(
                '[data-qa="vacancy-response-popup-form-letter-input"], textarea[name="message"], textarea'
            )
            if letter_input and await letter_input.is_visible() and cover_letter:
                await letter_input.fill("")
                for chunk in re.split(r'(\s+)', cover_letter.strip()):
                    await letter_input.type(chunk, delay=random.randint(15, 30))
                    if '\n' in chunk:
                        await asyncio.sleep(0.2)
                await asyncio.sleep(0.8)

            submit_response_btn = await page.query_selector(
                '[data-qa="vacancy-response-submit-popup"], button[data-qa*="vacancy-response-submit"]'
            )
            if submit_response_btn and await submit_response_btn.is_visible():
                await submit_response_btn.click()
                await asyncio.sleep(2.5)

            test_questions = await page.query_selector('[data-qa="vacancy-response-questions"]')
            if test_questions:
                await page.screenshot(path=scr_path)
                await browser.close()
                return False, "Работодатель требует ответить на тест/опросник. Откройте вакансию вручную.", scr_path

            await page.screenshot(path=scr_path)
            await browser.close()
            return True, "✅ Отклик с письмом успешно отправлен!", None

        except Exception as e:
            try:
                await page.screenshot(path=scr_path)
            except Exception:
                scr_path = None
            await browser.close()
            return False, f"Ошибка при отправке отклика: {str(e)}", scr_path
