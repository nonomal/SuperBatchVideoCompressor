#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SBVC (Super Batch Video Compressor) - 主入口

简洁的主入口脚本
"""

import sys

# 启动前清理 Python 缓存，避免旧的 .pyc 文件导致类定义不一致
# 必须在导入其他模块之前执行
from src.utils.process import cleanup_pycache
cleanup_pycache()

from cli import main


if __name__ == "__main__":
    sys.exit(main())
