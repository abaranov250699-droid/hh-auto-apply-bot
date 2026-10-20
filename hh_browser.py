import re
import json
import random
import asyncio
from typing import Optional, Tuple, List, Dict, Any
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from config import DATA_DIR, SCREENSHOTS_DIR

# Словарь активных сессий логина в памяти: chat_id -> dict
_active_logins: Dict[int, Dict[str, Any]] = {}

# Скрипт маскировки браузера под реального пользователя (анти-детект)
STEALTH_INIT_SCRIPT = """
(() => {
    // 1. Скрываем автоматизацию
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });

    // 2. Имитируем окружение Chrome
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // 3. Плагины браузера
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' }
        ]
    });

    // 4. Языки
    Object.defineProperty(navigator, 'languages', {
        get: () => ['ru-RU', 'ru', 'en-US', 'en']
    });

    // 5. Платформа
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32'
    });

    // 6. Разрешения
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
})();
"""

def get_session_file(chat_id: int) -> Path:
    return DATA_DIR / f"session_{chat_id}.json"

def is_user_authenticated(chat_id: int) -> bool:
    session_file = get_session_file(chat_id)
    return session_file.exists() and session_file.stat().st_size > 50

async def get_browser_context(p, chat_id: Optional[int] = None) -> Tuple[Browser, BrowserContext]:
    """
    Создает изолированный браузерный контекст с полной маскировкой под реального пользователя.
    """
    # Набор реалистичных аргументов Chromium
    browser = await p.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu',
            '--window-size=1920,1080'
        ]
    )
    
    storage_state = None
    if chat_id is not None:
        session_file = get_session_file(chat_id)
        if session_file.exists():
            storage_state = str(session_file)

    # Случайное легкое отклонение в размерах экрана (как у реальных мониторов)
    width = 1920 + random.choice([-20, 0, 10])
    height = 1080 + random.choice([-15, 0, 15])

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": width, "height": height},
        storage_state=storage_state,
        has_touch=False,
        is_mobile=False,
        device_scale_factor=1
    )

    # Применяем Stealth-скрипт ко всем страницам
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return browser, context

async def human_type(page: Page, selector: str, text: str):
    """
    Печатает текст как живой человек: с рандомными паузами между буквами и пробелами.
    """
    element = await page.wait_for_selector(selector, state="visible", timeout=10000)
    await element.click()
    await asyncio.sleep(random.uniform(0.3, 0.7))

    for char in text:
        await page.keyboard.type(char)
        # Случайная пауза: для знаков препинания и пробелов чуть дольше
        if char in ('.', ',', '!', '?', '\n'):
            await asyncio.sleep(random.uniform(0.15, 0.35))
        elif char == ' ':
            await asyncio.sleep(random.uniform(0.06, 0.14))
        else:
            await asyncio.sleep(random.uniform(0.02, 0.07))

async def human_scroll(page: Page):
    """Имитирует скроллинг страницы живым человеком."""
    scroll_amount = random.randint(250, 600)
    await page.mouse.wheel(0, scroll_amount)
    await asyncio.sleep(random.uniform(1.0, 2.0))
    if random.random() > 0.5:
        # Иногда чуть прокручиваем обратно вверх
        await page.mouse.wheel(0, -random.randint(50, 150))
        await asyncio.sleep(random.uniform(0.5, 1.0))

async def check_auth_status(chat_id: int) -> Tuple[bool, str]:
    """Проверяет действительность сохраненной сессии."""
    session_file = get_session_file(chat_id)
    if not session_file.exists():
        return False, "Сессия не найдена. Требуется вход."
    
    async with async_playwright() as p:
        browser, context = await get_browser_context(p, chat_id)
        page = await context.new_page()
        try:
            await page.goto("https://hh.ru/applicant/resumes", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(1.5, 2.5))
            
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

