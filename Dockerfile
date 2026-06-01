FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

COPY openclaw/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    --retries 8 --timeout 180 -r requirements.txt

COPY openclaw /app/openclaw

RUN mkdir -p /app/workspace

ENV OPENAI_API_KEY="${OPENAI_API_KEY}"

EXPOSE 8000

CMD ["python", "openclaw/run_server.py"]