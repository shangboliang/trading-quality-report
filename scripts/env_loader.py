#!/usr/bin/env python3
"""
环境变量加载器

优先级: 系统环境变量 > .env 文件
"""

import os
from pathlib import Path


def load_env(env_file: str = None):
    """加载环境变量"""
    # 1. 先查找 .env 文件
    if env_file is None:
        # 从脚本目录向上查找
        current = Path(__file__).parent
        for _ in range(5):  # 最多向上 5 层
            env_path = current / ".env"
            if env_path.exists():
                env_file = str(env_path)
                break
            current = current.parent
            if current == current.parent:  # 到达根目录
                break
    
    # 2. 从 .env 文件加载（不覆盖已有环境变量）
    if env_file and os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                # 跳过注释和空行
                if not line or line.startswith('#'):
                    continue
                # 解析 KEY=VALUE
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # 去掉引号
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    # 只在环境变量不存在时设置
                    if key not in os.environ:
                        os.environ[key] = value


def get_api_credentials() -> tuple:
    """获取 API 凭证"""
    load_env()
    
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    
    return api_key, api_secret


if __name__ == "__main__":
    # 测试
    load_env()
    key = os.environ.get("BINANCE_API_KEY", "未设置")
    secret = os.environ.get("BINANCE_API_SECRET", "未设置")
    print(f"BINANCE_API_KEY: {key[:8]}..." if len(key) > 8 else f"BINANCE_API_KEY: {key}")
    print(f"BINANCE_API_SECRET: {secret[:8]}..." if len(secret) > 8 else f"BINANCE_API_SECRET: {secret}")
