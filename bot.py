import os
import asyncio
from typing import Optional
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)

from config import BOT_TOKEN
import database as db
from hh_browser import (
    start_phone_login, submit_otp_code, save_cookie_login, 
    check_auth_status, is_user_authenticated, apply_to_hh_vacancy
)
from ai_letter import rewrite_cover_letter
from scheduler import (
    get_vacancy_keyboard, format_vacancy_message, 
    check_user_vacancies, start_monitoring_scheduler
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- FSM Состояния ---
class LoginStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()

class CookieStates(StatesGroup):
    waiting_for_token = State()

class ResumeStates(StatesGroup):
    waiting_for_resume = State()

class SearchStates(StatesGroup):
    waiting_for_url = State()

class PromptStates(StatesGroup):
    waiting_for_prompt = State()

class AiKeyStates(StatesGroup):
    waiting_for_key = State()

class EditLetterStates(StatesGroup):
    waiting_for_text = State()

class RewriteLetterStates(StatesGroup):
    waiting_for_instruction = State()

# --- Главное меню (кнопки внизу экрана) ---
def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Поиск вакансий"), KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="📝 Мое резюме"), KeyboardButton(text="🔐 Аккаунт HH.ru")],
            [KeyboardButton(text="⚡ Проверить сейчас"), KeyboardButton(text="⚙️ Настройки AI")]
        ],
        resize_keyboard=True
    )

# --- Команда /start ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(message.chat.id)
    
    welcome_text = (
        f"👋 <b>Привет, {message.from_user.first_name}!</b>\n\n"
        "Я ваш личный ассистент по поиску работы и автооткликам на <b>HH.ru</b>.\n\n"
        "<b>Как мы работаем:</b>\n"
        "1. 🔐 <b>Подключаем ваш HH.ru</b> (через кнопку «🔐 Аккаунт HH.ru» или /login)\n"
        "2. 📝 <b>Указываем резюме</b> («📝 Мое резюме»)\n"
        "3. 🔍 <b>Задаем фильтр поиска</b> («🔍 Поиск вакансий» — просто отправьте ссылку из браузера)\n"
        "4. 🔔 Бот присылает новые вакансии с готовым письмом, которое можно отредактировать в 1 клик или сразу отправить!"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

# --- Статус системы ---
@dp.message(F.text == "📊 Статус")
@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user = await db.get_or_create_user(message.chat.id)
    stats = await db.get_user_stats(message.chat.id)
    auth_ok, auth_text = await check_auth_status(message.chat.id)

    status_icon = "🟢 Включен" if user.get("is_active") else "⏸ Приостановлен"
    auth_icon = "✅ Авторизован" if auth_ok else "❌ Не авторизован"
    resume_icon = "✅ Добавлено" if user.get("resume_text") else "⚠️ Не заполнено"
    search_icon = "✅ Настроена" if user.get("search_url") else "⚠️ Не задана"
    ai_icon = "✅ Подключен" if user.get("ai_key") else "ℹ️ Базовый режим (без ключа)"

    text = (
        f"📊 <b>Статус вашего ассистента:</b>\n\n"
        f"• <b>Мониторинг:</b> {status_icon}\n"
        f"• <b>HH.ru аккаунт:</b> {auth_icon} ({auth_text})\n"
        f"• <b>Резюме:</b> {resume_icon}\n"
        f"• <b>Поисковая ссылка:</b> {search_icon}\n"
        f"• <b>AI Генератор:</b> {ai_icon}\n\n"
        f"📈 <b>Статистика вакансий:</b>\n"
        f"• Всего обработано: <b>{stats['total']}</b>\n"
        f"• Откликов отправлено: <b>{stats['applied']}</b>\n"
        f"• Ожидают вашего решения: <b>{stats['pending']}</b>\n"
        f"• Пропущено: <b>{stats['skipped']}</b>\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="▶️ Запустить поиск" if not user.get("is_active") else "⏸ Поставить на паузу", 
                callback_data="toggle_active"
            )
        ]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# --- Управление аккаунтом HH.ru ---
@dp.message(F.text == "🔐 Аккаунт HH.ru")
@dp.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    await state.clear()
    auth_ok, auth_msg = await check_auth_status(message.chat.id)
    
    text = (
        f"🔐 <b>Управление доступом к HH.ru</b>\n\n"
        f"Текущий статус: <b>{auth_msg}</b>\n\n"
        "Выберите удобный способ входа:\n"
        "1. 📱 <b>По номеру телефона (SMS-код)</b> — бот запросит номер и код подтверждения.\n"
        "2. 🔑 <b>По токену (hhtoken)</b> — если хотите войти моментально без SMS.\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Войти по телефону (SMS)", callback_data="login_phone")],
        [InlineKeyboardButton(text="🔑 Войти по hhtoken", callback_data="login_cookie")],
        [InlineKeyboardButton(text="🔄 Проверить подключение", callback_data="check_auth")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "login_phone")