async def start_phone_login(chat_id: int, phone: str) -> Tuple[bool, str, Optional[str]]:
    """Шаг 1 авторизации: ввод номера телефона."""
    if chat_id in _active_logins:
        try:
            await _active_logins[chat_id]["browser"].close()
        except Exception:
            pass
        _active_logins.pop(chat_id, None)

    digits = re.sub(r'\D', '', phone)
    if digits.startswith('7') or digits.startswith('8'):
        digits = digits[1:]
    
    if len(digits) != 10:
        return False, "Неверный формат номера. Введите номер РФ, например: +79991234567", None

    p = await async_playwright().start()
    browser, context = await get_browser_context(p)
    page = await context.new_page()

    try:
        await page.goto("https://hh.ru/account/login", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.2, 2.0))

        applicant_radio = await page.query_selector('[data-qa="account-type-card-APPLICANT"]')
        if applicant_radio:
            submit_btn = await page.query_selector('[data-qa="submit-button"]')
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(random.uniform(1.5, 2.2))

        phone_input = await page.query_selector('[data-qa="magritte-phone-input-national-number-input"]')
        if not phone_input:
            phone_input = await page.query_selector('input[type="tel"]')

        if not phone_input:
            scr_path = str(SCREENSHOTS_DIR / f"login_err_{chat_id}.png")
            await page.screenshot(path=scr_path)
            await browser.close()
            await p.stop()
            return False, "Не удалось найти поле ввода телефона на HH.ru.", scr_path

        await phone_input.fill("")
        for digit in digits:
            await phone_input.type(digit, delay=random.randint(40, 90))

        await asyncio.sleep(random.uniform(0.6, 1.2))

        submit_btn = await page.query_selector('[data-qa="submit-button"]')
        if not submit_btn:
            submit_btn = await page.query_selector('button[type="submit"]')

        if submit_btn:
            await submit_btn.click()
            await asyncio.sleep(random.uniform(2.5, 3.5))

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
            return False, "HH.ru запросил подтверждение (капчу). Попробуйте позже или используйте /cookie", scr_path

        _active_logins[chat_id] = {
            "playwright": p,
            "browser": browser,
            "context": context,
            "page": page,
            "has_captcha": False
        }
        return True, "Код подтверждения отправлен в SMS/Push от HH.ru. Введите полученный 4-значный код:", None

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
    """Шаг 2 авторизации: ввод SMS кода."""
    session = _active_logins.get(chat_id)
    if not session:
        return False, "Сессия авторизации истекла. Запустите /login заново."

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
                await asyncio.sleep(random.uniform(0.1, 0.2))
        elif inputs:
            await inputs[0].fill(clean_code)
        else:
            await page.keyboard.type(clean_code, delay=random.randint(50, 100))

        await asyncio.sleep(random.uniform(2.5, 3.5))

        confirm_btn = await page.query_selector('[data-qa="submit-button"], button[type="submit"]')
        if confirm_btn and await confirm_btn.is_visible():
            await confirm_btn.click()
            await asyncio.sleep(random.uniform(2.5, 3.5))

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
            return False, "Не удалось завершить вход. Попробуйте снова /login."

    except Exception as e:
        await browser.close()
        await p.stop()
        _active_logins.pop(chat_id, None)
        return False, f"Ошибка при вводе кода: {str(e)}"

