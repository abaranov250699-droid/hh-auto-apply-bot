FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Отключаем буферизацию вывода Python
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание папки данных
RUN mkdir -p /app/data/screenshots

EXPOSE 10000

CMD ["python", "main.py"]
