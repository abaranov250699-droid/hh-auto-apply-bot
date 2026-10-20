import os
from typing import Optional, Dict, Any
from google import genai
from google.genai import types
from config import GEMINI_API_KEY

def _get_client(user_key: Optional[str] = None) -> Optional[genai.Client]:
    key = user_key or GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    try:
        return genai.Client(api_key=key)
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")
        return None

async def generate_cover_letter(
    vacancy: Dict[str, Any],
    resume_text: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    user_ai_key: Optional[str] = None
) -> str:
    """
    Генерирует индивидуальное сопроводительное письмо на основе вакансии и резюме.
    """
    client = _get_client(user_ai_key)
    title = vacancy.get("title", "специалист")
    company = vacancy.get("company", "компания")
    desc = vacancy.get("description", "")[:2500]

    # Если API ключ настроен — используем Gemini
    if client:
        system_instruction = (
            "Ты — опытный карьерный консультант и профессиональный соискатель. "
            "Твоя цель: написать убедительное, вежливое, лаконичное и живое сопроводительное письмо на русском языке "
            "для отклика на вакансию на HH.ru.\n"
            "Правила:\n"
            "1. Не используй заезженные штампы вроде 'Я внимательный, стрессоустойчивый и коммуникабельный'.\n"
            "2. Пиши конкретно: покажи, почему кандидат подходит именно под стек и задачи этой компании.\n"
            "3. Объем: 2-3 коротких емких абзаца (максимум 150-200 слов), уважай время HR-менеджера.\n"
            "4. Включай призыв к диалогу в конце.\n"
            "5. Не придумывай несуществующий опыт, используй предоставленные данные резюме.\n"
        )
        if custom_prompt:
            system_instruction += f"\nДополнительные пожелания соискателя: {custom_prompt}"

        prompt = f"""
ВАКАНСИЯ:
- Должность: {title}
- Компания: {company}
- Описание и требования:
{desc}

РЕЗЮМЕ / ОПЫТ СОИСКАТЕЛЯ:
{resume_text if resume_text else 'Опытный специалист с профильным коммерческим опытом по указанному направлению.'}

Напиши готовый текст сопроводительного письма:
"""
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7,
                )
            )
            if response.text:
                return response.text.strip()
        except Exception as e:
            print(f"Gemini generation error: {e}")

    # Fallback: умный шаблон, если Gemini недоступен
    greeting = f"Здравствуйте!\n\nМеня заинтересовала вакансия {title}"
    if company and company != "Компания не указана":
        greeting += f" в команде {company}"
    greeting += ".\n\n"

    body = ""
    if resume_text:
        first_lines = "\n".join([line for line in resume_text.splitlines() if line.strip()][:3])
        body = f"Мой практический опыт и навыки хорошо соответствуют вашим требованиям:\n{first_lines}\n\n"
    else:
        body = "Обладаю необходимым коммерческим опытом и навыками для эффективного решения поставленных задач.\n\n"

    footer = "Буду рад обсудить подробности задач и ответить на ваши вопросы на интервью!\n\nС уважением."
    return greeting + body + footer

async def rewrite_cover_letter(
    previous_letter: str,
    instruction: str,
    user_ai_key: Optional[str] = None
) -> str:
    """
    Переписывает или корректирует письмо по инструкции пользователя через AI.
    """
    client = _get_client(user_ai_key)
    if not client:
        return previous_letter + f"\n\n[Правка: {instruction}]"

    prompt = f"""
Вот текущее сопроводительное письмо:
\"\"\"
{previous_letter}
\"\"\"

Пользователь просит внести следующие изменения:
\"{instruction}\"

Перепиши письмо с учетом этих пожеланий. Верни только готовый исправленный текст письма без лишних комментариев:
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.6,
            )
        )
        if response.text:
            return response.text.strip()
    except Exception as e:
        print(f"Gemini rewrite error: {e}")

    return previous_letter
