import os
import re
import json
import subprocess
from datetime import datetime
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

PROVIDERS = {
    "copilot": {
        "base_url": "https://models.inference.ai.azure.com",
        "api_key_env": "GITHUB_TOKEN",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
    }
}

current_config = {
    "provider": os.getenv("OPENAI_PROVIDER", "copilot") if os.getenv("OPENAI_PROVIDER", "copilot") in PROVIDERS else "copilot",
    "model": os.getenv("OPENAI_MODEL", "gpt-4.1")
}

conversation_histories = {}

# 不支持 function calling 的模型，退回纯对话模式
TOOL_UNSUPPORTED_MODELS = {"o1-mini", "deepseek-reasoner"}

WORKSPACE = "/app/workspace"
os.makedirs(WORKSPACE, exist_ok=True)

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
            "description": "查询A股实时股价和基本行情。支持输入股票代码（如 000001、600519）或股票名称（如 平安银行、贵州茅台）。数据来自东方财富，交易时段内为实时行情。",
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

_scheduler = BackgroundScheduler()
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
    try:
        result = subprocess.run(
            command, shell=True, cwd=WORKSPACE,
            capture_output=True, text=True, timeout=120, env=_SAFE_ENV,
        )
        if notify_chat_id:
            output = (result.stdout or "").strip()
            if result.stderr and result.stderr.strip():
                output += f"\n[stderr]\n{result.stderr.strip()}"
            if output:
                push_telegram_message(notify_chat_id, output[:4000])
    except Exception:
        pass


def _restore_tasks():
    """服务重启后从文件恢复定时任务。"""
    tasks = _load_tasks()
    for name, info in tasks.items():
        try:
            _scheduler.add_job(
                _run_task_command,
                CronTrigger.from_crontab(info["cron"]),
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
            CronTrigger.from_crontab(cron),
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


def tool_list_tasks() -> str:
    tasks = _load_tasks()
    if not tasks:
        return "当前没有定时任务。"
    lines = ["当前定时任务列表:"]
    for name, info in tasks.items():
        job = _scheduler.get_job(name)
        next_run = str(job.next_run_time) if job and job.next_run_time else "未知"
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
    def _safe_num(value, scale=1):
        if isinstance(value, (int, float)):
            return value / scale
        return value if value is not None else "N/A"

    def _format_result(name, code, latest, pct, open_p, pre_close, high, low, volume, amount, source):
        sign = "+" if isinstance(pct, (int, float)) and pct > 0 else ""
        query_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"【{name}】{code}\n"
            f"查询时间：{query_time}\n"
            f"数据来源：{source}\n"
            "数据说明：行情快照（接近实时，非逐笔成交）\n"
            f"最新价：{latest} 元\n"
            f"涨跌幅：{sign}{pct}%\n"
            f"今开：{open_p}  昨收：{pre_close}\n"
            f"最高：{high}  最低：{low}\n"
            f"成交量：{volume} 手  成交额：{amount} 元"
        )

    symbol = (symbol or "").strip()
    if not symbol:
        return "股价查询失败: symbol 不能为空"

    # 1) 首选 AKShare（支持代码和名称查询）
    try:
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
            )
    except Exception:
        pass

    # 2) 兜底：东方财富直连 API（服务器上更稳）
    try:
        if not re.fullmatch(r"\d{6}", symbol):
            return (
                f"AKShare 查询失败，且无法用名称「{symbol}」走东方财富直连兜底。\n"
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
                    latest=_safe_num(data.get("f43"), 100),
                    pct=_safe_num(data.get("f170"), 100),
                    open_p=_safe_num(data.get("f46"), 100),
                    pre_close=_safe_num(data.get("f60"), 100),
                    high=_safe_num(data.get("f44"), 100),
                    low=_safe_num(data.get("f45"), 100),
                    volume=data.get("f47", "N/A"),
                    amount=data.get("f48", "N/A"),
                    source="东方财富直连",
                )
            except Exception as e:
                last_err = e
        return f"股价查询失败: 东方财富直连重试后仍失败 ({last_err})"
    except Exception as e:
        return f"股价查询失败: {e}"


