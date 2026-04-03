import os
import asyncio
import requests
import base64
from datetime import datetime
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "http://openclaw:8000")
IMAGE_DIR = "/app/workspace/images"

# 创建图片目录
os.makedirs(IMAGE_DIR, exist_ok=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi! 我是你的 AI 助手，请发送消息给我吧。")


def _call_openclaw(payload: dict) -> str:
    try:
        resp = requests.post(f"{OPENCLAW_URL}/api/chat", json=payload, timeout=60)
        return resp.json().get("response", "抱歉，AI 暂时无法回答。")
    except Exception as e:
        return f"调用 AI 出错: {e}"


async def _typing_indicator(update: Update, context: ContextTypes.DEFAULT_TYPE, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        except Exception:
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4)
        except asyncio.TimeoutError:
            continue


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or update.message.caption or "").strip()
    chat_id = str(update.effective_chat.id)
    images = []

    # 处理图片
    if update.message.photo:
        try:
            # 获取最高分辨率的照片
            photo = update.message.photo[-1]
            photo_file = await photo.get_file()
            file_bytes = await photo_file.download_as_bytearray()

            # 保存到磁盘
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            img_filename = f"tg_{chat_id}_{timestamp}.jpg"
            img_path = os.path.join(IMAGE_DIR, img_filename)

            with open(img_path, "wb") as f:
                f.write(file_bytes)

            # 转 base64
            base64_img = base64.b64encode(file_bytes).decode()
            images.append({
                "type": "image_jpeg",
                "data": base64_img,
                "file_path": img_filename
            })
        except Exception as e:
            await update.message.reply_text(f"处理图片失败: {e}")
            return

    # 如果既没有文字也没有图片，忽略
    if not user_text and not images:
        return

    payload = {
        "prompt": user_text or "(用户发送了图片)",
        "chat_id": chat_id,
        "entry": "telegram",
        "images": images
    }

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(_typing_indicator(update, context, stop_event))
    reply = await asyncio.to_thread(_call_openclaw, payload)
    stop_event.set()
    await typing_task
    await update.message.reply_text(reply)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()