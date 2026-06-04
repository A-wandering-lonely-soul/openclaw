# OpenClaw

基于 GitHub Copilot API 的 AI 聊天机器人，支持 Telegram、飞书，以及微信官方 ClawBot（Beta）接入，通过 Docker 部署 + OpenClaw CLI 管理。
也可用仓库项目openclaw-web的前端作为界面（多加个nginx代理即可），接入了实时查股票和黄金价格的接口，很适合监控股市，并且这个前端项目对数据处理做了优化，可以将数据生成为图表结构，体验比原来md文档格式更好。

**主要功能：**
- 支持多模型切换（GitHub Copilot GPT-4.1 / gpt-4o 等、DeepSeek V3 / R1）
- 自动联网搜索（Tavily），问到实时信息时自动查询
- A股行情多通道查询（**Sina Finance 直连**优先，失败自动切换东方财富/AKShare 兜底；支持股票/指数/ETF）
- 网页端自选股列表**按用户持久化至 PostgreSQL**，多设备/多浏览器保持一致
- Skill 插件机制（第一版）：支持从本地目录加载 skill.json，动态扩展工具与系统提示
- 聊天上下文采用 Redis（热缓存）+ PostgreSQL（持久化）
- 支持 Telegram Bot、飞书机器人，以及微信官方 ClawBot（Beta）双层接入
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
├── docker-compose.nginx.yml
├── Caddyfile             # 独占 80/443 时使用的反向代理配置
├── Caddyfile.nginx       # 已有 nginx 时使用的 Caddy 配置
├── nginx/
│   └── openclaw.conf.example
├── openclaw-box.sh       # 服务器管理面板脚本
├── deploy.sh             # 一键部署脚本
├── .env.example          # 环境变量模板
└── .env                  # 密钥配置（不提交到 git）
```

---

## 给 AI 助手的快速入口（推荐先读）

如果你在别的会话窗口让 AI 协助改代码，建议先让它读这一节：

- 核心对话入口是 `/api/chat`，主逻辑在 `openclaw/run_server.py`。
- 渠道按 `entry` 区分：`telegram`、`feishu`、`web_frontend`、`wechat_clawbot`。
- 模型策略默认是全局配置（`OPENAI_PROVIDER` / `OPENAI_MODEL`），微信桥接可单独覆盖：`WECHAT_BRIDGE_PROVIDER` / `WECHAT_BRIDGE_MODEL`。
- 微信桥接 API：
   - `GET /api/channel/wechat/status`：看桥接是否启用、token 是否配置、当前微信渠道模型。
   - `POST /api/channel/wechat/chat`：微信入口转发到后端，最终复用 `/api/chat` 主链路。
- 微信桥接鉴权：请求头 `X-Wechat-Bridge-Token`（或 `Authorization: Bearer <token>`）必须等于 `WECHAT_BRIDGE_TOKEN`。
- 微信去重：使用 `event_id` / `message_id`，TTL 由 `WECHAT_BRIDGE_DEDUP_TTL_SECONDS` 控制。

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

**Tushare Token（可选，推荐用于稳定 A 股行情）**
1. 打开 [tushare.pro](https://tushare.pro) 注册账号
2. 在个人中心获取 Token
3. 填入 `.env` 的 `TUSHARE_TOKEN=...`

**微信官方 ClawBot（Beta，可选，用于个人微信接入）**
1. 先安装官方 OpenClaw CLI：`npm install -g openclaw`
2. 初始化或修复本机配置：`openclaw configure`（也可以先用 `openclaw doctor --fix` 看是否有配置损坏）
3. 在配置向导里选择本机 Gateway、模型提供商、以及 `openclaw-weixin` 通道
4. 执行微信扫码登录：`openclaw channels login --channel openclaw-weixin`
5. 登录后用 `openclaw channels status --channel openclaw-weixin` 确认通道处于 `enabled / configured / running`
6. 如果通道或插件更新过，重启网关：`openclaw gateway restart --safe`
7. 如果你要让微信消息也走本仓库的行情、搜索和上下文优化，请启用后端桥接配置（见下方环境变量）

**微信桥接到本仓库后端（渠道独立模型）**

在 `.env`（或容器环境变量）中补充：

```dotenv
WECHAT_BRIDGE_ENABLED=1
WECHAT_BRIDGE_TOKEN=请填随机长token
WECHAT_BRIDGE_PROVIDER=deepseek
WECHAT_BRIDGE_MODEL=deepseek-chat
WECHAT_BRIDGE_DEDUP_TTL_SECONDS=600
```

说明：
- `WECHAT_BRIDGE_ENABLED=1` 才会开放 `/api/channel/wechat/chat`。
- 微信桥接模型独立于 Web/Telegram/飞书；不填 `WECHAT_BRIDGE_PROVIDER/MODEL` 时，回退到全局模型。
- 微信侧转发程序应调用 `POST /api/channel/wechat/chat`，并带 `X-Wechat-Bridge-Token`。
- 后端会自动把微信会话映射为 `chat_id=wechat_<id>`，并将 `entry` 固定为 `wechat_clawbot`。

**微信 ClawBot 的更新 / 回退 / 卸载**
1. 查看更新状态：`openclaw update status`
2. 更新到 Beta 渠道：`openclaw update --channel beta`
3. 更新后建议再执行：`openclaw gateway restart --safe`
4. 移除微信通道：`openclaw channels remove --channel openclaw-weixin --delete`
5. 如果仍有插件记录残留，先用 `openclaw plugins list` 找到插件 ID，再执行：`openclaw plugins uninstall <插件ID>`
6. 配置乱了或升级后出问题时，优先执行：`openclaw doctor --fix`

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

### 4. 一键部署

推荐直接运行一键脚本：

```bash
cd ~/openclaw
# 如果脚本在 Windows 上编辑过，先去除 Windows 换行符（\r）
sed -i 's/\r//' deploy.sh openclaw-box.sh
chmod +x deploy.sh
./deploy.sh
```

脚本会自动完成这些事情：
- 检测当前机器是否已有 nginx 或 80/443 是否被占用，并自动切换代理模式
- 自动创建 `.env`，按你的选择逐项询问需要的变量
- 只在你选择 Telegram 时询问 `TELEGRAM_BOT_TOKEN`
- 只在你选择飞书时询问飞书相关变量
- 只在你选择 DeepSeek 作为默认模型时询问 `DEEPSEEK_API_KEY`
- 只在你启用联网搜索时询问 `TAVILY_API_KEY`
- 只启动你选中的 Bot 服务，未选中的 Telegram/飞书容器不会启动
- **模式 B（nginx 前置）** 时，会根据 `NGINX_PROXY_PORT` 自动生成 `nginx/generated/openclaw-docker.conf`，并提示如何 `include` 到现有站点（可与静态前端并存，见下文「6. nginx 前置模式配置」）

脚本支持两种代理模式：

**模式 A：Caddy 直接对外监听 80/443**
- 适合服务器上没有其他 Web 服务的情况
- 自动申请和续期 HTTPS 证书

**模式 B：nginx 已经占用 80/443，OpenClaw 挂到现有 nginx 后面**
- 适合已经在服务器上运行 nginx 的情况
- 不需要停止 nginx
- Caddy 只监听本机 `127.0.0.1:8080` 或脚本提示的其他空闲端口
- 由 nginx 继续负责公网 80/443 和 TLS 证书

`.env` 模板可参考 [.env.example](.env.example)。

会话存储相关环境变量：
- `REDIS_URL`：Redis 连接串，默认 `redis://redis:6379/0`
- `REDIS_CHAT_TTL_SECONDS`：Redis 会话缓存 TTL，默认 `604800`（7 天）
- `POSTGRES_DSN`：PostgreSQL 连接串，默认 `postgresql://openclaw:openclaw@postgres:5432/openclaw`

