# OpenClaw

接入了 Telegram 和飞书的通知服务，支持通过 Docker 一键部署到 Linux 服务器。

---

## 快速部署（一键安装）

> **环境要求**：Linux 服务器（x86_64 / ARM64），推荐 Ubuntu 20.04+

```bash
# 1. 克隆项目
git clone https://github.com/A-wandering-lonely-soul/openclaw.git
cd openclaw

# 2. 运行一键安装脚本（自动安装 Docker 并启动服务）
bash install.sh
```

脚本首次运行时会自动创建 `.env` 配置文件，根据提示编辑后再次执行即可完成部署。

---

## 手动部署

### 1. 配置环境变量

```bash
cp .env.example .env
nano .env   # 填入 Telegram Bot Token 和飞书 Webhook 等配置
```

| 变量 | 说明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot 的 API Token |
| `TELEGRAM_CHAT_ID` | 接收消息的 Chat ID |
| `FEISHU_WEBHOOK_URL` | 飞书自定义机器人 Webhook 地址 |
| `FEISHU_SECRET` | 飞书 Webhook 签名密钥（可选） |
| `APP_PORT` | 服务监听端口（默认 `8080`） |
| `LOG_LEVEL` | 日志级别（默认 `INFO`） |
| `TZ` | 时区（默认 `Asia/Shanghai`） |

### 2. 构建并启动

```bash
docker compose up -d --build
```

### 3. 查看日志

```bash
docker compose logs -f
```

### 4. 停止服务

```bash
docker compose down
```

---

## 更新

```bash
git pull
bash install.sh   # 或 docker compose up -d --build
```

---

## 目录结构

```
openclaw/
├── app/              # 应用源码
│   └── main.py
├── logs/             # 运行日志（自动创建）
├── .env.example      # 配置模板
├── Dockerfile        # 容器定义
├── docker-compose.yml
├── install.sh        # 一键安装脚本
└── requirements.txt
```
