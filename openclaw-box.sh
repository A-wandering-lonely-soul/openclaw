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
    echo " （1）清空当前会话上下文"
    echo " （2）清空所有人上下文"
    echo " （3）按入口清空上下文（Web/Telegram/飞书）"
    echo " （4）重启所有服务"
    echo " （5）停止所有服务"
    echo " （6）启动所有服务"
    echo " （7）查看应用日志（openclaw_service）"
    echo " （8）查看全部容器日志"
    echo " （9）清空日志"
    echo "（10）切换模型"
    echo "（11）管理图片存储"
    echo "（12）重置配置（重新输入 Token 和域名）"
    echo "（13）卸载（停止并移除 Docker 容器）"
    echo " （0）退出"
    echo "=============================="
    echo -n "请选择: "
}

switch_model() {
    echo ""
    echo "--- 选择服务提供商 ---"
    echo " （1）GitHub Copilot"
    echo " （2）DeepSeek"
    echo " （3）Ollama"
    echo -n "请选择: "
    read -r provider_choice

    local provider model
    case $provider_choice in
        1)
            provider="copilot"
            echo ""
            echo "--- Copilot 模型 ---"
            echo " （1）gpt-4.1        GPT-4.1（默认）"
            echo " （2）gpt-4o         GPT-4o（支持图片）"
            echo " （3）gpt-4o-mini    GPT-4o Mini（轻量）"
            echo " （4）o3-mini        o3 Mini（推理）"
            echo " （5）o1-mini        o1 Mini（推理）"
            echo " （6）claude-opus-4.6    Claude Opus 4.6"
            echo " （7）claude-sonnet-4.6  Claude Sonnet 4.6"
            echo " （8）gpt-5.3-codex      GPT-5.3-Codex"
            echo " （9）gemini-3.1-pro     Gemini 3.1 Pro"
            echo -n "请选择: "
            read -r model_choice
            case $model_choice in
                1) model="gpt-4.1" ;;
                2) model="gpt-4o" ;;
                3) model="gpt-4o-mini" ;;
                4) model="o3-mini" ;;
                5) model="o1-mini" ;;
                6) model="claude-opus-4.6" ;;
                7) model="claude-sonnet-4.6" ;;
                8) model="gpt-5.3-codex" ;;
                9) model="gemini-3.1-pro" ;;
                *) echo "❌ 无效选项"; return ;;
            esac
            ;;
        2)
            provider="deepseek"
            echo ""
            echo "--- DeepSeek 模型 ---"
            echo " （1）deepseek-chat      DeepSeek V3（对话）"
            echo " （2）deepseek-reasoner  DeepSeek R1（推理）"
            echo -n "请选择: "
            read -r model_choice
            case $model_choice in
                1) model="deepseek-chat" ;;
                2) model="deepseek-reasoner" ;;
                *) echo "❌ 无效选项"; return ;;
            esac
            ;;
        3)
            provider="ollama"
            echo ""
            echo "--- Ollama 模型 ---"
            echo " （1）llama3.2:3b"
            echo " （2）qwen2.5:3b"
            echo " （3）llama3.1:8b"
            echo " （4）qwen2.5:7b-instruct"
            echo " （5）gemma3:12b"
            echo " （6）自定义输入"
            echo -n "请选择: "
            read -r model_choice
            case $model_choice in
                1) model="llama3.2:3b" ;;
                2) model="qwen2.5:3b" ;;
                3) model="llama3.1:8b" ;;
                4) model="qwen2.5:7b-instruct" ;;
                5) model="gemma3:12b" ;;
                6)
                    echo -n "输入 ollama 模型名（如 llama3.1:8b）: "
                    read -r model
                    if [ -z "$model" ]; then
                        echo "❌ 模型名不能为空"
                        return
                    fi
                    ;;
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

clear_current_context() {
    echo ""
    echo "请输入要清空的 chat_id（示例：Telegram 私聊/群聊数字ID，或 feishu_xxx）"
    read -rp "chat_id: " target_chat_id
    if [ -z "$target_chat_id" ]; then
        echo "❌ chat_id 不能为空"
        return
    fi
    echo "正在清空会话 [$target_chat_id] 的上下文..."
    curl -s -X POST "$API_URL/api/clear_context" \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\": \"${target_chat_id}\"}" > /dev/null
    echo "✅ 会话 [$target_chat_id] 的上下文已清空"
}

clear_all_context() {
    echo "正在清空所有人上下文..."
    curl -s -X POST "$API_URL/api/clear_context" \
        -H "Content-Type: application/json" \
        -d '{"chat_id": "all"}' > /dev/null
    echo "✅ 已清空所有会话上下文"
}

