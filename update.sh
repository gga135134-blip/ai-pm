#!/bin/bash
# 一键更新脚本 - 每次改完代码后在服务器上运行
cd "$(dirname "$0")"

echo "📥 拉取最新代码..."
git pull

echo "📦 更新依赖..."
pip install -r requirements.txt -q

echo "🔄 重启服务..."
# 如果用 PM2 管理进程
if command -v pm2 &> /dev/null; then
    pm2 restart ai-pm
else
    echo "请手动重启服务"
fi

echo "✅ 更新完成！"
