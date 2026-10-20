import aiosqlite
from typing import Optional, List, Dict, Any
from config import DB_PATH

async def init_db():
    """Инициализация таблиц базы данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                phone TEXT,
                user_name TEXT,
                search_url TEXT,
                resume_text TEXT,
                custom_prompt TEXT,
                ai_key TEXT,
                is_active INTEGER DEFAULT 0,
                check_interval INTEGER DEFAULT 300,
                last_check_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vacancies (
                vacancy_id TEXT,
                chat_id INTEGER,
                title TEXT,
                company TEXT,
                salary TEXT,
                url TEXT,
                description TEXT,
                cover_letter TEXT,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (vacancy_id, chat_id)
            )
        """)
        await db.commit()

async def get_or_create_user(chat_id: int) -> Dict[str, Any]:
    """Получить данные пользователя или создать новую запись."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        
        # Создаем нового пользователя
        await db.execute("INSERT INTO users (chat_id) VALUES (?)", (chat_id,))
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row)

async def update_user(chat_id: int, **kwargs):
    """Обновить настройки пользователя."""
    if not kwargs:
        return
    fields = []
    values = []
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(chat_id)
    query = f"UPDATE users SET {', '.join(fields)} WHERE chat_id = ?"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, tuple(values))
        await db.commit()

async def get_active_users() -> List[Dict[str, Any]]:
    """Получить список пользователей с активным мониторингом."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE is_active = 1 AND search_url IS NOT NULL AND search_url != ''"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def is_vacancy_seen(vacancy_id: str, chat_id: int) -> bool:
    """Проверить, видели ли мы уже эту вакансию."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM vacancies WHERE vacancy_id = ? AND chat_id = ?", 
            (vacancy_id, chat_id)
        ) as cursor:
            return (await cursor.fetchone()) is not None

async def save_vacancy(
    vacancy_id: str,
    chat_id: int,
    title: str,
    company: str,
    salary: str,
    url: str,
    description: str,
    cover_letter: str
):
    """Сохранить новую вакансию."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO vacancies 
            (vacancy_id, chat_id, title, company, salary, url, description, cover_letter, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (vacancy_id, chat_id, title, company, salary, url, description, cover_letter))
        await db.commit()

async def get_vacancy(vacancy_id: str, chat_id: int) -> Optional[Dict[str, Any]]:
    """Получить данные вакансии."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vacancies WHERE vacancy_id = ? AND chat_id = ?", 
            (vacancy_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_vacancy(vacancy_id: str, chat_id: int, **kwargs):
    """Обновить статус или сопроводительное письмо вакансии."""
    if not kwargs:
        return
    fields = []
    values = []
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(vacancy_id)
    values.append(chat_id)
    query = f"UPDATE vacancies SET {', '.join(fields)} WHERE vacancy_id = ? AND chat_id = ?"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, tuple(values))
        await db.commit()

async def get_user_stats(chat_id: int) -> Dict[str, int]:
    """Получить статистику по вакансиям пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) as applied,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) as skipped
            FROM vacancies WHERE chat_id = ?
        """, (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "total": row[0] or 0,
                    "applied": row[1] or 0,
                    "pending": row[2] or 0,
                    "skipped": row[3] or 0,
                }
            return {"total": 0, "applied": 0, "pending": 0, "skipped": 0}
