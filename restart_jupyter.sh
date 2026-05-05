#!/bin/bash
# ============================================
# Jupyter Notebook 重启脚本 (端口 8888)
# ============================================

LOG_FILE="/media/chenbh17/cbhssd/logs/jupyterlab.log"
JUPYTER_BIN="/home/chenbh17/anaconda3/bin/jupyter-notebook"
PORT=8888

echo "========================================"
echo "  Jupyter Notebook 重启脚本"
echo "  端口: ${PORT}"
echo "========================================"

# ---- 1. 找到监听 8888 端口的 PID ----
echo ""
echo "[1/3] 查找 ${PORT} 端口进程..."

# 方法1: 使用 lsof (最可靠)
PID=$(lsof -ti :${PORT} 2>/dev/null | head -1)

# 方法2: 如果 lsof 不可用, 使用 ss
if [ -z "$PID" ]; then
    PID=$(ss -tlnp 2>/dev/null | grep ":${PORT}" | grep -oP 'pid=\K[0-9]+' | head -1)
fi

# 方法3: 使用 netstat
if [ -z "$PID" ]; then
    PID=$(netstat -tlnp 2>/dev/null | grep ":${PORT}" | awk '{print $NF}' | grep -oP '[0-9]+' | head -1)
fi

if [ -z "$PID" ]; then
    echo "  ⚠ 未找到监听 ${PORT} 端口的进程"
else
    echo "  找到 PID: ${PID}"
    
    # 显示进程信息
    ps -p "${PID}" -o pid,user,cmd --no-headers 2>/dev/null
fi

# ---- 2. Kill 进程 ----
echo ""
echo "[2/3] 终止进程..."

if [ -n "$PID" ]; then
    # 先尝试优雅终止 (SIGTERM)
    kill "${PID}" 2>/dev/null
    sleep 2
    
    # 检查是否还在运行
    if kill -0 "${PID}" 2>/dev/null; then
        echo "  SIGTERM 无效，使用 SIGKILL..."
        kill -9 "${PID}" 2>/dev/null
        sleep 1
    fi
    
    # 再次确认
    if kill -0 "${PID}" 2>/dev/null; then
        echo "  ❌ 无法终止进程 ${PID}"
        exit 1
    else
        echo "  ✅ 进程 ${PID} 已终止"
    fi
else
    echo "  无需终止（无运行中进程）"
fi

# 确保端口已释放
sleep 1

# ---- 3. 重启 Jupyter (参考 crontab 配置) ----
echo ""
echo "[3/3] 启动 Jupyter Notebook..."

# 与 crontab 使用完全相同的启动命令
nohup /home/chenbh17/anaconda3/bin/jupyter-notebook / --allow-root >> "${LOG_FILE}" 2>&1 &

NEW_PID=$!
sleep 3

# 验证是否启动成功
if kill -0 "${NEW_PID}" 2>/dev/null; then
    echo "  ✅ Jupyter Notebook 已启动 (PID: ${NEW_PID})"
else
    echo "  ❌ Jupyter Notebook 启动失败，请检查日志: ${LOG_FILE}"
    exit 1
fi

echo ""
echo "========================================"
echo "  重启完成！访问地址: http://localhost:${PORT}"
echo "  日志文件: ${LOG_FILE}"
echo "========================================"