说明：
- `/api/chat` 会把新消息同时写入 PostgreSQL，并写入 Redis 缓存。
- `/api/clear_context` 与 `/api/clear_context_by_entry` 会同时清理 PostgreSQL 与 Redis，行为与旧版保持一致（但不再依赖进程内存）。

> ⚠️ `.env` 已加入 `.gitignore`，不会被提交到 git，请勿手动分享此文件内容。
> ⚠️ 不要把 Token 粘贴到任何聊天窗口，包括和 AI 的对话。

### 5. 手动部署（高级用法）

如果你不想使用一键脚本，也可以手动创建 `.env` 后再执行 compose。

**模式 A：Caddy 直接对外提供 HTTPS**

```bash
cd ~/openclaw
docker compose build
docker compose up -d
```

**模式 B：保留 nginx，占用 80/443 的仍然是 nginx**

```bash
cd ~/openclaw
docker compose -f docker-compose.yml -f docker-compose.nginx.yml build
docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d
```

如果只启用 Telegram 或飞书，需要补上对应 profile：

```bash
# 只启用 Telegram
docker compose --profile telegram up -d

# 只启用飞书
docker compose --profile feishu up -d

# 同时启用 Telegram 和飞书
docker compose --profile telegram --profile feishu up -d
```

### 6. nginx 前置模式配置

