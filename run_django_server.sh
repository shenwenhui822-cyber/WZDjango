#!/bin/bash

# 若被 sh 调用，自动切换到 bash 重新执行
if [ -z "${BASH_VERSION:-}" ]; then
    echo "[WARN] 检测到当前非 bash，自动使用 bash 重新执行脚本..."
    exec bash "$0" "$@"
fi

# ======================================
# Django 开发服务器启动脚本 (Ubuntu)
# 功能：
#   1. 启动前检查目标端口；若已被占用则终止占用进程后再启动
#   2. 启动 Django 开发服务器（支持外部访问）
#   3. 支持自定义端口和 MongoDB 地址
# ======================================

# --- 配置参数 ---
PORT=${1:-7443}                  # 默认端口 7443（可通过命令行参数覆盖）
MONGO_HOST=${2:-"localhost"}      # 默认 MongoDB 地址 localhost
MONGO_PORT=${3:-"27017"}          # 默认 MongoDB 端口 27017
LOG_FILE="django_server.log"      # 日志文件路径
PY_CMD="python3"                  # 默认 Python 命令（激活 venv 后会改为 python）
PIP_CMD="pip3"                    # 默认 pip 命令（激活 venv 后会改为 pip）

# --- 激活虚拟环境 ---
activate_venv() {
    if [ -f "venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        . "venv/bin/activate"
        if command -v python >/dev/null 2>&1 && command -v pip >/dev/null 2>&1; then
            PY_CMD="python"
            PIP_CMD="pip"
            echo "[INFO] 已激活虚拟环境: venv/bin/activate"
            return 0
        fi
        echo "[WARN] 虚拟环境激活后未找到 python/pip，继续使用系统环境。"
        return 0
    fi
    echo "[WARN] 未找到 venv/bin/activate，继续使用系统 Python: $PY_CMD"
    return 0
}

# --- 自动安装 Python 依赖（镜像失败自动回退官方源） ---
auto_install_python_deps() {
    local install_target="$1"
    local mirror_url="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
    local official_url="https://pypi.org/simple"

    if ! command -v "$PIP_CMD" >/dev/null 2>&1; then
        echo "[ERROR] 未找到 pip 命令: $PIP_CMD"
        echo "[HINT] 请先安装 pip，或在虚拟环境中运行此脚本。"
        exit 1
    fi

    echo "[INFO] 尝试通过镜像源安装依赖: $install_target"
    if "$PIP_CMD" install -i "$mirror_url" $install_target; then
        echo "[INFO] 依赖安装成功（镜像源）。"
        return 0
    fi

    echo "[WARN] 镜像源安装失败，自动回退官方源..."
    if "$PIP_CMD" install -i "$official_url" $install_target; then
        echo "[INFO] 依赖安装成功（官方源）。"
        return 0
    fi

    echo "[ERROR] 自动安装依赖失败: $install_target"
    echo "[HINT] 请检查网络、Python 环境和 pip 配置后重试。"
    exit 1
}

# --- Linux 运行环境预检查（仅检查，不自动创建环境） ---
precheck_linux_env() {
    echo "[INFO] 开始进行 Linux 运行环境检查..."

    if [ -f "/proc/version" ] && ! grep -qi "linux" /proc/version; then
        echo "[WARN] 当前系统似乎不是 Linux，跳过 Linux 专用检查。"
        return 0
    fi

    if ! command -v "$PY_CMD" >/dev/null 2>&1; then
        echo "[ERROR] 未找到 Python 命令: $PY_CMD"
        echo "[HINT] 请先安装 Python3 或激活正确的虚拟环境后再运行脚本。"
        exit 1
    fi

    # 检测关键依赖（requirements 关键模块），缺失时自动安装
    local missing_mods=()
    local check_mods=("django" "dotenv" "pymongo" "pandas" "numpy" "openpyxl" "xlrd" "rqdatac")
    local m=""
    for m in "${check_mods[@]}"; do
        "$PY_CMD" -c "import ${m}" >/dev/null 2>&1 || missing_mods+=("${m}")
    done
    if [ ${#missing_mods[@]} -gt 0 ]; then
        echo "[WARN] 缺少 Python 依赖模块: ${missing_mods[*]}"
        if [ -f "requirements.txt" ]; then
            auto_install_python_deps "-r requirements.txt"
        else
            echo "[ERROR] 未找到 requirements.txt，无法自动安装完整依赖。"
            exit 1
        fi
    fi

    if [ ! -f ".env" ]; then
        echo "[WARN] 未找到 .env 文件，若 settings.py 依赖环境变量可能启动失败。"
    fi

    echo "[INFO] Linux 运行环境检查通过。"
}

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

# --- 启动 Django 服务器 ---
start_server() {
    echo "[INFO] 启动 Django 开发服务器 (Port: $PORT)..."
    "$PY_CMD" manage.py runserver 0.0.0.0:"$PORT" >> "$LOG_FILE" 2>&1 &
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
    activate_venv
    precheck_linux_env

    ensure_port_free
    # 启动 Django 服务器
    start_server
}

# 执行主逻辑
main
