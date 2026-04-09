import os
import re
import json
import shlex
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import requests as http_requests
from openai import OpenAI
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

try:
    from tavily import TavilyClient
    _tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", "")) if os.getenv("TAVILY_API_KEY") else None
except ImportError:
    _tavily = None

app = Flask(__name__)
logger = logging.getLogger("openclaw")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")

PROVIDERS = {
    "copilot": {
        "base_url": "https://models.inference.ai.azure.com",
        "api_key_env": "GITHUB_TOKEN",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1"),
        "api_key_env": "OLLAMA_API_KEY",
        "default_api_key": "ollama",
    }
}

current_config = {
    "provider": os.getenv("OPENAI_PROVIDER", "copilot") if os.getenv("OPENAI_PROVIDER", "copilot") in PROVIDERS else "copilot",
    "model": os.getenv("OPENAI_MODEL", "gpt-4.1")
}

conversation_histories = {}
chat_id_to_entry = {}  # 映射：chat_id -> entry（用于按入口清空）

OLLAMA_HEAVY_MODELS = {"llama3.1:8b", "qwen2.5:7b-instruct", "gemma3:12b"}
OLLAMA_RECOMMENDED_MODELS = {"qwen2.5:3b"}

OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "256"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))

# 不支持 function calling 的模型，退回纯对话模式
TOOL_UNSUPPORTED_MODELS = {"o1-mini", "deepseek-reasoner"}
TOOL_UNSUPPORTED_PROVIDERS = {"ollama"}

WORKSPACE = "/app/workspace"
os.makedirs(WORKSPACE, exist_ok=True)
IMAGES_DIR = os.path.join(WORKSPACE, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)
APP_TIMEZONE = ZoneInfo("Asia/Shanghai")
APP_TIMEZONE_NAME = "Asia/Shanghai"

# 不支持 vision 图片输入的模型
VISION_UNSUPPORTED_MODELS = {"gpt-4.1", "o3-mini", "o1-mini", "deepseek-chat", "deepseek-reasoner"}

# ─── 工具定义（OpenAI Function Calling 格式）────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "在工作区目录（/app/workspace）执行 shell 命令，返回 stdout/stderr 输出。适合运行脚本、安装包、查看文件列表等操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 30", "default": 30}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入工作区内的文件（会覆盖已有内容）。适合创建脚本、配置文件等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于工作区的文件路径，例如 scripts/check_gold.py"},
                    "content": {"type": "string", "description": "文件内容"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内某个文件的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于工作区的文件路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "发起 HTTP GET 请求，返回响应内容。适合查询公开 API、获取网页数据等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "请求的 URL（仅支持 http/https）"},
                    "headers": {"type": "object", "description": "可选的请求头", "default": {}}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": "创建定时任务，按 cron 表达式定期在工作区执行指定命令。任务重启后自动恢复。若需执行完毕后主动推送结果给用户，请将 notify_chat_id 设为当前用户的 chat_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务唯一名称，用于管理该任务"},
                    "cron": {"type": "string", "description": "标准 5 段 cron 表达式，如 '*/30 * * * *' 表示每30分钟"},
                    "command": {"type": "string", "description": "要定期执行的 shell 命令"},
                    "description": {"type": "string", "description": "任务说明（可选）", "default": ""},
                    "notify_chat_id": {"type": "string", "description": "执行完毕后将结果推送到此 Telegram chat_id，填入当前用户的 chat_id 即可实现主动推送。留空则不推送。", "default": ""}
                },
                "required": ["name", "cron", "command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "列出所有已创建的定时任务及其状态。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_task",
            "description": "删除指定名称的定时任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的任务名称"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "查询A股实时股价和基本行情。支持输入股票代码（如 000001、600519）或股票名称（如 平安银行、贵州茅台）。优先使用 Tushare Pro，失败时自动切换到 AKShare/东方财富/腾讯兜底。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码（如 000001）或股票名称（如 贵州茅台）"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_gold_price",
            "description": "查询上海黄金交易所黄金现货价格。默认查询 Au99.99，也可指定其他品种如 Au100g、Au(T+D)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "黄金品种代码，默认 Au99.99，可选 Au100g、Au(T+D) 等", "default": "Au99.99"}
                },
                "required": []
            }
        }
    }
]

WEB_FRONTEND_ENTRY = "web_frontend"
WEB_FRONTEND_ALLOWED_TOOL_NAMES = {"schedule_task"}

# ─── 工具实现 ─────────────────────────────────────────────────────────────────

# 隔离环境：不将宿主机敏感环境变量传入子进程
_SAFE_ENV = {
    "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "HOME": WORKSPACE,
    "LANG": "en_US.UTF-8",
    "PYTHONIOENCODING": "utf-8",
}


def _safe_path(path: str) -> str:
    """解析工作区内路径，防止目录穿越。"""
    full = os.path.realpath(os.path.join(WORKSPACE, path))
    if not full.startswith(os.path.realpath(WORKSPACE)):
        raise ValueError("路径越界，只允许访问工作区目录内的文件")
    return full


def tool_shell_exec(command: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=min(timeout, 120),
            env=_SAFE_ENV,
        )
        output = ""
        if result.stdout:
            output += f"[stdout]\n{result.stdout}"
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output[:4000] if output else "(命令执行完毕，无输出)"
    except subprocess.TimeoutExpired:
        return f"命令执行超时（>{timeout}s）"
    except Exception as e:
        return f"执行失败: {e}"


def tool_write_file(path: str, content: str) -> str:
    try:
        full_path = _safe_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ 文件已写入: {path}"
    except Exception as e:
        return f"写入失败: {e}"


def tool_read_file_content(path: str) -> str:
    try:
        full_path = _safe_path(path)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content[:4000]
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except Exception as e:
        return f"读取失败: {e}"


def tool_http_get(url: str, headers: dict = None) -> str:
    # 仅允许 http/https，防止其他协议下的 SSRF
    if not re.match(r'^https?://', url, re.IGNORECASE):
        return "错误：只允许 http/https URL"
    try:
        resp = http_requests.get(url, headers=headers or {}, timeout=15)
        text = resp.text[:3000]
        return f"[HTTP {resp.status_code}]\n{text}"
    except Exception as e:
        return f"请求失败: {e}"