async def save_cookie_login(chat_id: int, cookie_str: str) -> Tuple[bool, str]:
    """Вход по токену."""
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
    """
    ПОИСК ВАКАНСИЙ В 100% АНОНИМНОМ РЕЖИМЕ (БЕЗ КУК ПОЛЬЗОВАТЕЛЯ).
    HH.ru не знает, какой пользователь выполняет поиск.
    """
    target_url = search_url
    if "order_by=" not in target_url:
        sep = "&" if "?" in target_url else "?"
        target_url = f"{target_url}{sep}order_by=publication_time"

    vacancies = []
    async with async_playwright() as p:
        # Важно: chat_id=None — поиск строго анонимен!
        browser, context = await get_browser_context(p, chat_id=None)
        page = await context.new_page()
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(random.uniform(1.8, 3.0))

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
    """Получает описание вакансии также в анонимном режиме."""
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
            await page.goto(f"https://hh.ru/vacancy/{vacancy_id}", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(1.2, 2.0))

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
    """
    ОТКЛИК НА ВАКАНСИЮ С ПОЛНОЙ ИМИТАЦИЕЙ ЧЕЛОВЕЧЕСКОГО ПОВЕДЕНИЯ:
    - Задержка на чтение вакансии (3-6 сек)
    - Скроллинг страницы мышью
    - Наведение и клик
    - Посимвольный ввод сопроводительного письма (human typing)
    - Пауза перед финальным кликом
    """
    if not is_user_authenticated(chat_id):
        return False, "Вы не авторизованы в HH.ru! Выполните /login или /cookie", None

    async with async_playwright() as p:
        browser, context = await get_browser_context(p, chat_id)
        page = await context.new_page()
        scr_path = str(SCREENSHOTS_DIR / f"apply_{vacancy_id}_{chat_id}.png")

        try:
            await page.goto(f"https://hh.ru/vacancy/{vacancy_id}", wait_until="domcontentloaded", timeout=30000)
            
            # 1. Человеческая пауза: соискатель "читает" вакансию
            await asyncio.sleep(random.uniform(3.0, 5.5))

            # 2. Имитация чтения — скролл страницы
            await human_scroll(page)

            # Проверяем, не был ли уже совершен отклик ранее
            already = await page.query_selector('[data-qa="vacancy-response-link-view"]')
            page_text = await page.content()
            if already or "Вы откликнулись" in page_text:
                await browser.close()
                return True, "Вы уже откликались на эту вакансию ранее.", None

            # 3. Находим кнопку "Откликнуться"
            apply_btn = await page.query_selector('[data-qa="vacancy-response-link-top"]')
            if not apply_btn:
                apply_btn = await page.query_selector('a[href*="/applicant/vacancy_response"], button[data-qa*="vacancy-response"]')

            if not apply_btn:
                await page.screenshot(path=scr_path)
                await browser.close()
                return False, "Кнопка 'Откликнуться' не найдена. Возможно, вакансия закрыта.", scr_path

            # Наведение мыши и клик
            await apply_btn.hover()
            await asyncio.sleep(random.uniform(0.4, 0.8))
            await apply_btn.click()
            await asyncio.sleep(random.uniform(2.0, 3.5))

            # 4. Если открылось модальное окно — открываем сопроводительное письмо
            toggle_letter = await page.query_selector('[data-qa="vacancy-response-letter-toggle"]')
            if toggle_letter and await toggle_letter.is_visible():
                await toggle_letter.hover()
                await asyncio.sleep(random.uniform(0.3, 0.6))
                await toggle_letter.click()
                await asyncio.sleep(random.uniform(0.8, 1.5))

            # 5. Ввод письма как живой человек (побуквенно)
            letter_input = await page.query_selector(
                '[data-qa="vacancy-response-popup-form-letter-input"], textarea[name="message"], textarea'
            )
            if letter_input and await letter_input.is_visible() and cover_letter:
                # Очищаем поле
                await letter_input.fill("")
                # Печатаем по-человечески с небольшими задержками
                for chunk in re.split(r'(\s+)', cover_letter.strip()):
                    await letter_input.type(chunk, delay=random.randint(15, 35))
                    if '\n' in chunk:
                        await asyncio.sleep(random.uniform(0.2, 0.5))

                await asyncio.sleep(random.uniform(1.2, 2.5))

            # 6. Финальная кнопка отправки
            submit_response_btn = await page.query_selector(
                '[data-qa="vacancy-response-submit-popup"], button[data-qa*="vacancy-response-submit"]'
            )
            if submit_response_btn and await submit_response_btn.is_visible():
                await submit_response_btn.hover()
                await asyncio.sleep(random.uniform(0.5, 1.2))
                await submit_response_btn.click()
                await asyncio.sleep(random.uniform(2.5, 4.0))

            # Проверяем, не запросил ли работодатель обязательный опросник/тест
            test_questions = await page.query_selector('[data-qa="vacancy-response-questions"]')
            if test_questions:
                await page.screenshot(path=scr_path)
                await browser.close()
                return False, "Работодатель установил обязательные вопросы/тест. Откройте вакансию вручную.", scr_path

            await page.screenshot(path=scr_path)
            await browser.close()
            return True, "✅ Отклик с письмом успешно отправлен (маскировка 100%)!", None

        except Exception as e:
            try:
                await page.screenshot(path=scr_path)
            except Exception:
                scr_path = None
            await browser.close()
            return False, f"Ошибка при отправке отклика: {str(e)}", scr_path
