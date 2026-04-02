#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
DEFAULT_PROXY_PORT=8080

DEPLOY_MODE=""
DEPLOY_REASON=""
DOMAIN=""
NGINX_PROXY_PORT=""
OPENAI_PROVIDER=""
OPENAI_MODEL=""
GITHUB_TOKEN=""
DEEPSEEK_API_KEY=""
TAVILY_API_KEY=""
TUSHARE_TOKEN=""
TELEGRAM_BOT_TOKEN=""
FEISHU_APP_ID=""
FEISHU_APP_SECRET=""
FEISHU_VERIFICATION_TOKEN=""
FEISHU_ENCRYPT_KEY=""
ENABLE_TELEGRAM=0
ENABLE_FEISHU=0
ENABLE_TAVILY=0
ENABLE_TUSHARE=0
COMPOSE_ARGS=()
DOCKER_CMD=("docker")
COMPOSE_CMD=("docker" "compose")

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

prompt_value() {
    local __var_name="$1"
    local prompt_text="$2"
    local default_value="${3:-}"
    local secret="${4:-0}"
    local allow_empty="${5:-0}"
    local value=""

    while true; do
        if [ "$secret" = "1" ]; then
            read -rsp "$prompt_text" value
            echo ""
        else
            read -rp "$prompt_text" value
        fi

        if [ -z "$value" ]; then
            value="$default_value"
        fi

        if [ "$allow_empty" = "1" ] || [ -n "$value" ]; then
            printf -v "$__var_name" '%s' "$value"
            return
        fi

        echo "此项不能为空，请重新输入。"
    done
}

prompt_yes_no() {
    local prompt_text="$1"
    local default_answer="${2:-y}"
    local answer=""

    while true; do
        read -rp "$prompt_text" answer
        answer="${answer:-$default_answer}"
        case "$answer" in
            y|Y|yes|YES) return 0 ;;
            n|N|no|NO) return 1 ;;
            *) echo "请输入 y 或 n。" ;;
        esac
    done
}

port_in_use() {
    local port="$1"

    if command_exists ss; then
        ss -ltn "( sport = :$port )" 2>/dev/null | tail -n +2 | grep -q .
        return
    fi

    if command_exists netstat; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$port$"
        return
    fi

    return 1
}

find_free_port() {
    local port="$1"
    while port_in_use "$port"; do
        port=$((port + 1))
    done
    echo "$port"
}

resolve_docker_commands() {
    if command_exists docker; then
        if docker info >/dev/null 2>&1; then
            DOCKER_CMD=("docker")
        elif command_exists sudo && sudo docker info >/dev/null 2>&1; then
            DOCKER_CMD=("sudo" "docker")
        fi
    fi

    if "${DOCKER_CMD[@]}" compose version >/dev/null 2>&1; then
        COMPOSE_CMD=("${DOCKER_CMD[@]}" "compose")
        return
    fi

    if command_exists docker-compose; then
        COMPOSE_CMD=("docker-compose")
        return
    fi

    echo "未找到可用的 Docker Compose 命令。"
    exit 1
}

install_docker_if_needed() {
    if command_exists docker; then
        return
    fi

    echo "未检测到 Docker，准备自动安装。"
    if ! command_exists curl; then
        sudo apt-get update
        sudo apt-get install -y curl
    fi

    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh

    if command_exists sudo; then
        sudo usermod -aG docker "$USER" || true
    fi

    echo "Docker 安装完成。若当前用户尚未加入 docker 组生效，脚本会自动尝试使用 sudo。"
}

ensure_base_dependencies() {
    if command_exists apt-get; then
        sudo apt-get update
        sudo apt-get install -y curl git ca-certificates
    fi
}

detect_proxy_mode() {
    if command_exists systemctl && systemctl is-active --quiet nginx 2>/dev/null; then
        DEPLOY_MODE="nginx"
        DEPLOY_REASON="检测到 nginx 正在运行，自动切换为 nginx 前置模式。"
        return
    fi

    if port_in_use 80 || port_in_use 443; then
        DEPLOY_MODE="nginx"
        DEPLOY_REASON="检测到 80 或 443 已被占用，自动切换为前置代理模式。"
        return
    fi

    DEPLOY_MODE="caddy"
    DEPLOY_REASON="检测到 80/443 空闲，将使用 Caddy 直接接管公网入口。"
}