clear_by_entry() {
    echo ""
    echo "--- 按入口清空上下文 ---"
    echo " （1）清空 Web 前端会话"
    echo " （2）清空 Telegram 会话"
    echo " （3）清空 飞书 会话"
    echo " （4）清空全部（Web + Telegram + 飞书）"
    echo -n "请选择: "
    read -r entry_choice
    
    local target_entry
    case "$entry_choice" in
        1)
            target_entry="web_frontend"
            echo "正在清空 Web 前端会话..."
            ;;
        2)
            target_entry="telegram"
            echo "正在清空 Telegram 会话..."
            ;;
        3)
            target_entry="feishu"
            echo "正在清空 飞书 会话..."
            ;;
        4)
            target_entry="all"
            echo "正在清空所有会话..."
            ;;
        *)
            echo "❌ 无效选项"
            return
            ;;
    esac
    
    local result
    result=$(curl -s -X POST "$API_URL/api/clear_context_by_entry" \
        -H "Content-Type: application/json" \
        -d "{\"entry\": \"${target_entry}\"}")
    
    if echo "$result" | grep -q '"status":"ok"'; then
        local cleared_count=$(echo "$result" | grep -o '"cleared_count":[0-9]*' | cut -d':' -f2)
        echo "✅ 已清空 $target_entry 的会话（共清空 $cleared_count 个）"
    else
        echo "❌ 清空失败: $result"
    fi
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

view_app_logs() {
    echo ""
    echo "--- openclaw_service 应用日志 ---"
    echo " （1）最近 100 行"
    echo " （2）实时跟踪（Ctrl+C 退出）"
    echo -n "请选择: "
    read -r log_choice
    case "$log_choice" in
        1) docker logs --tail 100 openclaw_service ;;
        2) docker logs -f openclaw_service ;;
        *) echo "❌ 无效选项" ;;
    esac
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

manage_images() {
    echo ""
    echo "--- 图片存储管理 ---"
    local info
    info=$(curl -s "$API_URL/api/images")
    if [ $? -ne 0 ] || [ -z "$info" ]; then
        echo "❌ 无法连接到服务，请先确认服务已启动"
        return
    fi
    local count total_size
    count=$(echo "$info" | grep -o '"count":[0-9]*' | cut -d: -f2)
    total_size=$(echo "$info" | grep -o '"total_size":[0-9]*' | cut -d: -f2)
    count=${count:-0}
    total_size=${total_size:-0}

    # 转换大小
    local size_str
    if [ "$total_size" -ge 1048576 ] 2>/dev/null; then
        size_str="$(awk "BEGIN {printf \"%.1fMB\", $total_size/1048576}")"
    elif [ "$total_size" -ge 1024 ] 2>/dev/null; then
        size_str="$(awk "BEGIN {printf \"%.1fKB\", $total_size/1024}")"
    else
        size_str="${total_size}B"
    fi

    echo "共 $count 张图片，合计 $size_str"
    echo ""

    # 显示最近10张
    if [ "$count" -gt 0 ]; then
        echo "最近图片："
        echo "$info" | grep -o '"name":"[^"]*"' | head -10 | cut -d'"' -f4 | nl -w2 -s'. '
        echo ""
    fi

    echo " （1）删除全部图片"
    echo " （2）按文件名删除"
    echo " （0）返回"
    echo -n "请选择: "
    read -r img_choice
    case $img_choice in
        1)
            echo -n "确认删除全部图片？(y/N): "
            read -r confirm
            case "$confirm" in
                y|Y)
                    result=$(curl -s -X POST "$API_URL/api/images/delete" \
                        -H "Content-Type: application/json" \
                        -d '{"target":"all"}')
                    del_count=$(echo "$result" | grep -o '"count":[0-9]*' | cut -d: -f2)
                    echo "✅ 已删除 ${del_count:-0} 张图片"
                    ;;
                *) echo "已取消" ;;
            esac
            ;;
        2)
            echo -n "输入要删除的文件名: "
            read -r filename
            if [ -z "$filename" ]; then
                echo "❌ 文件名不能为空"
                return
            fi
            result=$(curl -s -X POST "$API_URL/api/images/delete" \
                -H "Content-Type: application/json" \
                -d "{\"target\":\"$filename\"}")
            if echo "$result" | grep -q '"status":"ok"'; then
                echo "✅ 已删除: $filename"
            else
                err=$(echo "$result" | grep -o '"error":"[^"]*"' | cut -d'"' -f4)
                echo "❌ 删除失败: ${err:-未知错误}"
            fi
            ;;
        0) return ;;
        *) echo "❌ 无效选项" ;;
    esac
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
        1) clear_current_context ;;
        2) clear_all_context ;;
        3) clear_by_entry ;;
        4) restart_services ;;
        5) stop_services ;;
        6) start_services ;;
        7) view_app_logs ;;
        8) view_logs ;;
        9) clear_logs ;;
        10) switch_model ;;
        11) manage_images ;;
        12) reset_config ;;
        13) uninstall_services ;;
        0) echo "退出。"; exit 0 ;;
        *) echo "❌ 无效选项，请重新输入" ;;
    esac
done
