# OpenClaw

基于 GitHub Copilot API 的 AI 聊天机器人，支持 Telegram 和飞书，通过 Docker 部署。

**主要功能：**
- 支持多模型切换（GitHub Copilot GPT-4.1 / gpt-4o 等、DeepSeek V3 / R1）
- 自动联网搜索（Tavily），问到实时信息时自动查询
- 保留对话历史上下文，支持多用户并发
- 支持 Telegram Bot 和飞书机器人双渠道接入
- 服务器管理面板 `openclaw-box`

---

## 项目结构

```
openclaw/
├── openclaw/             # Flask API 服务
│   ├── run_server.py
│   └── requirements.txt
├── telegram_bot/         # Telegram Bot 服务
│   ├── telegram_bot.py
│   └── Dockerfile
├── feishu_bot/           # 飞书机器人服务
│   ├── feishu_bot.py
│   └── Dockerfile
├── Dockerfile            # openclaw 服务镜像
├── docker-compose.yml
├── Caddyfile             # 反向代理配置
├── openclaw-box.sh       # 服务器管理面板脚本
└── .env                  # 密钥配置（不提交到 git）
```

---

## 部署准备

### 1. 获取所需 Token

**GitHub Token（用于调用 Copilot 模型）**
1. 打开 [github.com/settings/tokens](https://github.com/settings/tokens)
2. 点击 **Generate new token (classic)**
3. 勾选 `copilot` 或 `user` 权限范围
4. 生成并复制 Token（格式：`github_pat_...`）
5. 需要订阅 **GitHub Copilot Pro** 才能免费使用

**Telegram Bot Token**
1. 打开 Telegram，搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot`，按提示创建机器人
3. 复制 Bot Token（格式：`1234567890:AAG...`）

**飞书机器人（可选，用于飞书接入）**
1. 打开 [open.feishu.cn](https://open.feishu.cn) 并登录
2. 点击**创建企业自建应用**，填写名称和描述
3. 进入应用页面 → **凭证与基础信息**，复制 `App ID` 和 `App Secret`
4. 进入**添加应用能力** → 选择**机器人**并启用
5. 进入**权限管理**，开启以下权限：
   - `im:message`（接收和发送消息）
   - `im:message.group_at_msg`（接收群组 @ 消息）
6. 进入**事件订阅** → 填写请求网址：`https://lobsterpro.online/feishu/webhook`
   - 页面会发起验证请求，服务启动后即自动通过
7. 在**事件订阅**页面添加事件：`im.message.receive_v1`（接收消息）
8. 记录页面上的**验证 Token**（填入 `FEISHU_VERIFICATION_TOKEN`）
9. 如需消息加密，开启**加密策略**并记录 **Encrypt Key**（填入 `FEISHU_ENCRYPT_KEY`）
10. 发布应用版本并上线

**DeepSeek API Key（可选，用于 DeepSeek 模型）**
1. 打开 [platform.deepseek.com](https://platform.deepseek.com) 注册
2. 充值后创建 API Key

**Tavily API Key（可选，用于联网搜索）**
1. 打开 [app.tavily.com](https://app.tavily.com) 注册（免费，每月 1000 次）
2. 复制 API Key（格式：`tvly-...`）

---

### 2. 服务器环境要求

- Ubuntu 20.04 / 22.04
- Docker & Docker Compose

安装 Docker（如未安装）：
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 退出重新登录后生效
```

---

### 3. 拉取项目代码

```bash
git clone https://github.com/A-wandering-lonely-soul/openclaw.git ~/openclaw
cd ~/openclaw
```

---

### 4. 配置密钥

在项目根目录创建 `.env` 文件（**使用 printf 避免 Windows 换行符问题**）：

```bash
printf "DOMAIN=你的域名\nGITHUB_TOKEN=github_pat_你的token\nTELEGRAM_BOT_TOKEN=你的BotToken\nDEEPSEEK_API_KEY=\nTAVILY_API_KEY=tvly-你的key\n" > ~/openclaw/.env
```

`.env` 文件内容示例：
```
DOMAIN=lobsterpro.online
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAGxxxxxxxxxxxxxx
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx          # 可选，飞书接入
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx          # 可选，飞书接入
FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxxxxxx  # 可选，飞书事件验证
FEISHU_ENCRYPT_KEY=                         # 可选，飞书消息加密
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx        # 可选
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx        # 可选，联网搜索
```

> ⚠️ `.env` 已加入 `.gitignore`，不会被提交到 git，请勿手动分享此文件内容。
> ⚠️ 不要把 Token 粘贴到任何聊天窗口，包括和 AI 的对话。

---

### 5. 构建并启动服务

```bash
cd ~/openclaw
docker-compose build
docker-compose up -d
```

验证服务状态：
```bash
docker ps
```

两个容器均为 `Up` 状态即部署成功：
```
openclaw_service   Up   0.0.0.0:8000->8000/tcp
telegram_bot       Up
feishu_bot         Up
```

---

## 使用方式

### Telegram

打开 Telegram，找到你的 Bot，发送任意消息即可与 AI 对话。Bot 会保留每个用户的对话历史上下文。

### 飞书

- **私聊**：直接向机器人发送消息
- **群聊**：将机器人拉入群组，发消息时 @ 机器人即可触发回复

**联网搜索（自动触发）**

消息中包含以下关键词时，Bot 会自动调用 Tavily 搜索并将结果注入上下文：

> 最新、现在、今天、今年、最近、目前、实时、新闻、天气、股价、汇率、价格、比赛、比分 等

无需任何额外操作，普通问题不会触发搜索，不消耗搜索次数。

---

## 管理面板（服务器端）

在服务器上安装管理命令（只需执行一次）：

```bash
chmod +x ~/openclaw/openclaw-box.sh
sudo ln -s ~/openclaw/openclaw-box.sh /usr/local/bin/openclaw-box
```

之后在服务器任意目录执行：

```bash
openclaw-box
```

弹出交互菜单：

```
==============================
   OpenClaw 管理面板
   当前模型: [copilot] gpt-4.1
==============================
  1) 清空对话上下文
  2) 重启所有服务
  3) 停止所有服务
  4) 启动所有服务
  5) 查看日志
  6) 清空日志
  7) 切换模型
  0) 退出