choose_ai_provider() {
    local provider_choice=""
    local model_choice=""

    echo "请选择默认 AI 提供商："
    echo "  1) GitHub Copilot"
    echo "  2) DeepSeek"

    while true; do
        read -rp "输入 1 或 2（默认 1）: " provider_choice
        provider_choice="${provider_choice:-1}"
        case "$provider_choice" in
            1)
                OPENAI_PROVIDER="copilot"
                echo "请选择默认 Copilot 模型："
                echo "  1) gpt-4.1"
                echo "  2) gpt-4o"
                echo "  3) gpt-4o-mini"
                echo "  4) o3-mini"
                echo "  5) o1-mini"
                while true; do
                    read -rp "输入 1-5（默认 1）: " model_choice
                    model_choice="${model_choice:-1}"
                    case "$model_choice" in
                        1) OPENAI_MODEL="gpt-4.1" ; break ;;
                        2) OPENAI_MODEL="gpt-4o" ; break ;;
                        3) OPENAI_MODEL="gpt-4o-mini" ; break ;;
                        4) OPENAI_MODEL="o3-mini" ; break ;;
                        5) OPENAI_MODEL="o1-mini" ; break ;;
                        *) echo "请输入有效选项。" ;;
                    esac
                done
                prompt_value GITHUB_TOKEN "请输入 GITHUB_TOKEN: " "" 1 0
                return
                ;;
            2)
                OPENAI_PROVIDER="deepseek"
                echo "请选择默认 DeepSeek 模型："
                echo "  1) deepseek-chat"
                echo "  2) deepseek-reasoner"
                while true; do
                    read -rp "输入 1 或 2（默认 1）: " model_choice
                    model_choice="${model_choice:-1}"
                    case "$model_choice" in
                        1) OPENAI_MODEL="deepseek-chat" ; break ;;
                        2) OPENAI_MODEL="deepseek-reasoner" ; break ;;
                        *) echo "请输入有效选项。" ;;
                    esac
                done
                prompt_value DEEPSEEK_API_KEY "请输入 DEEPSEEK_API_KEY: " "" 1 0
                return
                ;;
            *) echo "请输入有效选项。" ;;
        esac
    done
}

collect_channel_config() {
    if prompt_yes_no "是否启用 Telegram Bot？[Y/n]: " "y"; then
        ENABLE_TELEGRAM=1
        prompt_value TELEGRAM_BOT_TOKEN "请输入 TELEGRAM_BOT_TOKEN: " "" 1 0
    fi

    if prompt_yes_no "是否启用飞书机器人？[y/N]: " "n"; then
        ENABLE_FEISHU=1
        prompt_value FEISHU_APP_ID "请输入 FEISHU_APP_ID: " "" 0 0
        prompt_value FEISHU_APP_SECRET "请输入 FEISHU_APP_SECRET: " "" 1 0
        prompt_value FEISHU_VERIFICATION_TOKEN "请输入 FEISHU_VERIFICATION_TOKEN: " "" 0 0
        prompt_value FEISHU_ENCRYPT_KEY "请输入 FEISHU_ENCRYPT_KEY（未启用加密可直接回车）: " "" 0 1
    fi

    if [ "$ENABLE_TELEGRAM" -eq 0 ] && [ "$ENABLE_FEISHU" -eq 0 ]; then
        echo "未选择任何 Bot 渠道，本次仅部署 OpenClaw API 与反向代理。"
    fi
}

collect_optional_config() {
    if prompt_yes_no "是否启用 Tavily 联网搜索？[y/N]: " "n"; then
        ENABLE_TAVILY=1
        prompt_value TAVILY_API_KEY "请输入 TAVILY_API_KEY: " "" 1 0
    fi

    if prompt_yes_no "是否启用 Tushare 作为 A股主数据源？[y/N]: " "n"; then
        ENABLE_TUSHARE=1
        prompt_value TUSHARE_TOKEN "请输入 TUSHARE_TOKEN: " "" 1 0
    fi
}

write_env_file() {
    local tmp_file
    tmp_file="$(mktemp)"

    {
        printf "DOMAIN=%s\n" "$DOMAIN"
        printf "OPENAI_PROVIDER=%s\n" "$OPENAI_PROVIDER"
        printf "OPENAI_MODEL=%s\n" "$OPENAI_MODEL"
        printf "GITHUB_TOKEN=%s\n" "$GITHUB_TOKEN"
        printf "DEEPSEEK_API_KEY=%s\n" "$DEEPSEEK_API_KEY"
        printf "TAVILY_API_KEY=%s\n" "$TAVILY_API_KEY"
        printf "TUSHARE_TOKEN=%s\n" "$TUSHARE_TOKEN"
        printf "TELEGRAM_BOT_TOKEN=%s\n" "$TELEGRAM_BOT_TOKEN"
        printf "FEISHU_APP_ID=%s\n" "$FEISHU_APP_ID"
        printf "FEISHU_APP_SECRET=%s\n" "$FEISHU_APP_SECRET"
        printf "FEISHU_VERIFICATION_TOKEN=%s\n" "$FEISHU_VERIFICATION_TOKEN"
        printf "FEISHU_ENCRYPT_KEY=%s\n" "$FEISHU_ENCRYPT_KEY"
        if [ "$DEPLOY_MODE" = "nginx" ]; then
            printf "NGINX_PROXY_PORT=%s\n" "$NGINX_PROXY_PORT"
        fi
    } > "$tmp_file"

    tr -d '\r' < "$tmp_file" > "$ENV_FILE"
    rm -f "$tmp_file"
    echo ".env 已写入：$ENV_FILE"
}