模式 B 下，容器内 Caddy 只监听本机端口（默认 `127.0.0.1:8080`），由**宿主机 nginx** 对外提供 443，再反代到该端口。Caddy 会按路径分发：
- `/feishu/*` → `feishu_bot`
- 其余（含 `/api/*`）→ `openclaw`

**方式一：整站都交给 OpenClaw（无其它静态站）**

把 [nginx/openclaw.conf.example](nginx/openclaw.conf.example) 复制为站点配置，替换域名与证书路径；其中 `proxy_pass http://127.0.0.1:8080` 需与 `.env` 里 `NGINX_PROXY_PORT` 一致。

```bash
sudo cp ~/openclaw/nginx/openclaw.conf.example /etc/nginx/sites-available/openclaw.conf
# 编辑域名、证书路径、以及 8080 端口是否与 .env 一致
sudo ln -s /etc/nginx/sites-available/openclaw.conf /etc/nginx/sites-enabled/openclaw.conf
sudo nginx -t
sudo systemctl reload nginx
```

**方式二：同一域名上已有静态前端（如 `root .../dist`），只把 OpenClaw 路径让出来**

运行 `./deploy.sh` 选择模式 B 后，会生成 **`nginx/generated/openclaw-docker.conf`**（由 [nginx/openclaw-proxy-locations.snippet](nginx/openclaw-proxy-locations.snippet) 按端口展开）。在对外 **HTTPS** 的 `server { }` 里、在 `location /` 与静态 `root` 之前加入：

```nginx
include /etc/nginx/snippets/openclaw-docker.conf;
```

并把生成文件拷到系统目录（路径可自定，与 `include` 一致即可）：

```bash
sudo mkdir -p /etc/nginx/snippets
sudo cp ~/openclaw/nginx/generated/openclaw-docker.conf /etc/nginx/snippets/openclaw-docker.conf
sudo nginx -t && sudo systemctl reload nginx
```

片段中包含 **`/feishu/`**（飞书 Webhook）与 **`/api/`**（OpenClaw API），避免整站 `proxy_pass` 覆盖你的前端路由。

### 7. 验证服务状态

```bash
docker ps
```

已启用的相关容器均为 `Up` 状态即部署成功。未选择的 Telegram 或飞书容器不会出现。

### 7.1 微信 ClawBot（Beta）管理入口

微信官方通道不是 Docker 容器，它通过 OpenClaw CLI 的 `channels`、`plugins`、`gateway`、`update` 和 `doctor` 管理。

你可以把下面这些命令当成固定运维入口：

```bash
openclaw channels status --channel openclaw-weixin
openclaw channels login --channel openclaw-weixin
openclaw gateway status
openclaw gateway restart --safe
openclaw update status
openclaw update --channel beta
openclaw channels remove --channel openclaw-weixin --delete
openclaw plugins uninstall <插件ID>
openclaw doctor --fix
curl -s http://127.0.0.1:8000/api/channel/wechat/status
```

这些命令也已经放进 [openclaw-box.sh](openclaw-box.sh) 的新菜单项里，方便你后续一键检查、更新、卸载和修复。

### 8. 查看上下文存储（PostgreSQL / Redis）

先进入 PostgreSQL：

```bash
docker compose exec postgres psql -U openclaw -d openclaw
```

在 `psql` 中执行：

```sql
\dt
```

预期能看到三张表：
- `chat_sessions`
- `chat_messages`
- `user_watchlist`（网页端自选股，按用户隔离）

查看最近会话：

```sql
SELECT chat_id, entry, updated_at
FROM chat_sessions
ORDER BY updated_at DESC
LIMIT 20;
```

查看最近消息（上下文预览）：