def tool_get_gold_price(symbol: str = "Au99.99") -> str:
    import akshare as ak

    symbol = (symbol or "Au99.99").strip() or "Au99.99"
    today = datetime.now().date()

    realtime_text = ""
    stale_text = ""

    # 先尝试更实时的数据源：沪金行情
    try:
        df_rt = ak.futures_zh_spot(symbol="沪金", market="CF")
        if df_rt is not None and not df_rt.empty:
            row = df_rt.iloc[0]
            latest_price = row.get("最新价", row.get("price", "N/A"))
            change_val = row.get("涨跌", row.get("change", "N/A"))
            pct_val = row.get("涨跌幅", row.get("change_percent", "N/A"))
            realtime_text = (
                "【黄金实时参考（沪金）】\n"
                f"最新价：{latest_price} 元/克\n"
                f"涨跌：{change_val}  涨跌幅：{pct_val}%\n"
                "说明：该值来自期货实时行情，可作为“现在金价”参考。"
            )
    except Exception:
        pass

    # 再取 SGE 现货日线作为补充信息（严格标注日期，不可当作今日价）
    try:
        df_spot = ak.spot_hist_sge(symbol=symbol)
        if df_spot is not None and not df_spot.empty:
            latest = df_spot.iloc[-1]
            date_val = str(latest.get("日期", latest.iloc[0]))
            close_val = latest.get("收盘价", latest.get("close", "N/A"))
            high_val = latest.get("最高价", latest.get("high", "N/A"))
            low_val = latest.get("最低价", latest.get("low", "N/A"))
            spot_date = None
            try:
                spot_date = datetime.strptime(date_val[:10], "%Y-%m-%d").date()
            except Exception:
                spot_date = None
            age_hint = ""
            if spot_date is not None:
                diff_days = (today - spot_date).days
                if diff_days > 0:
                    age_hint = f"（距今天 {diff_days} 天）"
            stale_text = (
                f"【上海金交所现货 {symbol}（历史收盘）】\n"
                f"数据日期：{date_val}{age_hint}\n"
                f"收盘价：{close_val} 元/克\n"
                f"最高：{high_val}  最低：{low_val} 元/克"
            )
    except Exception:
        pass

    if realtime_text:
        return realtime_text + (f"\n\n{stale_text}" if stale_text else "")

    if stale_text:
        return (
            "⚠️ 当前无法获取黄金实时行情。\n"
            "下面仅返回历史收盘数据，不能当作“今天/现在”的金价：\n\n"
            f"{stale_text}"
        )

    return "黄金价格查询失败：实时与历史数据源均不可用，请稍后重试。"


def execute_tool(name: str, args: dict) -> str:
    if name == "shell_exec":
        return tool_shell_exec(args.get("command", ""), args.get("timeout", 30))
    elif name == "write_file":
        return tool_write_file(args.get("path", ""), args.get("content", ""))
    elif name == "read_file":
        return tool_read_file_content(args.get("path", ""))
    elif name == "http_get":
        return tool_http_get(args.get("url", ""), args.get("headers", {}))
    elif name == "schedule_task":
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