build_compose_args() {
    COMPOSE_ARGS=(-f "$PROJECT_DIR/docker-compose.yml")

    if [ "$DEPLOY_MODE" = "nginx" ]; then
        COMPOSE_ARGS+=(-f "$PROJECT_DIR/docker-compose.nginx.yml")
    fi

    if [ "$ENABLE_TELEGRAM" -eq 1 ]; then
        COMPOSE_ARGS+=(--profile telegram)
    fi

    if [ "$ENABLE_FEISHU" -eq 1 ]; then
        COMPOSE_ARGS+=(--profile feishu)
    fi
}

build_compose_args_from_env() {
    COMPOSE_ARGS=(-f "$PROJECT_DIR/docker-compose.yml")
    ENABLE_TELEGRAM=0
    ENABLE_FEISHU=0
    ENABLE_TUSHARE=0

    if grep -q '^NGINX_PROXY_PORT=' "$ENV_FILE"; then
        DEPLOY_MODE="nginx"
        COMPOSE_ARGS+=(-f "$PROJECT_DIR/docker-compose.nginx.yml")
    else
        DEPLOY_MODE="caddy"
    fi

    if grep -q '^TELEGRAM_BOT_TOKEN=.' "$ENV_FILE"; then
        ENABLE_TELEGRAM=1
        COMPOSE_ARGS+=(--profile telegram)
    fi

    if grep -q '^FEISHU_APP_ID=.' "$ENV_FILE"; then
        ENABLE_FEISHU=1
        COMPOSE_ARGS+=(--profile feishu)
    fi

    if grep -q '^TUSHARE_TOKEN=.' "$ENV_FILE"; then
        ENABLE_TUSHARE=1
    fi
}

show_summary() {
    echo "===================================="
    echo "部署模式: $DEPLOY_MODE"
    echo "$DEPLOY_REASON"
    echo "默认模型: [$OPENAI_PROVIDER] $OPENAI_MODEL"
    if [ "$ENABLE_TELEGRAM" -eq 1 ]; then
        echo "Telegram Bot: 已启用"
    else
        echo "Telegram Bot: 未启用"
    fi
    if [ "$ENABLE_FEISHU" -eq 1 ]; then
        echo "飞书机器人: 已启用"
    else
        echo "飞书机器人: 未启用"
    fi
    if [ "$ENABLE_TAVILY" -eq 1 ]; then
        echo "Tavily 搜索: 已启用"
    else
        echo "Tavily 搜索: 未启用"
    fi
    if [ "$ENABLE_TUSHARE" -eq 1 ]; then
        echo "Tushare 主数据源: 已启用"
    else
        echo "Tushare 主数据源: 未启用（将使用其它数据源兜底）"
    fi
    echo "===================================="
}

read_nginx_proxy_port() {
    local port="${NGINX_PROXY_PORT:-}"
    if [ -z "$port" ] && [ -f "$ENV_FILE" ] && grep -q '^NGINX_PROXY_PORT=' "$ENV_FILE"; then
        port="$(grep '^NGINX_PROXY_PORT=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r' | tr -d ' ')"
    fi
    if [ -z "$port" ]; then
        port="$DEFAULT_PROXY_PORT"
    fi
    echo "$port"
}

