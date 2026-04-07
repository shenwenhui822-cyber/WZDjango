#!/bin/bash

# ======================================
# Django 开发服务器启动脚本 (Ubuntu)
# 功能：
#   1. 启动前检查目标端口；若已被占用则终止占用进程后再启动
#   2. 启动 Django 开发服务器（支持外部访问）
#   3. 可选导入 MongoDB 数据
#   4. 支持自定义端口和 MongoDB 地址
# ======================================

# --- 配置参数 ---
PORT=${1:-7443}                  # 默认端口 7443（可通过命令行参数覆盖）
MONGO_HOST=${2:-"localhost"}      # 默认 MongoDB 地址 localhost
MONGO_PORT=${3:-"27017"}          # 默认 MongoDB 端口 27017
LOG_FILE="django_server.log"      # 日志文件路径

# --- 若目标端口已被占用则终止占用进程（便于重新启动） ---
ensure_port_free() {
    echo "[INFO] 检查端口 $PORT 是否被占用..."
    local pids=""

    if command -v lsof >/dev/null 2>&1; then
        pids=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
    elif command -v ss >/dev/null 2>&1; then
        # ss 输出中含 pid=12345
        pids=$(ss -lptn "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u | tr '\n' ' ')
    elif command -v fuser >/dev/null 2>&1; then
        if fuser -n tcp "$PORT" >/dev/null 2>&1; then
            echo "[WARN] 端口 $PORT 已被占用，使用 fuser 终止..."
            fuser -k -TERM "$PORT/tcp" 2>/dev/null || true
            sleep 1
            fuser -n tcp "$PORT" >/dev/null 2>&1 && fuser -k -KILL "$PORT/tcp" 2>/dev/null || true
            echo "[INFO] 已尝试释放端口 $PORT"
            return 0
        fi
        echo "[INFO] 端口 $PORT 空闲。"
        return 0
    else
        echo "[WARN] 未找到 lsof/ss/fuser，跳过端口占用检查。"
        return 0
    fi

    if [ -n "$pids" ]; then
        echo "[WARN] 端口 $PORT 已被进程占用 (PID: $pids)，正在终止..."
        # shellcheck disable=SC2086
        kill -TERM $pids 2>/dev/null || true
        sleep 1
        if command -v lsof >/dev/null 2>&1; then
            pids=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
        else
            pids=$(ss -lptn "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u | tr '\n' ' ')
        fi
        if [ -n "$pids" ]; then
            # shellcheck disable=SC2086
            kill -KILL $pids 2>/dev/null || true
        fi
        echo "[INFO] 已释放端口 $PORT"
    else
        echo "[INFO] 端口 $PORT 空闲。"
    fi
}

# --- 检查 MongoDB 是否运行（可选） ---
check_mongodb() {
    echo "[INFO] 检查 MongoDB 是否运行..."
    if ! nc -z "$MONGO_HOST" "$MONGO_PORT" -w 5; then
        echo "[ERROR] MongoDB 未运行或无法连接！请确保 MongoDB 服务已启动。"
        exit 1
    fi
    echo "[INFO] MongoDB 连接正常 (Host: $MONGO_HOST, Port: $MONGO_PORT)"
}

# --- 导入 MongoDB 数据（可选） ---
import_data() {
    echo "[INFO] 正在导入 Alphadata 数据到 MongoDB..."
    python3 manage.py import_alphadata_xlsx >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        echo "[SUCCESS] 数据导入成功！"
    else
        echo "[ERROR] 数据导入失败！请检查 manage.py 和 MongoDB 配置。"
        exit 1
    fi
}

# --- 启动 Django 服务器 ---
start_server() {
    echo "[INFO] 启动 Django 开发服务器 (Port: $PORT)..."
    python3 manage.py runserver 0.0.0.0:"$PORT" >> "$LOG_FILE" 2>&1 &
    if [ $? -eq 0 ]; then
        echo "[SUCCESS] Django 服务器已启动！"
        echo "[INFO] 访问地址: http://<服务器IP>:$PORT/"
        echo "[INFO] 日志文件: $LOG_FILE"
    else
        echo "[ERROR] Django 服务器启动失败！请检查 manage.py 和依赖。"
        exit 1
    fi
}

# --- 主逻辑 ---
main() {
    # 检查是否在 Django 项目根目录
    if [ ! -f "manage.py" ]; then
        echo "[ERROR] 当前目录不是 Django 项目根目录！请切换到正确目录。"
        exit 1
    fi

    # 可选：导入 MongoDB 数据
    read -p "[QUESTION] 是否导入 MongoDB 数据？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        check_mongodb
        import_data
    fi

    ensure_port_free
    # 启动 Django 服务器
    start_server
}

# 执行主逻辑
main
