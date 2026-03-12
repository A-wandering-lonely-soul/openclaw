#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_URL="http://localhost:8000"

check_environment() {
    # 检测 Windows 系统
    local os
    os="$(uname -s 2>/dev/null || echo unknown)"
    case "$os" in
        CYGWIN*|MINGW*|MSYS*)
            echo "❌ 检测到 Windows 系统，OpenClaw 不支持在 Windows 上直接运行。"
            echo "   请在 Linux 服务器上部署。"
            exit 1
            ;;
    esac

    # 检测 Docker
    if ! command -v docker >/dev/null 2>&1; then
        echo "❌ 未检测到 Docker，请先安装 Docker。"
        exit 1
    fi

    if ! docker info >/dev/null 2>&1; then
        echo "❌ Docker 未运行或当前用户无权限。"
        echo "   请确认 Docker 已启动，或执行: sudo usermod -aG docker \$USER"
        exit 1
    fi
}

check_environment

get_containers() {
    local containers=("openclaw_service")

    if docker ps -a --format '{{.Names}}' | grep -qx 'telegram_bot'; then
        containers+=("telegram_bot")
    fi

    if docker ps -a --format '{{.Names}}' | grep -qx 'feishu_bot'; then
        containers+=("feishu_bot")
    fi

    if docker ps -a --format '{{.Names}}' | grep -qx 'caddy'; then
        containers+=("caddy")
    fi

    printf '%s\n' "${containers[@]}"
}

show_menu() {
    local model_info provider model
    model_info=$(curl -s "$API_URL/api/get_model" 2>/dev/null)
    provider=$(echo "$model_info" | grep -o '"provider":"[^"]*"' | cut -d'"' -f4)
    model=$(echo "$model_info" | grep -o '"model":"[^"]*"' | cut -d'"' -f4)

    echo ""
    echo "=============================="
    echo "   OpenClaw 管理面板"
    if [ -n "$provider" ]; then
        echo "   当前模型: [$provider] $model"
    fi
    echo "=============================="
    echo "  1) 清空对话上下文"
    echo "  2) 重启所有服务"
    echo "  3) 停止所有服务"
    echo "  4) 启动所有服务"
    echo "  5) 查看日志"
    echo "  6) 清空日志"
    echo "  7) 切换模型"
    echo "  8) 重置配置（重新输入 Token 和域名）"
    echo "  9) 卸载（停止并移除 Docker 容器）"
    echo "  0) 退出"
    echo "=============================="
    echo -n "请选择: "
}

switch_model() {
    echo ""
    echo "--- 选择服务提供商 ---"
    echo "  1) GitHub Copilot"
    echo "  2) DeepSeek"
    echo -n "请选择: "
    read -r provider_choice

    local provider model
    case $provider_choice in
        1)
            provider="copilot"
            echo ""
            echo "--- Copilot 模型 ---"
            echo "  1) gpt-4.1        GPT-4.1（默认）"
            echo "  2) gpt-4o         GPT-4o（支持图片）"
            echo "  3) gpt-4o-mini    GPT-4o Mini（轻量）"
            echo "  4) o3-mini        o3 Mini（推理）"
            echo "  5) o1-mini        o1 Mini（推理）"
            echo -n "请选择: "
            read -r model_choice
            case $model_choice in
                1) model="gpt-4.1" ;;
                2) model="gpt-4o" ;;
                3) model="gpt-4o-mini" ;;
                4) model="o3-mini" ;;
                5) model="o1-mini" ;;
                *) echo "❌ 无效选项"; return ;;
            esac
            ;;
        2)
            provider="deepseek"
            echo ""
            echo "--- DeepSeek 模型 ---"
            echo "  1) deepseek-chat      DeepSeek V3（对话）"
            echo "  2) deepseek-reasoner  DeepSeek R1（推理）"
            echo -n "请选择: "
            read -r model_choice
            case $model_choice in
                1) model="deepseek-chat" ;;
                2) model="deepseek-reasoner" ;;
                *) echo "❌ 无效选项"; return ;;
            esac
            ;;
        *)
            echo "❌ 无效选项"
            return
            ;;
    esac

    local result
    result=$(curl -s -X POST "$API_URL/api/set_model" \
        -H "Content-Type: application/json" \
        -d "{\"provider\":\"$provider\",\"model\":\"$model\"}")
    if echo "$result" | grep -q '"status":"ok"'; then
        echo "✅ 已切换到 [$provider] $model"
    else
        echo "❌ 切换失败: $result"
    fi
}