==============================
```

| 选项 | 功能说明 |
|------|----------|
| 1 | 清空所有用户的 AI 对话历史，下次对话重新开始 |
| 2 | 重启 openclaw_service 和 telegram_bot 容器 |
| 3 | 停止所有服务 |
| 4 | 启动所有服务 |
| 5 | 查看两个容器最近 50 行日志 |
| 6 | 清空两个容器的日志文件 |
| 7 | 切换 AI 模型（Copilot / DeepSeek），**切换后建议同时执行选项 1 清空上下文** |

### 可用模型

**GitHub Copilot**（需要 `GITHUB_TOKEN`）

| 模型 | 说明 |
|------|------|
| gpt-4.1 | GPT-4.1，默认 |
| gpt-4o | GPT-4o，支持图片输入 |
| gpt-4o-mini | GPT-4o Mini，轻量快速 |
| o3-mini | o3 Mini，擅长推理 |
| o1-mini | o1 Mini，擅长推理 |

**DeepSeek**（需要 `DEEPSEEK_API_KEY`，在 [platform.deepseek.com](https://platform.deepseek.com) 注册）

| 模型 | 说明 |
|------|------|
| deepseek-chat | DeepSeek V3，通用对话 |
| deepseek-reasoner | DeepSeek R1，深度推理 |

---

## 常用运维命令

```bash
# 查看实时日志
docker logs -f openclaw_service
docker logs -f telegram_bot
docker logs -f feishu_bot

# 重新部署（代码更新后，--no-cache 确保新代码生效）
cd ~/openclaw
docker-compose down
docker-compose build --no-cache openclaw
docker-compose up -d

# 查看容器状态
docker ps

# 验证当前模型
curl https://$(grep DOMAIN .env | cut -d= -f2)/api/get_model
```

---

## 常见问题

**Q: Bot 没有回复**
- 检查 `TELEGRAM_BOT_TOKEN` 是否正确且完整（token 不能截断）
- 运行 `docker logs -f telegram_bot` 查看错误

**Q: 显示"AI 服务出错"**
- 检查 `GITHUB_TOKEN` 是否有效且有 Copilot 权限
- 运行 `docker logs -f openclaw_service` 查看错误

**Q: 切换模型后 AI 还是用旧模型的人设回复**
- 切换模型后必须同时清空上下文（管理面板选项 1），否则旧对话历史会影响新模型

**Q: 联网搜索没有触发**
- 检查 `TAVILY_API_KEY` 是否已填入 `.env` 并重新部署（`build --no-cache`）
- 消息里需含有触发关键词（最新、今天、天气、新闻等）

**Q: .env 修改后不生效**
- 必须重启容器：`docker-compose down && docker-compose up -d`
- 检查 .env 是否有 Windows 换行符：`cat -A .env`（行尾出现 `^M` 则有问题）
- 修复命令：`sed -i 's/\r//' .env`

**Q: 飞书事件订阅填写请求网址后验证失败**
- 确保服务已通过 `docker-compose up -d` 启动
- 确认 Caddyfile 中的域名与实际域名一致，且 HTTPS 证书已签发
- 可用 `curl https://lobsterpro.online/feishu/webhook` 测试端口连通性

**Q: 飞书机器人收到消息但不回复**
- 运行 `docker logs -f feishu_bot` 查看错误
- 检查 `.env` 中 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是否填写正确
- 检查飞书应用是否已开启 `im:message` 权限并发布上线