write_nginx_openclaw_snippet() {
    local port="$1"
    local out_dir="$PROJECT_DIR/nginx/generated"
    local snippet_src="$PROJECT_DIR/nginx/openclaw-proxy-locations.snippet"
    local out_file="$out_dir/openclaw-docker.conf"

    if [ ! -f "$snippet_src" ]; then
        echo "⚠️  未找到 $snippet_src，跳过 nginx 片段生成。"
        return
    fi

    mkdir -p "$out_dir"
    sed "s/__PORT__/${port}/g" "$snippet_src" > "$out_file"
    echo "✅ 已生成 nginx 片段：$out_file"
    echo ""
    echo "【前置 nginx 与静态站并存时】在 443 的 server { } 内加入一行（放在 location / 之前）："
    echo "    include /etc/nginx/snippets/openclaw-docker.conf;"
    echo ""
    echo "然后（若尚未复制）："
    echo "    sudo cp $out_file /etc/nginx/snippets/openclaw-docker.conf"
    echo "    sudo nginx -t && sudo systemctl reload nginx"
    echo ""

    if [ ! -t 0 ]; then
        return
    fi

    if ! command_exists sudo; then
        return
    fi

    if prompt_yes_no "是否现在将片段复制到 /etc/nginx/snippets/openclaw-docker.conf（需 sudo）？[y/N]: " "n"; then
        sudo cp "$out_file" /etc/nginx/snippets/openclaw-docker.conf
        echo "✅ 已复制。请在 HTTPS server 块内加入 include 后执行 nginx -t 与 reload。"
    fi
}

setup_openclaw_box() {
    local box_script="$PROJECT_DIR/openclaw-box.sh"
    local box_link="/usr/local/bin/openclaw-box"

    if [ ! -f "$box_script" ]; then
        echo "⚠️  未找到 openclaw-box.sh，跳过管理命令安装。"
        return
    fi

    chmod +x "$box_script" || true

    if command_exists sudo; then
        sudo ln -sf "$box_script" "$box_link"
        sudo chmod +x "$box_link" || true
    else
        ln -sf "$box_script" "$box_link"
        chmod +x "$box_link" || true
    fi

    echo "✅ 管理命令已安装：openclaw-box"
}

start_services() {
    echo "开始构建并启动服务..."
    "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" build
    "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" up -d
    setup_openclaw_box

    if [ "$DEPLOY_MODE" = "nginx" ]; then
        write_nginx_openclaw_snippet "$(read_nginx_proxy_port)"
    fi

    echo "===================================="
    echo "部署完成。"
    if [ "$DEPLOY_MODE" = "nginx" ]; then
        echo "本机代理端口: 127.0.0.1:${NGINX_PROXY_PORT:-$(grep '^NGINX_PROXY_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"
        echo "请将前置 nginx 反代到上述端口；若同机还有静态站，请使用脚本生成的 nginx/generated/openclaw-docker.conf（见上方说明）。"
    else
        echo "访问地址: https://$DOMAIN"
    fi
    echo "查看状态: ${COMPOSE_CMD[*]} ${COMPOSE_ARGS[*]} ps"
    echo "查看日志: ${COMPOSE_CMD[*]} ${COMPOSE_ARGS[*]} logs -f"
    echo "===================================="
}

maybe_use_existing_env() {
    if [ ! -f "$ENV_FILE" ]; then
        return 1
    fi

    echo "检测到已有 .env 文件。"
    if prompt_yes_no "是否重新生成 .env？[y/N]: " "n"; then
        cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
        return 1
    fi

    echo "保留现有 .env，直接启动服务。"
    build_compose_args_from_env
    start_services
    return 0
}

check_os() {
    local os
    os="$(uname -s 2>/dev/null || echo unknown)"
    case "$os" in
        CYGWIN*|MINGW*|MSYS*)
            echo "❌ 检测到 Windows 系统，OpenClaw 不支持在 Windows 上直接运行。"
            echo "   请在 Linux 服务器（Ubuntu 20.04/22.04）上部署。"
            exit 1
            ;;
        Linux*)
            ;;
        Darwin*)
            echo "⚠️  检测到 macOS，仅建议用于开发测试，生产环境请使用 Linux 服务器。"
            ;;
        *)
            echo "⚠️  未知操作系统: $os，继续部署可能出现兼容性问题。"
            ;;
    esac
}

main() {
    check_os
    ensure_base_dependencies
    install_docker_if_needed
    resolve_docker_commands

    if maybe_use_existing_env; then
        exit 0
    fi

    detect_proxy_mode
    echo "$DEPLOY_REASON"

    prompt_value DOMAIN "请输入对外访问域名（如 bot.example.com）: " "" 0 0

    if [ "$DEPLOY_MODE" = "nginx" ]; then
        local suggested_port
        suggested_port="$(find_free_port "$DEFAULT_PROXY_PORT")"
        prompt_value NGINX_PROXY_PORT "请输入本机反向代理端口（默认 $suggested_port）: " "$suggested_port" 0 0
    fi

    choose_ai_provider
    collect_channel_config
    collect_optional_config
    write_env_file
    build_compose_args
    show_summary
    start_services
}

main "$@"
