import asyncio
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from hh_browser import search_hh_vacancies, get_vacancy_description, is_user_authenticated
from ai_letter import generate_cover_letter

def get_vacancy_keyboard(vacancy_id: str) -> InlineKeyboardMarkup:
    """Создает кнопки для карточки вакансии."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Откликнуться", 
                    callback_data=f"apply:{vacancy_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать", 
                    callback_data=f"edit:{vacancy_id}"
                ),
                InlineKeyboardButton(
                    text="🤖 Переписать AI", 
                    callback_data=f"rewrite:{vacancy_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Пропустить", 
                    callback_data=f"skip:{vacancy_id}"
                )
            ]
        ]
    )

def format_vacancy_message(vacancy: dict, cover_letter: str) -> str:
    """Форматирует красивое сообщение о вакансии для Telegram."""
    title = vacancy.get("title", "Не указано")
    company = vacancy.get("company", "Не указана")
    salary = vacancy.get("salary", "Не указана")
    url = vacancy.get("url", "https://hh.ru")

    return (
        f"🔔 <b>Новая подходящая вакансия!</b>\n\n"
        f"💼 <b>Должность:</b> {title}\n"
        f"🏢 <b>Компания:</b> {company}\n"
        f"💰 <b>Зарплата:</b> {salary}\n"
        f"🔗 <a href='{url}'>Открыть на HH.ru</a>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Проект сопроводительного письма:</b>\n\n"
        f"<i>{cover_letter}</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Нажмите «Откликнуться» для мгновенной отправки или «Редактировать», чтобы изменить текст.</i>"
    )

async def check_user_vacancies(bot: Bot, user: dict):
    """Проверяет новые вакансии для конкретного пользователя."""
    chat_id = user["chat_id"]
    search_url = user.get("search_url")
    if not search_url:
        return

    try:
        found_vacancies = await search_hh_vacancies(search_url, limit=10)
        for vac in found_vacancies:
            vac_id = vac["id"]
            if await db.is_vacancy_seen(vac_id, chat_id):
                continue

            # Получаем подробное описание для качественного письма
            details = await get_vacancy_description(vac_id)
            vac["description"] = details.get("description", "")
            if not vac.get("title") and details.get("title"):
                vac["title"] = details["title"]
            if not vac.get("company") and details.get("company"):
                vac["company"] = details["company"]

            # Генерируем сопроводительное письмо
            letter = await generate_cover_letter(
                vacancy=vac,
                resume_text=user.get("resume_text"),
                custom_prompt=user.get("custom_prompt"),
                user_ai_key=user.get("ai_key")
            )

            # Сохраняем в базу данных
            await db.save_vacancy(
                vacancy_id=vac_id,
                chat_id=chat_id,
                title=vac["title"],
                company=vac["company"],
                salary=vac["salary"],
                url=vac["url"],
                description=vac.get("description", ""),
                cover_letter=letter
            )

            # Отправляем уведомление в Telegram с кнопками
            text = format_vacancy_message(vac, letter)
            kb = get_vacancy_keyboard(vac_id)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
                await asyncio.sleep(1)  # Защита от лимитов Telegram
            except Exception as e:
                print(f"Failed to send message to {chat_id}: {e}")

    except Exception as e:
        print(f"Error checking vacancies for {chat_id}: {e}")

async def start_monitoring_scheduler(bot: Bot):
    """Фоновый цикл проверки вакансий для всех активных пользователей."""
    while True:
        try:
            active_users = await db.get_active_users()
            for user in active_users:
                await check_user_vacancies(bot, user)
                await asyncio.sleep(2)
        except Exception as e:
            print(f"Scheduler loop error: {e}")

        # Пауза между итерациями (по умолчанию 60 сек, проверка каждых пользователей по очереди)
        await asyncio.sleep(60)
