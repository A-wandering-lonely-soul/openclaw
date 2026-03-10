FROM python:3.11-slim

WORKDIR /app

COPY feishu_bot.py /app/feishu_bot.py

RUN pip install --no-cache-dir requests flask pycryptodome

ENV FEISHU_APP_ID=""
ENV FEISHU_APP_SECRET=""
ENV FEISHU_VERIFICATION_TOKEN=""
ENV FEISHU_ENCRYPT_KEY=""
ENV OPENCLAW_URL="http://openclaw:8000"

EXPOSE 8002

CMD ["python", "feishu_bot.py"]
