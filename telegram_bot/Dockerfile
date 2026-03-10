FROM python:3.11-slim

WORKDIR /app

COPY telegram_bot.py /app/telegram_bot.py

RUN pip install --no-cache-dir requests "python-telegram-bot>=20.0"

ENV TELEGRAM_BOT_TOKEN=""
ENV OPENCLAW_URL="http://openclaw:8000"

CMD ["python", "telegram_bot.py"]