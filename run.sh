#!/bin/bash
# 一键导出：抓密钥（弹管理员授权框，自动重启微信）→ 映射 → 解密 → 批量导出
# 用法:
#   ./run.sh              # 全流程
#   ./run.sh --no-hunt    # 跳过抓密钥（密钥没变时，只重新解密+导出）
#   ./run.sh --no-restart # 抓密钥但不重启微信（微信重启会中断当前使用）
set -e
cd "$(dirname "$0")"

# 1. 虚拟环境与依赖
if [ ! -x .venv/bin/python ]; then
    echo "[*] 创建虚拟环境并安装依赖 ..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi
PY=.venv/bin/python

# 2. 首次运行生成 config.json（自动检测微信数据目录）
$PY -c "from config import load_config; load_config()"

# 3. 抓取密钥（需要 root，自动弹出管理员授权框；--restart 触发全部库重开）
if [ "$1" != "--no-hunt" ]; then
    HUNT_ARGS=""
    [ "$1" = "--no-restart" ] && HUNT_ARGS="--timeout 180"
    $PY hunt_keys.py --restart $HUNT_ARGS
fi

# 4. 密钥映射 → 解密 → 批量导出
$PY map_keys.py
$PY decrypt_db.py
$PY export_all.py

echo
echo "============================================================"
echo " 完成。聊天记录在: $(pwd)/exported_all/"
echo " 总索引:          $(pwd)/exported_all/index.csv"
echo "============================================================"