# ─── 定时任务调度器 ───────────────────────────────────────────────────────────

_scheduler = BackgroundScheduler(timezone=APP_TIMEZONE)
_scheduler.start()
TASKS_FILE = os.path.join(WORKSPACE, ".tasks.json")


def _load_tasks() -> dict:
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_tasks(tasks: dict):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def push_telegram_message(chat_id: str, text: str) -> bool:
    """通过 Telegram Bot API 主动推送消息给用户。"""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return True
    except Exception:
        return False


def _run_task_command(command: str, notify_chat_id: str = ""):
    logger.info("[task] run command=%r notify_chat_id=%r", command, notify_chat_id)
    try:
        result = subprocess.run(
            command, shell=True, cwd=WORKSPACE,
            capture_output=True, text=True, timeout=120, env=_SAFE_ENV,
        )
        output = (result.stdout or "").strip()
        if result.stderr and result.stderr.strip():
            output += f"\n[stderr]\n{result.stderr.strip()}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        logger.info("[task] done command=%r output=%r", command, output[:200])
        if notify_chat_id:
            # notify_chat_id 必须是 Telegram 数字 chat_id 才能推送
            if output:
                ok = push_telegram_message(notify_chat_id, output[:4000])
                if not ok:
                    logger.warning("[task] push failed, notify_chat_id=%r may not be a valid Telegram ID", notify_chat_id)
    except subprocess.TimeoutExpired:
        logger.error("[task] command timed out command=%r", command)
    except Exception:
        logger.exception("[task] unexpected error command=%r", command)


def _restore_tasks():
    """服务重启后从文件恢复定时任务。"""
    tasks = _load_tasks()
    for name, info in tasks.items():
        try:
            _scheduler.add_job(
                _run_task_command,
                CronTrigger.from_crontab(info["cron"], timezone=APP_TIMEZONE),
                id=name,
                args=[info["command"], info.get("notify_chat_id", "")],
                replace_existing=True,
            )
        except Exception as e:
            print(f"[scheduler] 恢复任务 {name} 失败: {e}")


def tool_schedule_task(name: str, cron: str, command: str, description: str = "", notify_chat_id: str = "") -> str:
    try:
        _scheduler.add_job(
            _run_task_command,
            CronTrigger.from_crontab(cron, timezone=APP_TIMEZONE),
            id=name,
            args=[command, notify_chat_id],
            replace_existing=True,
        )
        tasks = _load_tasks()
        tasks[name] = {"cron": cron, "command": command, "description": description, "notify_chat_id": notify_chat_id}
        _save_tasks(tasks)
        msg = f"✅ 定时任务 [{name}] 已创建\n  cron: {cron}\n  命令: {command}"
        if notify_chat_id:
            msg += "\n  执行结果将主动推送到你的 Telegram"
        return msg
    except Exception as e:
        return f"创建任务失败: {e}"


def _validate_web_schedule_command(command: str) -> tuple[bool, str]:
    """网页前端入口的定时命令安全校验，阻止高危系统特权操作。"""
    raw = (command or "").strip()
    if not raw:
        return False, "命令不能为空"
    if len(raw) > 500:
        return False, "命令过长"

    # 禁止特权提升与系统破坏命令
    banned_words = re.compile(
        r"\b(sudo|su\b|chmod|chown|mkfs|mount|umount|shutdown|reboot|halt|poweroff|"
        r"systemctl|service|apt|yum|dnf|apk|docker|podman|kubectl)\b",
        re.IGNORECASE,
    )
    if banned_words.search(raw):
        return False, "命令包含高风险系统操作，不允许通过网页入口执行"

    # 禁止向工作区外写入（检测 > 或 >> 后跟绝对路径且不是工作区）
    if re.search(r">+\s*/(?!app/workspace)", raw):
        return False, "不允许向工作区外写入文件"

    return True, ""


def tool_schedule_task_web(name: str, cron: str, command: str, description: str = "", notify_chat_id: str = "") -> str:
    ok, reason = _validate_web_schedule_command(command)
    if not ok:
        return f"创建任务失败: {reason}"
    return tool_schedule_task(name, cron, command, description, notify_chat_id)


def tool_list_tasks() -> str:
    tasks = _load_tasks()
    if not tasks:
        return "当前没有定时任务。"
    lines = ["当前定时任务列表:"]
    for name, info in tasks.items():
        job = _scheduler.get_job(name)
        if job and job.next_run_time:
            next_run_dt = job.next_run_time.astimezone(APP_TIMEZONE)
            next_run = f"{next_run_dt.strftime('%Y-%m-%d %H:%M:%S')} ({APP_TIMEZONE_NAME})"
        else:
            next_run = "未知"
        lines.append(f"\n  [{name}]")
        lines.append(f"    cron: {info['cron']}")
        lines.append(f"    命令: {info['command']}")
        lines.append(f"    下次执行: {next_run}")
        if info.get("description"):
            lines.append(f"    说明: {info['description']}")
    return "\n".join(lines)


def tool_remove_task(name: str) -> str:
    tasks = _load_tasks()
    if name not in tasks:
        return f"任务 [{name}] 不存在"
    try:
        _scheduler.remove_job(name)
    except Exception:
        pass
    del tasks[name]
    _save_tasks(tasks)
    return f"✅ 任务 [{name}] 已删除"