```sql
SELECT id, chat_id, role, left(content::text, 200) AS content_preview, created_at
FROM chat_messages
ORDER BY id DESC
LIMIT 50;
```

只看网页端会话（`entry = 'web_frontend'`）及消息条数：

```sql
SELECT s.chat_id, s.updated_at, COUNT(m.id) AS msg_count
FROM chat_sessions s
LEFT JOIN chat_messages m ON m.chat_id = s.chat_id
WHERE s.entry = 'web_frontend'
GROUP BY s.chat_id, s.updated_at
ORDER BY s.updated_at DESC;
```

查看某个会话的完整上下文（按时间升序）：

```sql
SELECT id, role, content, created_at
FROM chat_messages
WHERE chat_id = 'CHAT_ID'
ORDER BY id ASC;
```

退出 PostgreSQL：

```sql
\q
```

查看 Redis 会话缓存：

```bash
docker compose exec redis redis-cli --scan --pattern "openclaw:chat:history:*"
```

查看某个会话缓存长度：

```bash
docker compose exec redis redis-cli LLEN openclaw:chat:history:CHAT_ID
```

说明：
- PostgreSQL 用于持久化，重启容器后数据仍在。
- Redis 用于热缓存，按 `REDIS_CHAT_TTL_SECONDS` 自动过期。

---

## Skill 插件（第一版）

### 目录与加载规则

- 后端会自动扫描以下目录中的 `*/skill.json`：
   - `/app/workspace/skills`（容器卷，推荐）
   - `/app/openclaw/skills`（随镜像代码）
- 可通过环境变量覆盖/追加：
   - `OPENCLAW_SKILLS_DIRS=/custom/skills1,/custom/skills2`

### skill.json 最小示例

```json
{
   "id": "demo_skill",
   "name": "Demo Skill",
   "enabled": true,
   "entry_allowlist": ["default", "web_frontend"],
   "system_prompt": "这里写给模型的补充规则。",
   "tools": [
      {
         "type": "function",
         "function": {
            "name": "your_tool_name",
            "description": "工具说明",
            "parameters": {
               "type": "object",
               "properties": {
                  "input": {"type": "string"}
               },
               "required": ["input"]
            }
         }
      }
   ],
   "tool_handlers": {
      "your_tool_name": "your_handler_key"
   }
}
```

说明：第一版为了安全，`tool_handlers` 只能映射到后端内置允许的 handler key（白名单），不会直接执行第三方任意 Python 代码。

### Skill 管理 API

- `GET /api/skills`：查看已扫描 Skill、可用工具、扫描目录
- `POST /api/skills/reload`：重新加载 Skill（新增/修改后调用）
- `POST /api/skills/toggle`：启用/禁用 Skill
   - body 示例：`{"id":"demo_skill","enabled":true}`
- `POST /api/skills/install`：通过 Git URL 在线安装公开 Skill（安装后自动 reload）
   - body 字段：
      - `git_url`（必填）：公开仓库 HTTPS 地址
      - `ref`（可选）：分支或 tag
      - `subdir`（可选）：仓库内 Skill 子目录（当仓库包含多个 skill 时建议填写）
      - `install_name`（可选）：本地安装目录名
      - `overwrite`（可选）：同名目录是否覆盖，默认 `false`
   - body 示例：`{"git_url":"https://github.com/owner/repo.git","ref":"main","subdir":"skills/my_skill","overwrite":true}`

### 接入“别人公开 Skill”的建议流程

1. 在管理界面或接口调用 `POST /api/skills/install`，填入公开 Git URL
2. 审核 `skill.json` 的 `tool_handlers` 是否在你允许范围内
3. 安装后会自动 reload，也可手动调用 `POST /api/skills/reload`
4. 用 `GET /api/skills` 检查工具是否已注册

> 安全建议：不要直接信任未知来源 Skill；先在测试环境启用并观察日志后再上生产。

---

## 网页前端（本地联调）

项目已支持独立网页前端，前端目录位于当前仓库同级目录：`../openclaw-web`。

当前版本说明：
- 不改动现有后端逻辑与 Telegram / 飞书功能
- 不接入当前仓库的 Docker Compose、Caddy 或 Nginx
- 仅新增独立 Vue 前端项目，用于本地开发和网页对话

### 前端能力

