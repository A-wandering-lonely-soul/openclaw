#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINERS=("openclaw_service" "telegram_bot")
API_URL="http://localhost:8000"

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
    docker restart "${CONTAINERS[@]}"
    echo "✅ 重启完成"
}

stop_services() {
    echo "正在停止服务..."
    docker stop "${CONTAINERS[@]}"
    echo "✅ 服务已停止"
}

start_services() {
    echo "正在启动服务..."
    docker start "${CONTAINERS[@]}"
    echo "✅ 服务已启动"
}

view_logs() {
    echo ""
    echo "--- openclaw_service 日志 (最近50行) ---"
    docker logs --tail 50 openclaw_service
    echo ""
    echo "--- telegram_bot 日志 (最近50行) ---"
    docker logs --tail 50 telegram_bot
}

clear_logs() {
    echo "正在清空日志..."
    for container in "${CONTAINERS[@]}"; do
        log_path=$(docker inspect --format='{{.LogPath}}' "$container" 2>/dev/null)
        if [ -n "$log_path" ] && [ -f "$log_path" ]; then
            truncate -s 0 "$log_path"
            echo "✅ $container 日志已清空"
        else
            echo "⚠️  $container 日志路径未找到"
        fi
    done
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
        0) echo "退出。"; exit 0 ;;
        *) echo "❌ 无效选项，请重新输入" ;;
    esac
done
