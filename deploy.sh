#!/bin/bash

# -----------------------------
# 配置：从交互式输入读取 Key（不要硬编码在文件中）
# -----------------------------
if [ ! -f ~/openclaw/.env ]; then
    read -rp "请输入 GITHUB_TOKEN: " GITHUB_TOKEN
    read -rp "请输入 TELEGRAM_BOT_TOKEN: " TELEGRAM_BOT_TOKEN
    # 用 printf 写入保证 LF 行尾（不产生 CRLF 问题）
    printf "GITHUB_TOKEN=%s\nTELEGRAM_BOT_TOKEN=%s\n" "$GITHUB_TOKEN" "$TELEGRAM_BOT_TOKEN" > ~/openclaw/.env
    echo ".env 文件已创建"
fi

# 修复行尾符（防止 Windows 编辑导致 CRLF）
sed -i 's/\r//' ~/openclaw/.env

# -----------------------------
# 安装 Docker & Docker Compose
# -----------------------------
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo "Docker 安装完成，请退出重登录再运行此脚本"
    exit
fi

if ! command -v docker-compose &> /dev/null; then
    sudo curl -L "https://github.com/docker/compose/releases/download/v2.21.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# -----------------------------
# 克隆项目目录 (可选)
# -----------------------------
mkdir -p ~/openclaw_gpt4
cd ~/openclaw_gpt4

# -----------------------------
# 启动服务
# -----------------------------
docker-compose up -d

echo "===================================="
echo "部署完成！"
echo "OpenClaw HTTPS: https://openclaw.example.com"
echo "Telegram Bot 已就绪"
echo "日志查看: docker logs -f openclaw_service / telegram_bot "
echo "===================================="