- 单页聊天界面，无登录页
- 多会话切换，按 `chat_id` 隔离上下文
- 查看当前模型并切换 provider / model
- 支持在 Web 端切换 provider：copilot / deepseek / ollama
- Ollama 在 Web 端默认仅展示低配推荐模型 `qwen2.5:3b`
- 若后端当前为 Ollama 重型模型，Web 端仅显示并禁用该选项，避免误选导致高负载/超时
- 清空当前会话上下文
- 删除单条会话时同步清理该 `chat_id` 的后端上下文
- 本地保存会话列表与消息记录

前端页面中的说明性文字已做精简（不再在界面重复展示架构说明），相关说明统一保留在文档中。

### 前端静态资源（背景图）

可将自定义背景图放在 `../openclaw-web/public/static/` 下，构建后可通过 `/static/*` 访问。
例如：`../openclaw-web/public/static/anime-bg.jpg`。

### 启动前端

先启动后端：

```bash
cd ~/openclaw
python openclaw/run_server.py
```

再启动前端：

```bash
cd ~/openclaw-web
npm install
npm run dev
```

默认访问地址：
- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

前端开发环境默认通过 Vite 代理把 `/api/*` 转发到 `http://localhost:8000`，因此不需要修改后端 CORS 配置。

### 前端环境变量

可参考 `../openclaw-web/.env.example`：

- `VITE_API_BASE_URL=/api`
- `VITE_API_PROXY_TARGET=http://localhost:8000`
- `VITE_REQUEST_TIMEOUT_MS=120000`

### 常见联调问题

**Q: 网页发送消息时报网络错误**
- 确认 `openclaw/run_server.py` 已启动并监听 `8000`
- 确认前端 `.env` 中的 `VITE_API_PROXY_TARGET` 指向正确后端地址

**Q: 前端能打开，但模型读取失败**
- 检查后端 `/api/get_model` 是否可访问
- 若后端需要的 Token 未配置，先按本文前面的部署准备补齐环境变量

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
 （1）清空当前会话上下文
 （2）清空所有人上下文
 （3）按入口清空上下文（Web/Telegram/飞书）
 （4）重启所有服务
 （5）停止所有服务
 （6）启动所有服务
 （7）查看应用日志（openclaw_service）
 （8）查看全部容器日志
 （9）清空日志
（10）切换模型
 （11）管理图片存储
 （12）重置配置（重新输入 Token 和域名）
 （13）卸载（停止并移除 Docker 容器）
 （14）微信 ClawBot（Beta）管理
 （0）退出
==============================
```

| 选项 | 功能说明 |
|------|----------|
| 1 | 按 `chat_id` 清空指定会话上下文（只影响该会话） |
| 2 | 清空所有用户/群聊会话上下文 |
| 3 | 按入口清空上下文（Web / Telegram / 飞书） |
| 4 | 重启当前已部署的服务容器 |
| 5 | 停止当前已部署的服务 |
| 6 | 启动当前已部署的服务 |
| 7 | 查看当前已部署 `openclaw_service` 的应用日志 |
| 8 | 查看全部容器日志 |
| 9 | 清空当前已部署容器的日志文件 |
| 10 | 切换 AI 模型（Copilot / DeepSeek），切换后建议同时执行选项（2）清空所有人上下文 |
| 11 | 管理图片存储（删除全部图片 / 按文件名删除） |
| 12 | 备份并删除 `.env`，重新运行部署向导重新输入 Token、域名等配置 |
| 13 | 停止并移除所有 OpenClaw Docker 容器，可选同时删除 `.env` |
| 14 | 微信 ClawBot（Beta）管理：查状态、重新扫码、看更新状态、执行 Beta 更新、移除通道、运行 doctor 修复、查看后端桥接状态 |

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

### 本地联调命令速查（后端 + 前端）

后端本地直跑（用于快速调试 API）：

```bash
cd ~/openclaw
python openclaw/run_server.py
```

前端本地开发（Vite）：

```bash
cd ~/openclaw-web
npm install
npm run dev
```

本地 Docker 联调（更接近生产）：

```bash
cd ~/openclaw
docker compose up -d --build openclaw redis postgres

# 需要联调 Telegram/飞书时按需启用
docker compose --profile telegram up -d --build telegram_bot
docker compose --profile feishu up -d --build feishu_bot
```

联调常用检查：

```bash
# 后端健康与模型状态
curl http://127.0.0.1:8000/api/get_model