def tool_get_stock_price(symbol: str) -> str:
    import time

    def _safe_num(value, scale=1):
        if isinstance(value, (int, float)):
            return value / scale
        return value if value is not None else "N/A"

    def _normalize_price(value):
        """东方财富字段在不同场景下可能是 7.12 或 712，做自适应缩放。"""
        if not isinstance(value, (int, float)):
            return value if value is not None else "N/A"
        return value / 100 if value >= 100 else value

    def _normalize_pct(value):
        """涨跌幅字段可能是 0.35 或 35，做自适应缩放。"""
        if not isinstance(value, (int, float)):
            return value if value is not None else "N/A"
        return value / 100 if abs(value) > 20 else value

    def _format_result(
        name, code, latest, pct, open_p, pre_close, high, low, volume, amount, source,
        granularity="行情快照", data_time="N/A", fallback_note=""
    ):
        sign = "+" if isinstance(pct, (int, float)) and pct > 0 else ""
        query_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note_line = f"降级提示：{fallback_note}\n" if fallback_note else ""
        return (
            f"【{name}】{code}\n"
            f"查询时间：{query_time}\n"
            f"数据来源：{source}\n"
            f"数据时间：{data_time}\n"
            f"数据粒度：{granularity}\n"
            f"{note_line}"
            "数据说明：接近实时，非逐笔成交\n"
            f"最新价：{latest} 元\n"
            f"涨跌幅：{sign}{pct}%\n"
            f"今开：{open_p}  昨收：{pre_close}\n"
            f"最高：{high}  最低：{low}\n"
            f"成交量：{volume} 手  成交额：{amount} 元"
        )

    symbol = (symbol or "").strip()
    if not symbol:
        return "股价查询失败: symbol 不能为空"
    logger.info("[stock] query start symbol=%s", symbol)
    fallback_note = ""

    # 0) 主数据源：Tushare Pro（需要设置 TUSHARE_TOKEN）
    tushare_token = os.getenv("TUSHARE_TOKEN", "").strip()
    if tushare_token:
        logger.info("[stock] trying Tushare Pro")
        try:
            import tushare as ts
            ts.set_token(tushare_token)
            pro = ts.pro_api()

            # 仅当是 6 位代码时直接映射，否则走名称模糊匹配
            ts_code = None
            if re.fullmatch(r"\d{6}", symbol):
                ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
            else:
                basic_df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
                match = basic_df[basic_df["name"].str.contains(symbol, na=False)]
                if not match.empty:
                    ts_code = match.iloc[0]["ts_code"]

            if ts_code:
                logger.info("[stock] tushare resolved ts_code=%s", ts_code)
                code = str(ts_code).split(".")[0]
                name_df = pro.stock_basic(ts_code=ts_code, fields="name")
                name = name_df.iloc[0]["name"] if name_df is not None and not name_df.empty else code

                # 先尝试分钟级
                minute_err = None
                try:
                    quote_df = ts.pro_bar(ts_code=ts_code, asset="E", freq="1min", limit=5)
                    if quote_df is not None and not quote_df.empty:
                        row = quote_df.iloc[-1]
                        data_time = (
                            row.get("trade_time")
                            or row.get("datetime")
                            or row.get("trade_date")
                            or row.get("date")
                            or "N/A"
                        )
                        pre_close = row.get("pre_close", "N/A")
                        close = row.get("close", "N/A")
                        pct = "N/A"
                        if isinstance(close, (int, float)) and isinstance(pre_close, (int, float)) and pre_close:
                            pct = round((close - pre_close) / pre_close * 100, 2)
                        return _format_result(
                            name=name,
                            code=code,
                            latest=close,
                            pct=pct,
                            open_p=row.get("open", "N/A"),
                            pre_close=pre_close,
                            high=row.get("high", "N/A"),
                            low=row.get("low", "N/A"),
                            volume=row.get("vol", "N/A"),
                            amount=row.get("amount", "N/A"),
                            source="Tushare Pro",
                            granularity="1分钟K线",
                            data_time=str(data_time),
                        )
                    logger.warning("[stock] tushare 1min returned empty ts_code=%s", ts_code)
                except Exception:
                    minute_err = True
                    logger.exception("[stock] tushare 1min failed, fallback to daily ts_code=%s", ts_code)
                    fallback_note = "Tushare 分钟级接口不可用，已自动降级为日线（可能是接口次数受限）"

                # 分钟级失败则降级到日线（仍然优先使用 Tushare）
                try:
                    daily_df = pro.daily(ts_code=ts_code, limit=1)
                    if daily_df is not None and not daily_df.empty:
                        row = daily_df.iloc[0]
                        pre_close = row.get("pre_close", "N/A")
                        close = row.get("close", "N/A")
                        pct = "N/A"
                        if isinstance(close, (int, float)) and isinstance(pre_close, (int, float)) and pre_close:
                            pct = round((close - pre_close) / pre_close * 100, 2)
                        return _format_result(
                            name=name,
                            code=code,
                            latest=close,
                            pct=pct,
                            open_p=row.get("open", "N/A"),
                            pre_close=pre_close,
                            high=row.get("high", "N/A"),
                            low=row.get("low", "N/A"),
                            volume=row.get("vol", "N/A"),
                            amount=row.get("amount", "N/A"),
                            source="Tushare Pro",
                            granularity="日线收盘（降级）",
                            data_time=str(row.get("trade_date", "N/A")),
                            fallback_note=fallback_note,
                        )
                    logger.warning("[stock] tushare daily returned empty ts_code=%s", ts_code)
                    if minute_err:
                        fallback_note = "Tushare 分钟级失败且日线为空，已切换到备用数据源（可能是接口次数受限）"
                except Exception:
                    logger.exception("[stock] tushare daily fallback failed ts_code=%s", ts_code)
                    if minute_err:
                        fallback_note = "Tushare 分钟级/日线均失败，已切换到备用数据源（可能是接口次数受限）"
            else:
                logger.warning("[stock] tushare could not resolve symbol=%s", symbol)
        except Exception:
            logger.exception("[stock] tushare failed symbol=%s", symbol)
    else:
        logger.warning("[stock] TUSHARE_TOKEN missing, skip tushare")

    # AKShare：保持为可选兜底，放到最后尝试（避免它在部分时段频繁失败抢占其它来源）

    # 2) 兜底：东方财富直连 API（服务器上更稳）
    try:
        logger.info("[stock] trying Eastmoney direct")
        if not re.fullmatch(r"\d{6}", symbol):
            return (
                f"无法用名称「{symbol}」走东方财富直连兜底。\n"
                "请改用 6 位股票代码重试（如 600256）。"
            )

        secid = f"{'1' if symbol.startswith('6') else '0'}.{symbol}"
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": secid,
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170",
            "invt": "2",
            "fltt": "2",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        }

        last_err = None
        for _ in range(3):
            try:
                resp = http_requests.get(url, params=params, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json().get("data") or {}
                if not data:
                    raise ValueError("东方财富返回空数据")

                return _format_result(
                    name=data.get("f58", symbol),
                    code=data.get("f57", symbol),
                    latest=_normalize_price(data.get("f43")),
                    pct=_normalize_pct(data.get("f170")),
                    open_p=_normalize_price(data.get("f46")),
                    pre_close=_normalize_price(data.get("f60")),
                    high=_normalize_price(data.get("f44")),
                    low=_normalize_price(data.get("f45")),
                    volume=data.get("f47", "N/A"),
                    amount=data.get("f48", "N/A"),
                    source="东方财富直连",
                    granularity="行情快照",
                    data_time="N/A",
                    fallback_note=fallback_note,
                )
            except Exception as e:
                last_err = e
                logger.exception("[stock] Eastmoney direct attempt failed symbol=%s", symbol)
                time.sleep(0.6)

        # 3) 最后兜底：腾讯行情接口（无 key，字段较少）
        try:
            logger.info("[stock] trying Tencent direct")
            q_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
            t_url = f"https://qt.gtimg.cn/q={q_symbol}"
            t_resp = http_requests.get(
                t_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                timeout=10,
            )
            t_resp.raise_for_status()
            text = t_resp.content.decode("gbk", errors="ignore")
            parts = text.split("~")
            # 关键字段：name=1, code=2, latest=3, pre_close=4, open=5, volume=6, amount=37
            if len(parts) > 37 and parts[3]:
                latest = float(parts[3]) if parts[3] not in ("", "0") else "N/A"
                pre_close = float(parts[4]) if parts[4] not in ("", "0") else "N/A"
                open_p = float(parts[5]) if parts[5] not in ("", "0") else "N/A"
                pct = "N/A"
                if isinstance(latest, float) and isinstance(pre_close, float) and pre_close != 0:
                    pct = round((latest - pre_close) / pre_close * 100, 2)
                return _format_result(
                    name=parts[1] or symbol,
                    code=parts[2] or symbol,
                    latest=latest,
                    pct=pct,
                    open_p=open_p,
                    pre_close=pre_close,
                    high="N/A",
                    low="N/A",
                    volume=parts[6] if len(parts) > 6 else "N/A",
                    amount=parts[37] if len(parts) > 37 else "N/A",
                    source="腾讯行情直连",
                    granularity="行情快照",
                    data_time="N/A",
                    fallback_note=fallback_note,
                )
        except Exception:
            logger.exception("[stock] Tencent direct failed symbol=%s", symbol)

        # 4) 最后兜底：AKShare（支持代码和名称查询，但对源站更敏感，故放最后）
        try:
            for _ in range(2):
                try:
                    logger.info("[stock] trying AKShare (optional last resort)")
                    import akshare as ak
                    df = ak.stock_zh_a_spot_em()
                    result = df[df["代码"] == symbol]
                    if result.empty:
                        result = df[df["名称"].str.contains(symbol, na=False)]
                    if not result.empty:
                        row = result.iloc[0]
                        return _format_result(
                            name=row.get("名称", "N/A"),
                            code=row.get("代码", "N/A"),
                            latest=row.get("最新价", "N/A"),
                            pct=row.get("涨跌幅", "N/A"),
                            open_p=row.get("今开", "N/A"),
                            pre_close=row.get("昨收", "N/A"),
                            high=row.get("最高", "N/A"),
                            low=row.get("最低", "N/A"),
                            volume=row.get("成交量", "N/A"),
                            amount=row.get("成交额", "N/A"),
                            source="AKShare/东方财富",
                            granularity="行情快照",
                            data_time="N/A",
                            fallback_note=fallback_note,
                        )
                except Exception:
                    logger.exception("[stock] AKShare last-resort failed symbol=%s", symbol)
                    time.sleep(0.4)
        except Exception:
            logger.exception("[stock] AKShare optional block unexpected failure symbol=%s", symbol)

        logger.error("[stock] all sources failed symbol=%s last_err=%s", symbol, last_err)
        return f"股价查询失败: 东方财富直连重试后仍失败 ({last_err})"
    except Exception as e:
        logger.exception("[stock] unexpected failure symbol=%s", symbol)
        return f"股价查询失败: {e}"


def tool_get_gold_price(symbol: str = "Au99.99") -> str:
    import akshare as ak

    symbol = (symbol or "Au99.99").strip() or "Au99.99"
    logger.info("[gold] query start symbol=%s", symbol)

    def _pick(latest_obj, keys, default="N/A"):
        for k in keys:
            try:
                v = latest_obj.get(k, None)
            except Exception:
                v = None
            if v is None:
                continue
            # 去掉空字符串/占位符
            if isinstance(v, str) and not v.strip():
                continue
            return v
        return default

    def _to_float(v):
        if isinstance(v, (int, float)):
            return float(v)
        try:
            sv = str(v).strip()
            if sv in ("", "N/A"):
                return None
            return float(sv)
        except Exception:
            return None

    gold_realtime_err = None

    # 1) 优先：上海金交所分钟级/近实时现货报价（AKShare spot_quotations_sge）
    try:
        logger.info("[gold] trying spot_quotations_sge")
        df_rt = ak.spot_quotations_sge(symbol=symbol)
        if df_rt is not None and not df_rt.empty:
            latest = df_rt.iloc[-1]
            latest_price = _to_float(
                _pick(
                    latest,
                    [
                        "现价",
                        "最新价",
                        "价格",
                        "close",
                        "Close",
                        "现货价",
                        "现货价格",
                        "成交价",
                        "最新成交价",
                        "最新成交价格",
                    ],
                )
            )
            update_time = _pick(
                latest,
                [
                    "更新时间",
                    "更新",
                    "时间",
                    "datetime",
                    "date",
                    "last_update_time",
                    "lastUpdate",
                    "更新时间(北京时间)",
                ],
            )

            # 时间尽量输出原样，若取不到也不硬猜
            gran = "分钟级/近实时"
            if latest_price is not None:
                return (
                    f"【上海金交所现货 {symbol}】\n"
                    f"数据粒度：{gran}\n"
                    f"数据时间：{update_time}\n"
                    f"最新价：{latest_price} 元/克"
                )
            logger.warning(
                "[gold] spot_quotations_sge price parse failed (latest_price=None). columns=%s",
                list(df_rt.columns),
            )
        logger.warning("[gold] spot_quotations_sge returned empty or unparsable")
    except Exception as e:
        logger.exception("[gold] spot_quotations_sge failed")
        gold_realtime_err = f"spot_quotations_sge 异常：{type(e).__name__}: {e}"
        pass

    # 2) 备选：沪金期货实时参考
    for market_value in ["CF", "金交所", ""]:
        try:
            logger.info("[gold] trying futures_zh_spot (沪金, market=%s)", market_value)
            if market_value:
                df_rt2 = ak.futures_zh_spot(symbol="沪金", market=market_value)
            else:
                df_rt2 = ak.futures_zh_spot(symbol="沪金")
            if df_rt2 is not None and not df_rt2.empty:
                row = df_rt2.iloc[0]
                latest_price = row.get("最新价", row.get("price", "N/A"))
                change_val = row.get("涨跌", row.get("change", "N/A"))
                pct_val = row.get("涨跌幅", row.get("change_percent", "N/A"))
                return (
                    "【黄金实时参考（沪金期货）】\n"
                    "数据粒度：实时参考\n"
                    f"最新价：{latest_price} 元/克\n"
                    f"涨跌：{change_val}  涨跌幅：{pct_val}%"
                )
            logger.warning("[gold] futures_zh_spot empty (market=%s)", market_value)
        except Exception as e:
            logger.exception("[gold] futures_zh_spot failed (market=%s)", market_value)
            if not gold_realtime_err:
                gold_realtime_err = f"futures_zh_spot 异常：{type(e).__name__}: {e}"
        # 若已成功 return，则不会执行到这里
        pass

    # 3) 最后：SGE 现货日线收盘（历史，仅供参考）
    try:
        logger.info("[gold] trying spot_hist_sge (historical close)")
        df_spot = ak.spot_hist_sge(symbol=symbol)
        if df_spot is not None and not df_spot.empty:
            latest = df_spot.iloc[-1]
            date_val = str(latest.get("日期", latest.iloc[0]))
            close_val = latest.get("收盘价", latest.get("close", "N/A"))
            high_val = latest.get("最高价", latest.get("high", "N/A"))
            low_val = latest.get("最低价", latest.get("low", "N/A"))
            err_line = f"实时接口错误：{gold_realtime_err}\n\n" if gold_realtime_err else "\n"
            return (
                f"⚠️ 当前无法获取黄金实时行情。\n"
                f"下面仅返回历史收盘数据（不能当作“今天/现在”的金价）：\n"
                f"{err_line}"
                f"【上海金交所现货 {symbol}（历史收盘）】\n"
                f"数据日期：{date_val}\n"
                f"收盘价：{close_val} 元/克\n"
                f"最高：{high_val}  最低：{low_val} 元/克"
            )
    except Exception:
        logger.exception("[gold] spot_hist_sge failed")
        pass

    return "黄金价格查询失败：实时与历史数据源均不可用，请稍后重试。"


def tools_for_entry(entry: str) -> list[dict]:
    if entry == WEB_FRONTEND_ENTRY:
        return [
            t for t in TOOLS
            if t.get("function", {}).get("name") in WEB_FRONTEND_ALLOWED_TOOL_NAMES
        ]
    return TOOLS


def execute_tool(name: str, args: dict, entry: str = "") -> str:
    if entry == WEB_FRONTEND_ENTRY and name not in WEB_FRONTEND_ALLOWED_TOOL_NAMES:
        return "权限不足：网页入口仅允许创建定时任务。"

    if name == "shell_exec":
        return tool_shell_exec(args.get("command", ""), args.get("timeout", 30))
    elif name == "write_file":
        return tool_write_file(args.get("path", ""), args.get("content", ""))
    elif name == "read_file":
        return tool_read_file_content(args.get("path", ""))
    elif name == "http_get":
        return tool_http_get(args.get("url", ""), args.get("headers", {}))
    elif name == "schedule_task":
        if entry == WEB_FRONTEND_ENTRY:
            return tool_schedule_task_web(
                args.get("name", ""), args.get("cron", ""),
                args.get("command", ""), args.get("description", ""),
                args.get("notify_chat_id", "")
            )
        return tool_schedule_task(
            args.get("name", ""), args.get("cron", ""),
            args.get("command", ""), args.get("description", ""),
            args.get("notify_chat_id", "")
        )
    elif name == "list_tasks":
        return tool_list_tasks()
    elif name == "remove_task":
        return tool_remove_task(args.get("name", ""))
    elif name == "get_stock_price":
        return tool_get_stock_price(args.get("symbol", ""))
    elif name == "get_gold_price":
        return tool_get_gold_price(args.get("symbol", "Au99.99"))
    else:
        return f"未知工具: {name}"


# 恢复持久化的定时任务
_restore_tasks()

# ─── 系统提示词（动态，含当前 chat_id）──────────────────────────────────────

def build_system_prompt(chat_id: str, entry: str = "") -> str:
    now = datetime.now(APP_TIMEZONE)
    now_iso = now.isoformat(timespec="seconds")
    today = now.strftime("%Y-%m-%d")
    timezone_name = APP_TIMEZONE_NAME
    ollama_mode = current_config["provider"] == "ollama"

    tavily_line = (
        "- 实时搜索（后端自动）：当用户询问天气、新闻、价格等实时信息时，后端会自动用 Tavily 搜索引擎查询，"
        "并将搜索结果注入到用户消息的开头。你只需直接基于这些已注入的内容回答，"
        "不要说'我无法联网'，也不要自行用 http_get 重复查询同一问题。"
    ) if _tavily and not ollama_mode else ""

    web_mode = entry == WEB_FRONTEND_ENTRY

    if ollama_mode:
        principle_1 = "1. 当前为 Ollama 纯对话模式：只提供文本回答，不执行任何实际操作。"
        execution_rule = "2. 严禁调用或假设工具能力；严禁声称“已创建任务/已执行命令/已抓取数据”。"
        capability_lines = (
            "- 纯文本问答：基于现有上下文给出简洁、直接的回答。\n"
        )
        policy_lines = (
            "3. 不具备联网、定时任务、文件读写、命令执行能力；遇到这类请求必须明确说明限制。\n"
            "4. 涉及天气/新闻/价格等实时信息时，若无已提供的实时数据，必须明确不确定性，不能编造。\n"
            "5. 回答尽量简短，除非用户明确要求详细分析。\n"
            "6. 不要编造或猜测自己的能力范围；上面列出的就是你全部能力。\n"
            "7. 当用户询问“今天几号/当前日期”等时间问题时，优先基于上面的服务器时间回答，并明确日期。"
        )
    elif web_mode:
        principle_1 = "1. 用户说\"帮我做某事\"时，在能力范围内直接执行。"
        execution_rule = "2. 网页入口模式下，仅可调用 schedule_task 创建定时任务。"
        capability_lines = (
            "- schedule_task：创建 cron 定时任务（禁止特权命令，普通命令均可）\n"
        )
        policy_lines = (
            "3. 网页入口严格受限：只允许创建定时任务；禁止系统控制、禁止读写文件、禁止网络抓取。\n"
            "4. 定时命令禁止使用 sudo/systemctl/docker 等特权命令，普通 shell 命令均可。\n"
            "5. 网页入口无法收到推送通知，不要设置 notify_chat_id；如需在任务执行后收到通知，请通过 Telegram Bot 使用。\n"
            "6. cron 是周期性任务，无法做到“仅执行一次”。若用户说“X 分钟后提醒”，需换算到具体时分，"
            "告知用户任务将在该时刻及此后每天同一时刻重复触发，并询问是否接受后再创建。\n"
            "7. 遇到错误要基于工具输出解释原因并给出可执行修正方案。\n"
            "8. 任务完成后简洁告知用户任务名称、cron 表达式、下次执行时间（北京时间）。\n"
            "9. 不要编造或猜测自己的能力范围；上面列出的就是你全部能力。\n"
            "10. 所有定时任务时间一律按北京时间（Asia/Shanghai）解释与反馈。\n"
            "11. 当用户询问“今天几号/当前日期”等时间问题时，优先基于上面的服务器时间回答，并明确日期。"
        )
    else:
        principle_1 = "1. 用户说\"帮我做某事\"时，直接动手执行，不要只给文字建议。"
        execution_rule = "2. 需要运行脚本时，先用 write_file 写好，再用 shell_exec 执行。"
        capability_lines = (
            "- shell_exec：在 /app/workspace 目录执行 Shell 命令\n"
            "- write_file：在工作区创建/覆盖文件\n"
            "- read_file：读取工作区文件内容\n"
            "- http_get：发起 HTTP GET 请求（查询公开 API、抓取数据）\n"
            "- schedule_task：创建 cron 定时任务（服务重启后自动恢复）\n"
            "- list_tasks：列出所有定时任务\n"
            "- remove_task：删除定时任务\n"
            "- get_stock_price：查询A股实时股价（支持股票代码或名称，如\"600519\"或\"贵州茅台\"）\n"
            "- get_gold_price：查询上海金交所黄金现货价格（Au99.99 等品种）\n"
            "- 图片识别（vision）：用户发送图片时，图片内容会直接附在消息中，你可以直接分析和描述图片内容。"
        )
        policy_lines = (
            "3. 设置定时任务用 schedule_task，而不是直接修改 crontab。\n"
            "4. 如果用户希望收到定时任务的执行结果，在调用 schedule_task 时将 notify_chat_id 设为 "
            f"{chat_id}，系统会自动通过 Telegram 主动推送结果给用户。\n"
            "5. 遇到错误要查看输出、分析原因并尝试修复，不要直接放弃。\n"
            "6. 任务完成后简洁告知用户结果和下次执行时间等关键信息。\n"
            "7. 不要编造或猜测自己的能力范围；上面列出的就是你全部能力。\n"
            "8. 涉及价格/行情时，必须严格基于工具返回结果中的“数据日期/时间”表述；历史数据绝不能说成“今天”或“实时”。\n"
            "9. 当用户询问 A股/股票/黄金/金价 时，优先调用 get_stock_price 或 get_gold_price；不要仅根据搜索引擎摘要直接报价格。\n"
            "10. 所有定时任务时间一律按北京时间（Asia/Shanghai）解释与反馈。\n"
            "11. 当用户询问“今天几号/当前日期”等时间问题时，优先基于上面的服务器时间回答，并明确日期。"
        )
    return f"""你是 OpenClaw，一个运行在 Linux 服务器上、具备真实执行能力的 AI Agent。
当前用户的 chat_id 为：{chat_id}
当前服务器时间为：{now_iso}（时区：{timezone_name}，今天日期：{today}）

你拥有以下能力：
{capability_lines}
{tavily_line}

工作原则：
{principle_1}
{execution_rule}
{policy_lines}

## 结构化输出规范（Web 前端可视化）

当数据适合可视化时，在文字说明之后附加以下格式的代码块，前端会自动渲染：

**图表** — 适用于：多天天气、趋势走势、时序数据等
```chart
{{"type":"line","title":"图表标题","xAxis":["标签1","标签2"],"series":[{{"name":"系列名","data":[1,2,3]}}]}}
```
type 可选: `bar`（柱状图）、`line`（折线图）

**表格** — 适用于：对比数据、排行、汇总
```datatable
{{"headers":["列1","列2","列3"],"rows":[["a","b","c"],["d","e","f"]]}}
```

**流程/架构图** — 适用于：DDF、流程图、架构图
```mermaid
flowchart TD
    A[开始] --> B[结束]
```

规则：只在数据真实具体时输出可视化块，不要凭空伪造数据；普通回复不需要附加代码块。"""

# ─── 判断是否需要联网搜索 ─────────────────────────────────────────────────────

SEARCH_KEYWORDS = [
    r"最新", r"现在", r"今天", r"今年", r"最近", r"目前", r"实时",
    r"新闻", r"天气", r"股价", r"汇率", r"价格", r"比赛", r"比分",
    r"\d{4}年", r"latest", r"current", r"today", r"news", r"price",
]


def needs_search(text: str) -> bool:
    if not _tavily:
        return False
    for kw in SEARCH_KEYWORDS:
        if re.search(kw, text, re.IGNORECASE):
            return True
    return False


def is_market_quote_query(text: str) -> bool:
    keywords = [
        r"A股", r"股票", r"股价", r"上证", r"深证", r"创业板",
        r"黄金", r"金价", r"沪金", r"Au99\.99", r"\b\d{6}\b"
    ]
    for kw in keywords:
        if re.search(kw, text, re.IGNORECASE):
            return True
    return False


def is_weather_query(text: str) -> bool:
    return bool(re.search(r"天气|气温|温度|下雨|降雨|风力|空气质量|预报", text, re.IGNORECASE))


def resolve_weather_target_date(text: str):
    now = datetime.now().astimezone()
    offset = None
    label = ""

    if re.search(r"大后天", text):
        offset = 3
        label = "大后天"
    elif re.search(r"后天", text):
        offset = 2
        label = "后天"
    elif re.search(r"明天", text):
        offset = 1
        label = "明天"
    elif re.search(r"今天|今日", text):
        offset = 0
        label = "今天"

    if offset is None:
        return None

    target = now + timedelta(days=offset)
    return {
        "label": label,
        "date": target.strftime("%Y-%m-%d"),
    }


def do_search(query: str) -> str:
    try:
        result = _tavily.search(query=query, max_results=3, search_depth="basic")
        snippets = []
        for r in result.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")[:300]
            url = r.get("url", "")
            snippets.append(f"[{title}]({url})\n{content}")
        if snippets:
            return "\n\n".join(snippets)
    except Exception as e:
        return f"(搜索失败: {e})"
    return ""


def get_client():
    provider = PROVIDERS[current_config["provider"]]
    api_key = os.getenv(provider.get("api_key_env", ""), "")
    if not api_key:
        api_key = provider.get("default_api_key", "")
    if not api_key:
        api_key = "dummy"
    if current_config["provider"] == "ollama":
        return OpenAI(
            api_key=api_key,
            base_url=provider["base_url"],
            timeout=OLLAMA_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return OpenAI(
        api_key=api_key,
        base_url=provider["base_url"]
    )


def get_ollama_tags_url() -> str:
    base_url = PROVIDERS["ollama"]["base_url"].rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}/api/tags"


def fetch_ollama_models() -> set[str]:
    tags_url = get_ollama_tags_url()
    resp = http_requests.get(tags_url, timeout=5)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    models = set()
    for item in data.get("models", []):
        name = str(item.get("name", "")).strip()
        if name:
            models.add(name)
    return models


# ─── API 路由 ─────────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    prompt = data.get("prompt", "")
    chat_id = str(data.get("chat_id", "default"))
    entry = str(data.get("entry", "")).strip()

    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = []
    
    # 记录 chat_id 对应的 entry（用于按入口清空）
    if entry and chat_id not in chat_id_to_entry:
        chat_id_to_entry[chat_id] = entry

    history = conversation_histories[chat_id]

    ollama_mode = current_config["provider"] == "ollama"

    # 联网搜索增强
    user_content = prompt
    if not ollama_mode and needs_search(prompt) and not is_market_quote_query(prompt):
        search_query = prompt
        weather_guard = ""

        if is_weather_query(prompt):
            target = resolve_weather_target_date(prompt)
            if target:
                search_query = f"{prompt} {target['date']} 天气预报"
                weather_guard = (
                    f"时间约束：用户问的是{target['label']}（{target['date']}）的天气，"
                    f"必须按该日期回答；若检索内容不足以确认该日期，请明确说明不确定，"
                    f"不要把今天天气当作{target['label']}天气。\n\n"
                )

        search_result = do_search(search_query)
        if search_result:
            user_content = (
                f"以下是搜索引擎获取的实时信息，请基于这些信息回答用户问题：\n\n"
                f"{weather_guard}"
                f"{search_result}\n\n"
                f"用户问题：{prompt}"
            )

    # 处理图片（vision）
    images = data.get("images", [])
    vision_supported = current_config["model"] not in VISION_UNSUPPORTED_MODELS

    if ollama_mode and current_config["model"] in OLLAMA_HEAVY_MODELS:
        high_risk_request = len(prompt) > 200 or bool(images) or needs_search(prompt)
        if high_risk_request:
            warn_reply = (
                f"⚠️ 当前 Ollama 模型 [{current_config['model']}] 属于重型模型，在低配 CPU 服务器上很容易超时或占满资源。"
                "建议切换到 qwen2.5:3b 后再试（如需 llama3.2:3b 请先手动 ollama pull）；复杂/联网任务也建议改用 Copilot。"
            )
            history.append({"role": "assistant", "content": warn_reply})
            return jsonify({"response": warn_reply})

    user_msg_idx = len(history)
    if images and vision_supported:
        content_blocks = [{"type": "text", "text": user_content}]
        for img in images:
            b64 = img.get("data", "")
            if b64:
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                })
        history.append({"role": "user", "content": content_blocks})
    elif images and not vision_supported:
        history.append({"role": "user", "content": user_content})
        warn_reply = f"⚠️ 当前模型 [{current_config['model']}] 不支持图片识别，请切换到 gpt-4o 或 gpt-4o-mini 后再发图片。"
        history.append({"role": "assistant", "content": warn_reply})
        return jsonify({"response": warn_reply})
    else:
        history.append({"role": "user", "content": user_content})

    restore_prompt = f"[图片] {prompt}" if images else prompt
    use_tools = (
        current_config["provider"] not in TOOL_UNSUPPORTED_PROVIDERS
        and current_config["model"] not in TOOL_UNSUPPORTED_MODELS
    )

    try:
        client = get_client()
        system_prompt = build_system_prompt(chat_id, entry)
        messages = [{"role": "system", "content": system_prompt}] + history
        available_tools = tools_for_entry(entry)

        if not use_tools:
            # 不支持工具的模型，退回纯对话
            request_args = {
                "model": current_config["model"],
                "messages": messages,
            }
            if ollama_mode:
                request_args["max_tokens"] = OLLAMA_MAX_TOKENS
                request_args["temperature"] = OLLAMA_TEMPERATURE
            response = client.chat.completions.create(**request_args)
            reply = response.choices[0].message.content or ""
            history[user_msg_idx] = {"role": "user", "content": restore_prompt}
            history.append({"role": "assistant", "content": reply})
            return jsonify({"response": reply})

        # Agent 循环：最多 10 轮工具调用
        for _ in range(10):
            messages = [{"role": "system", "content": system_prompt}] + history
            request_args = {
                "model": current_config["model"],
                "messages": messages,
                "tools": available_tools,
                "tool_choice": "auto",
            }
            if ollama_mode:
                request_args["max_tokens"] = OLLAMA_MAX_TOKENS
                request_args["temperature"] = OLLAMA_TEMPERATURE
            response = client.chat.completions.create(**request_args)
            msg = response.choices[0].message

            if msg.tool_calls:
                # 将 assistant 工具调用消息存入历史
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                history.append(assistant_msg)

                # 执行每个工具并将结果存入历史
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    result = execute_tool(tc.function.name, args, entry)
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                # AI 返回最终文本，结束循环
                reply = msg.content or ""
                history[user_msg_idx] = {"role": "user", "content": restore_prompt}
                history.append({"role": "assistant", "content": reply})
                return jsonify({"response": reply})

        # 超过最大轮次
        reply = "⚠️ Agent 执行轮次已达上限，任务可能未完全完成，请重新描述或继续追问。"
        history.append({"role": "assistant", "content": reply})
        return jsonify({"response": reply})

    except Exception as e:
        # 回滚本次对话新增的所有消息
        del history[user_msg_idx:]
        return jsonify({"response": f"AI 服务出错: {e}"}), 500