def build_system_prompt(chat_id: str) -> str:
    tavily_line = (
        "- 实时搜索（后端自动）：当用户询问天气、新闻、价格等实时信息时，后端会自动用 Tavily 搜索引擎查询，"
        "并将搜索结果注入到用户消息的开头。你只需直接基于这些已注入的内容回答，"
        "不要说'我无法联网'，也不要自行用 http_get 重复查询同一问题。"
    ) if _tavily else ""

    return f"""你是 OpenClaw，一个运行在 Linux 服务器上、具备真实执行能力的 AI Agent。
当前用户的 chat_id 为：{chat_id}

你拥有以下能力：
- shell_exec：在 /app/workspace 目录执行 Shell 命令
- write_file：在工作区创建/覆盖文件
- read_file：读取工作区文件内容
- http_get：发起 HTTP GET 请求（查询公开 API、抓取数据）
- schedule_task：创建 cron 定时任务（服务重启后自动恢复）
- list_tasks：列出所有定时任务
- remove_task：删除定时任务
- get_stock_price：查询A股实时股价（支持股票代码或名称，如"600519"或"贵州茅台"）
- get_gold_price：查询上海金交所黄金现货价格（Au99.99 等品种）
{tavily_line}

工作原则：
1. 用户说"帮我做某事"时，直接动手执行，不要只给文字建议。
2. 需要运行脚本时，先用 write_file 写好，再用 shell_exec 执行。
3. 设置定时任务用 schedule_task，而不是直接修改 crontab。
4. 如果用户希望收到定时任务的执行结果，在调用 schedule_task 时将 notify_chat_id 设为 {chat_id}，系统会自动通过 Telegram 主动推送结果给用户。
5. 遇到错误要查看输出、分析原因并尝试修复，不要直接放弃。
6. 任务完成后简洁告知用户结果和下次执行时间等关键信息。
7. 不要编造或猜测自己的能力范围；上面列出的就是你全部能力。
8. 涉及价格/行情时，必须严格基于工具返回结果中的“数据日期/时间”表述；历史数据绝不能说成“今天”或“实时”。
9. 当用户询问 A股/股票/黄金/金价 时，优先调用 get_stock_price 或 get_gold_price；不要仅根据搜索引擎摘要直接报价格。"""

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
    return OpenAI(
        api_key=os.getenv(provider["api_key_env"]),
        base_url=provider["base_url"]
    )


# ─── API 路由 ─────────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    prompt = data.get("prompt", "")
    chat_id = str(data.get("chat_id", "default"))

    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = []

    history = conversation_histories[chat_id]

    # 联网搜索增强
    user_content = prompt
    if needs_search(prompt) and not is_market_quote_query(prompt):
        search_result = do_search(prompt)
        if search_result:
            user_content = (
                f"以下是搜索引擎获取的实时信息，请基于这些信息回答用户问题：\n\n"
                f"{search_result}\n\n"
                f"用户问题：{prompt}"
            )

    user_msg_idx = len(history)
    history.append({"role": "user", "content": user_content})

    use_tools = current_config["model"] not in TOOL_UNSUPPORTED_MODELS

    try:
        client = get_client()
        system_prompt = build_system_prompt(chat_id)
        messages = [{"role": "system", "content": system_prompt}] + history

        if not use_tools:
            # 不支持工具的模型，退回纯对话
            response = client.chat.completions.create(
                model=current_config["model"],
                messages=messages,
            )
            reply = response.choices[0].message.content or ""
            history[user_msg_idx] = {"role": "user", "content": prompt}
            history.append({"role": "assistant", "content": reply})
            return jsonify({"response": reply})

        # Agent 循环：最多 10 轮工具调用
        for _ in range(10):
            messages = [{"role": "system", "content": system_prompt}] + history
            response = client.chat.completions.create(
                model=current_config["model"],
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
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
                    result = execute_tool(tc.function.name, args)
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                # AI 返回最终文本，结束循环
                reply = msg.content or ""
                history[user_msg_idx] = {"role": "user", "content": prompt}
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


@app.route("/api/set_model", methods=["POST"])
def set_model():
    data = request.json
    provider = data.get("provider", "copilot")
    model = data.get("model", "gpt-4.1")
    if provider not in PROVIDERS:
        return jsonify({"status": "error", "message": f"未知 provider: {provider}"}), 400
    current_config["provider"] = provider
    current_config["model"] = model
    return jsonify({"status": "ok", "provider": provider, "model": model})


@app.route("/api/get_model", methods=["GET"])
def get_model():
    return jsonify(current_config)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
