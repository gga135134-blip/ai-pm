#!/bin/bash
# AI-PM 启动脚本
cd "$(dirname "$0")"

# 安装依赖（首次或更新后运行）
pip install -r requirements.txt -q

# 启动服务（0.0.0.0 让外网可访问）
python run.py
