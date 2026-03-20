import os
import re
import json
import time
import hashlib
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY", "")
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "http://openclaw:8000")

FEISHU_API = "https://open.feishu.cn/open-apis"

# Tenant Access Token 缓存
_token_cache = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()

# 消息去重（防止飞书重试导致重复处理）
_processed_events = set()
_events_lock = threading.Lock()


def get_tenant_access_token() -> str:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
        resp = requests.post(
            f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            timeout=10,
        )
        data = resp.json()
        token = data.get("tenant_access_token", "")
        expires_in = data.get("expire", 7200)
        _token_cache["token"] = token
        _token_cache["expires_at"] = time.time() + expires_in
        return token


def reply_to_message(message_id: str, text: str):
    """回复指定消息（保留会话线索）"""
    token = get_tenant_access_token()
    requests.post(
        f"{FEISHU_API}/im/v1/messages/{message_id}/reply",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"msg_type": "text", "content": json.dumps({"text": text})},
        timeout=30,
    )


def decrypt_body(encrypted_str: str) -> dict | None:
    """AES-256-CBC 解密飞书加密消息体（需设置 FEISHU_ENCRYPT_KEY）"""
    if not ENCRYPT_KEY:
        return None
    try:
        import base64
        from Crypto.Cipher import AES

        key = hashlib.sha256(ENCRYPT_KEY.encode()).digest()
        data = base64.b64decode(encrypted_str)
        iv, cipher_text = data[:16], data[16:]
        aes = AES.new(key, AES.MODE_CBC, iv)
        decrypted = aes.decrypt(cipher_text)
        # 去掉 PKCS7 padding
        pad_len = decrypted[-1]
        return json.loads(decrypted[:-pad_len].decode())
    except Exception:
        return None


@app.route("/feishu/webhook", methods=["POST"])
def webhook():
    body = request.get_json(force=True) or {}

    # 1. 处理加密消息
    if "encrypt" in body:
        decrypted = decrypt_body(body["encrypt"])
        if decrypted is None:
            return jsonify({"error": "decrypt failed"}), 400
        body = decrypted

    # 2. URL 验证挑战（飞书配置 Webhook 时调用一次）
    if body.get("type") == "url_verification":
        if VERIFICATION_TOKEN and body.get("token") != VERIFICATION_TOKEN:
            return jsonify({"error": "invalid token"}), 403
        return jsonify({"challenge": body.get("challenge")})

    # 3. 事件处理
    header = body.get("header", {})
    event = body.get("event", {})
    event_type = header.get("event_type", "")
    event_id = header.get("event_id", "")

    # 去重
    if event_id:
        with _events_lock:
            if event_id in _processed_events:
                return jsonify({"code": 0})
            _processed_events.add(event_id)
            # 保持集合不过大
            if len(_processed_events) > 1000:
                _processed_events.clear()

    if event_type == "im.message.receive_v1":
        message = event.get("message", {})
        msg_type = message.get("message_type", "")

        # 仅处理文本消息
        if msg_type != "text":
            return jsonify({"code": 0})

        message_id = message.get("message_id", "")
        # 用 chat_id 隔离每个会话的历史上下文
        chat_id = f"feishu_{message.get('chat_id', message_id)}"

        try:
            content = json.loads(message.get("content", "{}"))
            user_text = content.get("text", "").strip()
            # 去除群里 @机器人 的 mention 标签
            user_text = re.sub(r"@\S+", "", user_text).strip()
        except Exception:
            return jsonify({"code": 0})

        if not user_text:
            return jsonify({"code": 0})

        # 先给用户一个状态提示，模拟“正在思考”
        reply_to_message(message_id, "🤔 正在思考中，请稍候…")

        # 调用 openclaw AI 服务
        try:
            resp = requests.post(
                f"{OPENCLAW_URL}/api/chat",
                json={"prompt": user_text, "chat_id": chat_id},
                timeout=60,
            )
            reply = resp.json().get("response", "抱歉，AI 暂时无法回答。")
        except Exception as e:
            reply = f"调用 AI 出错: {e}"

        reply_to_message(message_id, reply)

    return jsonify({"code": 0})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
