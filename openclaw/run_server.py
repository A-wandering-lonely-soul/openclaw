import os
import re
from openai import OpenAI
from flask import Flask, request, jsonify

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

# 判断是否需要联网搜索
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
    if needs_search(prompt):
        search_result = do_search(prompt)
        if search_result:
            user_content = (
                f"以下是搜索引擎获取的实时信息，请基于这些信息回答用户问题：\n\n"
                f"{search_result}\n\n"
                f"用户问题：{prompt}"
            )

    history.append({"role": "user", "content": user_content})

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=current_config["model"],
            messages=history
        )
        reply = response.choices[0].message.content
        # 历史中保存原始问题，不存搜索增强的内容
        history[-1] = {"role": "user", "content": prompt}
        history.append({"role": "assistant", "content": reply})
        return jsonify({"response": reply})
    except Exception as e:
        history.pop()
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