clear_context() {
    echo "正在清空对话上下文..."
    curl -s -X POST http://localhost:8000/api/clear_context \
        -H "Content-Type: application/json" \
        -d '{"chat_id": "all"}' > /dev/null
    echo "✅ 已发送清空请求（各用户下次对话时上下文将重置）"
}

restart_services() {
    echo "正在重启服务..."
    mapfile -t containers < <(get_containers)
    docker restart "${containers[@]}"
    echo "✅ 重启完成"
}

stop_services() {
    echo "正在停止服务..."
    mapfile -t containers < <(get_containers)
    docker stop "${containers[@]}"
    echo "✅ 服务已停止"
}

start_services() {
    echo "正在启动服务..."
    mapfile -t containers < <(get_containers)
    docker start "${containers[@]}"
    echo "✅ 服务已启动"
}

view_logs() {
    mapfile -t containers < <(get_containers)
    for container in "${containers[@]}"; do
        echo ""
        echo "--- $container 日志 (最近50行) ---"
        docker logs --tail 50 "$container"
    done
}

clear_logs() {
    echo "正在清空日志..."
    mapfile -t containers < <(get_containers)
    for container in "${containers[@]}"; do
        log_path=$(docker inspect --format='{{.LogPath}}' "$container" 2>/dev/null)
        if [ -n "$log_path" ] && [ -f "$log_path" ]; then
            truncate -s 0 "$log_path"
            echo "✅ $container 日志已清空"
        else
            echo "⚠️  $container 日志路径未找到"
        fi
    done
}

reset_config() {
    echo ""
    echo "⚠️  此操作将重新生成 .env 配置文件，现有配置会自动备份。"
    read -rp "确认重置？(y/N): " confirm
    case "$confirm" in
        y|Y)
            if [ -f "$PROJECT_DIR/.env" ]; then
                cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.env.bak.$(date +%Y%m%d%H%M%S)"
                echo "已备份旧配置至 .env.bak.*"
                rm -f "$PROJECT_DIR/.env"
            fi
            echo "正在启动配置向导..."
            exec "$PROJECT_DIR/deploy.sh"
            ;;
        *)
            echo "已取消"
            ;;
    esac
}

uninstall_services() {
    echo ""
    echo "⚠️  此操作将停止并移除所有 OpenClaw 相关 Docker 容器。"
    read -rp "确认卸载？(y/N): " confirm
    case "$confirm" in
        y|Y)
            echo "正在停止并移除容器..."
            mapfile -t containers < <(get_containers)
            docker stop "${containers[@]}" 2>/dev/null || true
            docker rm "${containers[@]}" 2>/dev/null || true
            echo "正在移除相关镜像..."
            docker images --format '{{.Repository}}' \
                | grep -E '^(openclaw|telegram.bot|feishu.bot)' \
                | xargs -r docker rmi 2>/dev/null || true
            read -rp "是否同时删除 .env 配置文件？(y/N): " del_env
            case "$del_env" in
                y|Y)
                    rm -f "$PROJECT_DIR/.env"
                    echo "✅ .env 已删除"
                    ;;
            esac
            echo "✅ 卸载完成"
            ;;
        *)
            echo "已取消"
            ;;
    esac
}

while true; do
    show_menu
    read -r choice
    case $choice in
        1) clear_context ;;
        2) restart_services ;;
        3) stop_services ;;
        4) start_services ;;
        5) view_logs ;;
        6) clear_logs ;;
        7) switch_model ;;
        8) reset_config ;;
        9) uninstall_services ;;
        0) echo "退出。"; exit 0 ;;
        *) echo "❌ 无效选项，请重新输入" ;;
    esac
done
