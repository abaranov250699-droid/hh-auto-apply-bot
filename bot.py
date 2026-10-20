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
    check_single_filter, start_monitoring_scheduler
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

class FilterStates(StatesGroup):
    waiting_for_name = State()
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
            [KeyboardButton(text="🎯 Мои фильтры поиска"), KeyboardButton(text="📊 Статус")],
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
        "1. 🔐 <b>Подключаем ваш HH.ru</b> («🔐 Аккаунт HH.ru» или /login)\n"
        "2. 📝 <b>Указываем резюме</b> («📝 Мое резюме»)\n"
        "3. 🎯 <b>Добавляем фильтры поиска</b> («🎯 Мои фильтры поиска» — можно добавить несколько направлений!)\n"
        "4. 🔔 Бот в реальном времени мониторит HH и присылает новые вакансии с готовым письмом прямо на iPhone!"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

# --- Статус системы ---
@dp.message(F.text == "📊 Статус")
@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user = await db.get_or_create_user(message.chat.id)
    stats = await db.get_user_stats(message.chat.id)
    auth_ok, auth_text = await check_auth_status(message.chat.id)
    filters = await db.get_user_filters(message.chat.id)
    active_filters_count = sum(1 for f in filters if f.get("is_active"))

    status_icon = "🟢 Включен" if user.get("is_active") else "⏸ Приостановлен"
    auth_icon = "✅ Авторизован" if auth_ok else "❌ Не авторизован"
    resume_icon = "✅ Добавлено" if user.get("resume_text") else "⚠️ Не заполнено"
    filters_desc = f"✅ {active_filters_count} активных (всего {len(filters)})" if filters else "⚠️ Фильтры не добавлены"
    ai_icon = "✅ Подключен" if user.get("ai_key") else "ℹ️ Базовый режим (без ключа)"

    text = (
        f"📊 <b>Статус вашего ассистента:</b>\n\n"
        f"• <b>Мониторинг:</b> {status_icon}\n"
        f"• <b>HH.ru аккаунт:</b> {auth_icon} ({auth_text})\n"
        f"• <b>Резюме:</b> {resume_icon}\n"
        f"• <b>Фильтры поиска:</b> {filters_desc}\n"
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
                text="▶️ Запустить общий поиск" if not user.get("is_active") else "⏸ Поставить общий поиск на паузу", 
                callback_data="toggle_global_active"
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
        "Отправьте значение куки <code>hhtoken</code> из браузера:",
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

# --- Множественные фильтры поиска вакансий ---
def build_filters_keyboard(filters: list) -> InlineKeyboardMarkup:
    buttons = []
    for f in filters:
        f_id = f["id"]
        status_icon = "🟢" if f.get("is_active") else "⏸"
        name = f.get("name", "Фильтр")
        buttons.append([
            InlineKeyboardButton(text=f"{status_icon} {name}", callback_data=f"toggle_filter:{f_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_filter:{f_id}")
        ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить новый фильтр", callback_data="add_filter_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(F.text == "🎯 Мои фильтры поиска")
@dp.message(F.text == "🔍 Поиск вакансий")
@dp.message(Command("filters"))
@dp.message(Command("search"))
async def cmd_filters(message: types.Message, state: FSMContext):
    await state.clear()
    filters = await db.get_user_filters(message.chat.id)

    if not filters:
        text = (
            "🎯 <b>У вас пока нет сохраненных фильтров поиска.</b>\n\n"
            "Вы можете настроить несколько независимых фильтров (например: «Python Удаленка», «Team Lead», «Golang»).\n"
            "Бот будет параллельно отслеживать каждый из них!\n\n"
            "Нажмите кнопку ниже, чтобы добавить ваш первый фильтр:"
        )
    else:
        text = (
            f"🎯 <b>Ваши фильтры поиска (всего: {len(filters)}):</b>\n\n"
            "Нажмите на статус (🟢/⏸), чтобы включить/приостановить фильтр, или добавьте еще один:"
        )

    await message.answer(text, reply_markup=build_filters_keyboard(filters), parse_mode="HTML")

@dp.callback_query(F.data == "add_filter_start")
async def cb_add_filter_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(FilterStates.waiting_for_name)
    await callback.message.answer(
        "🏷 <b>Шаг 1 из 2:</b> Введите понятное название для фильтра.\n\n"
        "Например: <code>Python Удаленка</code> или <code>Руководитель проектов</code>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(FilterStates.waiting_for_name)
async def process_filter_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое. Введите понятное имя для фильтра:")
        return

    await state.update_data(filter_name=name)
    await state.set_state(FilterStates.waiting_for_url)
    await message.answer(
        f"🔗 <b>Шаг 2 из 2 для «{name}»:</b>\n"
        "Откройте [hh.ru](https://hh.ru), выставите нужные параметры и пришлите сюда ссылку на поиск:\n\n"
        "Пример: <code>https://hh.ru/search/vacancy?text=Python&salary=200000...</code>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@dp.message(FilterStates.waiting_for_url)
async def process_filter_url(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if "hh.ru" not in url:
        await message.answer("⚠️ Ссылка должна вести на сайт hh.ru. Попробуйте еще раз:")
        return

    data = await state.get_data()
    name = data.get("filter_name", "Новый фильтр")

    await db.add_filter(message.chat.id, name=name, url=url)
    await db.update_user(message.chat.id, is_active=1)
    await state.clear()

    filters = await db.get_user_filters(message.chat.id)
    await message.answer(
        f"✅ <b>Фильтр «{name}» успешно добавлен и активирован!</b>\n"
        "Бот сразу включил мониторинг новых вакансий по нему.",
        reply_markup=build_filters_keyboard(filters),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("toggle_filter:"))
async def cb_toggle_filter(callback: types.CallbackQuery):
    f_id = int(callback.data.split(":")[1])
    new_state = await db.toggle_filter(f_id, callback.message.chat.id)
    status_msg = "🟢 Фильтр включен" if new_state else "⏸ Фильтр на паузе"
    await callback.answer(status_msg)

    filters = await db.get_user_filters(callback.message.chat.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=build_filters_keyboard(filters))
    except Exception:
        pass

@dp.callback_query(F.data.startswith("del_filter:"))
async def cb_del_filter(callback: types.CallbackQuery):
    f_id = int(callback.data.split(":")[1])
    await db.delete_filter(f_id, callback.message.chat.id)
    await callback.answer("🗑 Фильтр удален")

    filters = await db.get_user_filters(callback.message.chat.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=build_filters_keyboard(filters))
    except Exception:
        pass

@dp.callback_query(F.data == "toggle_global_active")
async def cb_toggle_global(callback: types.CallbackQuery):
    user = await db.get_or_create_user(callback.message.chat.id)
    new_state = 0 if user.get("is_active") else 1
    await db.update_user(callback.message.chat.id, is_active=new_state)

    status_str = "🟢 Мониторинг запущен!" if new_state else "⏸ Мониторинг приостановлен."
    await callback.message.answer(status_str)
    await callback.answer()

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
    filters = await db.get_user_filters(message.chat.id)
    active_filters = [f for f in filters if f.get("is_active")]
    if not active_filters:
        await message.answer("⚠️ У вас нет активных фильтров поиска. Добавьте их в «🎯 Мои фильтры поиска».")
        return

    wait_msg = await message.answer(f"🔎 Проверяю {len(active_filters)} активных фильтров на HH.ru...")
    user = await db.get_or_create_user(message.chat.id)
    for f in active_filters:
        item = {
            "chat_id": message.chat.id,
            "filter_id": f["id"],
            "filter_name": f["name"],
            "search_url": f["url"],
            "resume_text": user.get("resume_text"),
            "custom_prompt": user.get("custom_prompt"),
            "ai_key": user.get("ai_key")
        }
        await check_single_filter(bot, item)
    
    await wait_msg.delete()
    await message.answer("🏁 Проверка завершена! Если появились новые вакансии, они отправлены выше.")

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
        "(Например: <i>'Сделай в 2 раза короче'</i> или <i>'Добавь больше уверенности и сделай упор на опыт с Docker'</i>):",
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