@app.route("/api/clear_context", methods=["POST"])
def clear_context():
    data = request.json
    chat_id = str(data.get("chat_id", "default"))
    if chat_id == "all":
        conversation_histories.clear()
    else:
        conversation_histories.pop(chat_id, None)
    return jsonify({"status": "ok"})


@app.route("/api/images", methods=["GET"])
def list_images():
    """列出已保存的图片文件"""
    try:
        files = []
        if os.path.isdir(IMAGES_DIR):
            for name in sorted(os.listdir(IMAGES_DIR)):
                fpath = os.path.join(IMAGES_DIR, name)
                if os.path.isfile(fpath):
                    stat = os.stat(fpath)
                    files.append({
                        "name": name,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=APP_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
                    })
        total_size = sum(f["size"] for f in files)
        return jsonify({"files": files, "count": len(files), "total_size": total_size})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/images/delete", methods=["POST"])
def delete_images():
    """删除图片：target='all' 删除所有，target='filename.jpg' 删除指定文件"""
    data = request.json
    target = str(data.get("target", "")).strip()
    if not target:
        return jsonify({"error": "target 参数不能为空"}), 400

    deleted = []
    try:
        if target == "all":
            if os.path.isdir(IMAGES_DIR):
                for name in os.listdir(IMAGES_DIR):
                    fpath = os.path.join(IMAGES_DIR, name)
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        deleted.append(name)
        else:
            # 防止路径穿越攻击
            safe_name = os.path.basename(target)
            fpath = os.path.join(IMAGES_DIR, safe_name)
            if os.path.isfile(fpath):
                os.remove(fpath)
                deleted.append(safe_name)
            else:
                return jsonify({"error": f"文件不存在: {safe_name}"}), 404
        return jsonify({"status": "ok", "deleted": deleted, "count": len(deleted)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clear_context_by_entry", methods=["POST"])
def clear_context_by_entry():
    """按入口清空上下文：支持 web_frontend, telegram, feishu 或 all"""
    data = request.json
    target_entry = str(data.get("entry", "")).strip().lower()
    
    if not target_entry:
        return jsonify({"error": "entry 参数不能为空"}), 400
    
    if target_entry == "all":
        # 清空所有
        conversation_histories.clear()
        chat_id_to_entry.clear()
        cleared_count = 0
    elif target_entry in ["web_frontend", "telegram", "feishu"]:
        # 清空指定入口的会话
        to_delete = [cid for cid, ent in chat_id_to_entry.items() if ent == target_entry]
        cleared_count = len(to_delete)
        for cid in to_delete:
            conversation_histories.pop(cid, None)
            chat_id_to_entry.pop(cid, None)
    else:
        return jsonify({"error": f"不支持的 entry: {target_entry}，必须是 all/web_frontend/telegram/feishu"}), 400
    
    return jsonify({"status": "ok", "cleared_count": cleared_count, "cleared_entry": target_entry})


@app.route("/api/set_model", methods=["POST"])
def set_model():
    data = request.json
    provider = data.get("provider", "copilot")
    model = data.get("model", "gpt-4.1")
    if provider not in PROVIDERS:
        return jsonify({"status": "error", "message": f"未知 provider: {provider}"}), 400
    if provider == "ollama":
        try:
            available_models = fetch_ollama_models()
        except Exception as e:
            return jsonify({"status": "error", "message": f"无法连接 Ollama 服务，请检查 OLLAMA_BASE_URL 与 ollama 服务状态: {e}"}), 400
        if available_models and model not in available_models:
            preview = sorted(available_models)
            return jsonify({
                "status": "error",
                "message": f"Ollama 模型不存在: {model}。可用模型: {', '.join(preview)}。请先执行 ollama pull {model}。"
            }), 400
    current_config["provider"] = provider
    current_config["model"] = model
    return jsonify({"status": "ok", "provider": provider, "model": model})


@app.route("/api/get_model", methods=["GET"])
def get_model():
    return jsonify(current_config)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