# Skill 列表（确认是否加载）
curl http://127.0.0.1:8000/api/skills

# 关键日志
docker logs -f openclaw_service
docker logs -f telegram_bot
```

说明：
- 前端默认地址 `http://localhost:5173`，通过 Vite 代理访问后端 `/api/*`。
- Telegram 本地联调时避免和线上同时用同一 Bot Token（会抢 update）。
- Skill 在线安装依赖容器内 `git`，若安装失败先看 `openclaw_service` 日志。

```bash
# 查看实时日志
docker logs -f openclaw_service
docker logs -f telegram_bot
docker logs -f feishu_bot

# 运行一键部署脚本（推荐）
cd ~/openclaw
./deploy.sh

# 增量更新后端（仅重建 openclaw 服务，利用 BuildKit pip 缓存，速度较快）
cd ~/openclaw
git pull
docker compose build openclaw
docker compose up -d openclaw

# 增量更新前端静态资源（部署在 nginx 静态目录的情况）
cd ~/openclaw-web
git pull
npm install
npm run build
# 将 dist/ 内容复制到你的 nginx 静态目录（路径按实际配置替换）
sudo cp -r dist/* /var/www/openclaw-web/

# 完整重建后端（依赖变更或需清缓存时）
cd ~/openclaw
docker compose build --no-cache openclaw
docker compose up -d openclaw

# nginx 前置模式完整重建
cd ~/openclaw
docker compose -f docker-compose.yml -f docker-compose.nginx.yml build --no-cache
docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d

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

**Q: A股查询失败或返回过旧数据**
- 当前实现为三通道：优先 **Sina Finance 直连**（`hq.sinajs.cn`），失败自动切 `东方财富` -> `AKShare ETF 兜底`
- 建议使用 6 位代码（如 `000001`、`512660`），指数前缀规则：`000xxx`/`5/6xxxx` 对应上交所，`399xxx`/`0/1/2/3xxxx` 对应深交所
- 若 Sina Finance 被屏蔽，可在容器内自检东方财富：`docker exec -i openclaw_service curl -s 'https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=f43' | head -c 200`
- 若修改过 `run_server.py` 后未生效，执行：`docker compose restart openclaw`

**Q: 运行 `openclaw-box` 报错 `Permission denied`**
- 软链接目标文件没有执行权限，运行以下命令授权：
- `sudo chmod +x /usr/local/bin/openclaw-box`

**Q: 运行 deploy.sh 报错 `/usr/bin/env: 'bash\r': No such file or directory`**
- 脚本包含 Windows 换行符（CRLF），在 Linux 上无法执行
- 修复命令：`sed -i 's/\r//' ~/openclaw/deploy.sh ~/openclaw/openclaw-box.sh`

**Q: .env 修改后不生效**
- 可以直接重新运行 `./deploy.sh`，或手动执行 `docker compose up -d`
- 检查 .env 是否有 Windows 换行符：`cat -A .env`（行尾出现 `^M` 则有问题）
- 修复命令：`sed -i 's/\r//' .env`

**Q: 飞书事件订阅填写请求网址后验证失败**
- 确保服务已通过 `./deploy.sh` 或 `docker compose up -d` 启动
- 模式 A 下，确认 Caddyfile 中的域名与实际域名一致，且 HTTPS 证书已签发
- 模式 B 下，确认 nginx 已正确转发到 `127.0.0.1:8080`
- 可用 `curl https://lobsterpro.online/feishu/webhook` 测试端口连通性

**Q: 飞书机器人收到消息但不回复**
- 运行 `docker logs -f feishu_bot` 查看错误
- 检查 `.env` 中 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是否填写正确
- 检查飞书应用是否已开启 `im:message` 权限并发布上线

**Q: 网页端自选股换设备/浏览器后丢失**
- v2 之前自选股存在 localStorage，换设备即丢失
- 当前版本已改为存储在 PostgreSQL（`user_watchlist` 表），与账号绑定，多端共享
- 旧版 localStorage 数据不会自动迁移；首次登录时若数据库无记录，会加载默认板块 ETF

**Q: 更新后自选股 API 报 404**
- 后端服务需重启以加载新代码：`docker compose up -d openclaw`
- `user_watchlist` 表由 `init_storage()` 自动创建（`CREATE TABLE IF NOT EXISTS`），无需手动执行 SQL