async def cb_login_phone(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(LoginStates.waiting_for_phone)
    await callback.message.answer(
        "📱 <b>Введите ваш номер телефона</b>, привязанный к аккаунту HH.ru:\n\n"
        "Пример: <code>+79991234567</code> или <code>9991234567</code>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(LoginStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    wait_msg = await message.answer("⏳ Открываю безопасное соединение с HH.ru и отправляю номер телефона...")

    ok, resp_msg, scr_path = await start_phone_login(message.chat.id, phone)
    await wait_msg.delete()

    if not ok:
        if scr_path and os.path.exists(scr_path):
            await message.answer_photo(
                photo=FSInputFile(scr_path),
                caption=f"⚠️ {resp_msg}"
            )
        else:
            await message.answer(f"❌ {resp_msg}")
        return

    await state.set_state(LoginStates.waiting_for_code)
    await message.answer(
        f"📩 {resp_msg}\n\n<i>Отправьте 4 цифры из SMS или Push от HeadHunter сюда в чат:</i>",
        parse_mode="HTML"
    )

@dp.message(LoginStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    wait_msg = await message.answer("⏳ Проверяю код подтверждения...")

    ok, resp_msg = await submit_otp_code(message.chat.id, code)
    await wait_msg.delete()

    if ok:
        await state.clear()
        await message.answer(f"🎉 {resp_msg}", reply_markup=get_main_keyboard())
    else:
        await message.answer(f"❌ {resp_msg}\nПопробуйте ввести код еще раз:")

@dp.callback_query(F.data == "login_cookie")
async def cb_login_cookie(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(CookieStates.waiting_for_token)
    await callback.message.answer(
        "🔑 <b>Вход по hhtoken</b>\n\n"
        "Отправьте значение куки <code>hhtoken</code> из браузера (DevTools -> Application -> Cookies -> hh.ru -> hhtoken):",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(CookieStates.waiting_for_token)
async def process_token(message: types.Message, state: FSMContext):
    token = message.text.strip()
    wait_msg = await message.answer("⏳ Проверяю токен...")
    ok, resp_msg = await save_cookie_login(message.chat.id, token)
    await wait_msg.delete()

    if ok:
        await state.clear()
        await message.answer(resp_msg, reply_markup=get_main_keyboard())
    else:
        await message.answer(resp_msg)

@dp.callback_query(F.data == "check_auth")
async def cb_check_auth(callback: types.CallbackQuery):
    ok, msg = await check_auth_status(callback.message.chat.id)
    icon = "✅" if ok else "❌"
    await callback.message.answer(f"{icon} {msg}")
    await callback.answer()

# --- Настройка поиска вакансий ---
@dp.message(F.text == "🔍 Поиск вакансий")
@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    user = await db.get_or_create_user(message.chat.id)
    current_url = user.get("search_url") or "Не задана"

    text = (
        f"🔍 <b>Настройка поиска вакансий</b>\n\n"
        f"<b>Текущая ссылка поиска:</b>\n<code>{current_url}</code>\n\n"
        "<b>Как настроить:</b>\n"
        "1. Откройте <b>hh.ru</b> в браузере.\n"
        "2. Введите должность, укажите нужный город, зарплату, удаленку и опыт.\n"
        "3. <b>Скопируйте адрес страницы</b> из браузера и пришлите в ответ сюда в чат!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить ссылку поиска", callback_data="set_search_url")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "set_search_url")
async def cb_set_search_url(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_url)
    await callback.message.answer(
        "🔗 <b>Пришлите ссылку на поиск вакансий с HH.ru:</b>\n"
        "(например: <code>https://hh.ru/search/vacancy?text=Python&salary=250000...</code>)",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(SearchStates.waiting_for_url)
async def process_search_url(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not ("hh.ru" in url):
        await message.answer("⚠️ Ссылка должна быть с сайта hh.ru. Попробуйте еще раз:")
        return

    await db.update_user(message.chat.id, search_url=url, is_active=1)
    await state.clear()
    await message.answer(
        "✅ <b>Ссылка поиска успешно сохранена!</b> Мониторинг новых вакансий активирован.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

# --- Настройка резюме ---
@dp.message(F.text == "📝 Мое резюме")
@dp.message(Command("resume"))
async def cmd_resume(message: types.Message, state: FSMContext):
    user = await db.get_or_create_user(message.chat.id)
    current_resume = user.get("resume_text")

    if current_resume:
        text = (
            f"📝 <b>Ваше текущее резюме:</b>\n\n"
            f"<i>{current_resume[:1000]}...</i>\n\n"
            "AI использует этот текст для генерации сопроводительных писем под каждую вакансию."
        )
    else:
        text = (
            "📝 <b>Резюме еще не добавлено.</b>\n\n"
            "Пришлите выжимку вашего опыта (навыки, стек, достижения), "
            "чтобы бот мог формировать точные и убедительные сопроводительные письма."
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Обновить резюме", callback_data="set_resume_text")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "set_resume_text")
async def cb_set_resume(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ResumeStates.waiting_for_resume)
    await callback.message.answer(
        "✍️ <b>Пришлите текст вашего резюме или описание ключевого опыта:</b>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(ResumeStates.waiting_for_resume)
async def process_resume_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    await db.update_user(message.chat.id, resume_text=text)
    await state.clear()
    await message.answer("✅ <b>Резюме успешно сохранено!</b>", reply_markup=get_main_keyboard(), parse_mode="HTML")

# --- Настройки AI ---
@dp.message(F.text == "⚙️ Настройки AI")
async def cmd_ai_settings(message: types.Message):
    user = await db.get_or_create_user(message.chat.id)
    has_key = bool(user.get("ai_key"))
    prompt = user.get("custom_prompt") or "По умолчанию (лаконично, профессионально, без штампов)"

    text = (
        f"⚙️ <b>Настройки генератора писем (AI)</b>\n\n"
        f"• <b>Gemini API ключ:</b> {'✅ Установлен' if has_key else '❌ Не установлен'}\n"
        f"• <b>Инструкция стилю письма:</b> <i>{prompt}</i>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Указать Gemini API ключ", callback_data="set_ai_key")],
        [InlineKeyboardButton(text="💬 Настроить стиль писем", callback_data="set_ai_prompt")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "set_ai_key")
async def cb_set_ai_key(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AiKeyStates.waiting_for_key)
    await callback.message.answer(
        "🔑 <b>Отправьте ваш ключ Gemini API</b> (получить можно бесплатно в Google AI Studio):\n"
        "Если хотите удалить ключ, отправьте слово <code>delete</code>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(AiKeyStates.waiting_for_key)
async def process_ai_key(message: types.Message, state: FSMContext):
    key = message.text.strip()
    if key.lower() == "delete":
        await db.update_user(message.chat.id, ai_key="")
        await message.answer("Ключ удален.")
    else:
        await db.update_user(message.chat.id, ai_key=key)
        await message.answer("✅ <b>Ключ Gemini API успешно сохранен!</b>", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "set_ai_prompt")
async def cb_set_ai_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(PromptStates.waiting_for_prompt)
    await callback.message.answer(
        "💬 <b>Напишите пожелания к стилю писем</b>\n"
        "(Например: <i>'Делай упор на опыт с Python/FastAPI, пиши максимально кратко, не более 2 абзацев'</i>):",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(PromptStates.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    await db.update_user(message.chat.id, custom_prompt=message.text.strip())
    await state.clear()
    await message.answer("✅ <b>Пожелания к стилю писем сохранены!</b>", reply_markup=get_main_keyboard(), parse_mode="HTML")

# --- Мгновенная проверка вакансий ---
@dp.message(F.text == "⚡ Проверить сейчас")
@dp.message(Command("check"))
async def cmd_check_now(message: types.Message):
    user = await db.get_or_create_user(message.chat.id)
    if not user.get("search_url"):
        await message.answer("⚠️ Сначала укажите ссылку поиска вакансий в разделе «🔍 Поиск вакансий».")
        return

    wait_msg = await message.answer("🔎 Ищу новые вакансии на HH.ru...")
    await check_user_vacancies(bot, user)
    await wait_msg.delete()
    await message.answer("🏁 Проверка завершена! Если появились новые вакансии, они отправлены выше.")

# --- Переключение активности мониторинга ---
@dp.callback_query(F.data == "toggle_active")
async def cb_toggle_active(callback: types.CallbackQuery):
    user = await db.get_or_create_user(callback.message.chat.id)
    new_state = 0 if user.get("is_active") else 1
    await db.update_user(callback.message.chat.id, is_active=new_state)

    status_str = "🟢 Мониторинг запущен!" if new_state else "⏸ Мониторинг приостановлен."
    await callback.message.answer(status_str)
    await callback.answer()

# --- Кнопка "Откликнуться" под вакансией ---
@dp.callback_query(F.data.startswith("apply:"))
async def cb_apply_vacancy(callback: types.CallbackQuery):
    vacancy_id = callback.data.split(":")[1]
    chat_id = callback.message.chat.id

    vac = await db.get_vacancy(vacancy_id, chat_id)
    if not vac:
        await callback.answer("Вакансия не найдена в базе.", show_alert=True)
        return

    letter = vac.get("cover_letter") or ""

    # Отправляем статус
    await callback.message.edit_reply_markup(reply_markup=None)
    status_msg = await callback.message.reply("⏳ <b>Отправляю отклик на HH.ru...</b>", parse_mode="HTML")

    ok, result_msg, scr_path = await apply_to_hh_vacancy(chat_id, vacancy_id, letter)
    await status_msg.delete()

    if ok:
        await db.update_vacancy(vacancy_id, chat_id, status="applied")
        await callback.message.reply(f"🎉 <b>Успех!</b>\n{result_msg}", parse_mode="HTML")
    else:
        await db.update_vacancy(vacancy_id, chat_id, status="failed", error_message=result_msg)
        if scr_path and os.path.exists(scr_path):
            await callback.message.reply_photo(
                photo=FSInputFile(scr_path),
                caption=f"⚠️ {result_msg}"
            )
        else:
            await callback.message.reply(f"❌ {result_msg}")

    await callback.answer()

# --- Кнопка "Редактировать" под вакансией ---
@dp.callback_query(F.data.startswith("edit:"))
async def cb_edit_vacancy(callback: types.CallbackQuery, state: FSMContext):
    vacancy_id = callback.data.split(":")[1]
    chat_id = callback.message.chat.id

    vac = await db.get_vacancy(vacancy_id, chat_id)
    if not vac:
        await callback.answer("Вакансия не найдена.", show_alert=True)
        return

    await state.set_state(EditLetterStates.waiting_for_text)
    await state.update_data(vacancy_id=vacancy_id, message_id=callback.message.message_id)

    await callback.message.reply(
        "✏️ <b>Отправьте в ответ новый текст сопроводительного письма:</b>\n\n"
        f"<i>Текущий проект:</i>\n{vac.get('cover_letter')}",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(EditLetterStates.waiting_for_text)
async def process_new_letter_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    vacancy_id = data.get("vacancy_id")
    msg_id = data.get("message_id")
    new_letter = message.text.strip()

    await db.update_vacancy(vacancy_id, message.chat.id, cover_letter=new_letter)
    await state.clear()

    # Обновляем сообщение с вакансией
    vac = await db.get_vacancy(vacancy_id, message.chat.id)
    if vac and msg_id:
        new_text = format_vacancy_message(vac, new_letter)
        kb = get_vacancy_keyboard(vacancy_id)
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=new_text,
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            pass

    await message.answer("✅ <b>Текст письма обновлен!</b> Вы можете откликнуться на него выше.", parse_mode="HTML")

# --- Кнопка "Переписать AI" под вакансией ---
@dp.callback_query(F.data.startswith("rewrite:"))
async def cb_rewrite_vacancy(callback: types.CallbackQuery, state: FSMContext):
    vacancy_id = callback.data.split(":")[1]
    await state.set_state(RewriteLetterStates.waiting_for_instruction)
    await state.update_data(vacancy_id=vacancy_id, message_id=callback.message.message_id)

    await callback.message.reply(
        "🤖 <b>Что нужно изменить в письме?</b>\n"
        "(Например: <i>'Сделай в 2 раза короче'</i> или <i>'Добавь больше уверенности и убери опыт с Django'</i>):",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(RewriteLetterStates.waiting_for_instruction)
async def process_rewrite_instruction(message: types.Message, state: FSMContext):
    data = await state.get_data()
    vacancy_id = data.get("vacancy_id")
    msg_id = data.get("message_id")
    instruction = message.text.strip()

    vac = await db.get_vacancy(vacancy_id, message.chat.id)
    if not vac:
        await message.answer("Вакансия не найдена.")
        await state.clear()
        return

    wait_msg = await message.answer("🤖 AI переписывает сопроводительное письмо...")

    user = await db.get_or_create_user(message.chat.id)
    new_letter = await rewrite_cover_letter(
        previous_letter=vac.get("cover_letter", ""),
        instruction=instruction,
        user_ai_key=user.get("ai_key")
    )
    await wait_msg.delete()

    await db.update_vacancy(vacancy_id, message.chat.id, cover_letter=new_letter)
    await state.clear()

    # Обновляем оригинальную карточку вакансии
    if msg_id:
        new_text = format_vacancy_message(vac, new_letter)
        kb = get_vacancy_keyboard(vacancy_id)
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=new_text,
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            pass

    await message.answer("✅ <b>Письмо переписано нейросетью!</b> Проверьте обновленный вариант выше.", parse_mode="HTML")

# --- Кнопка "Пропустить" под вакансией ---
@dp.callback_query(F.data.startswith("skip:"))
async def cb_skip_vacancy(callback: types.CallbackQuery):
    vacancy_id = callback.data.split(":")[1]
    chat_id = callback.message.chat.id

    await db.update_vacancy(vacancy_id, chat_id, status="skipped")
    await callback.message.edit_text(
        f"<s>{callback.message.text}</s>\n\n❌ <i>Вакансия пропущена.</i>",
        parse_mode="HTML",
        reply_markup=None
    )
    await callback.answer("Пропущено")
