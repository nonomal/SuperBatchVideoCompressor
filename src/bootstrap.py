#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动准备模块

统一处理编码、缓存/临时文件清理、日志初始化、信号/进程管理、编码器可用性检测。
"""

import sys
import io
import logging
from typing import Dict, Any

from src.utils.process import (
    cleanup_temp_files,
    setup_signal_handlers,
)
from src.utils.logging import setup_logging
from src.utils.encoder_check import detect_available_encoders


def enforce_utf8_windows() -> None:
    """在 Windows 强制 stdout/stderr 使用 UTF-8，避免中文乱码"""
    if sys.platform != 'win32':
        return
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def prepare_environment(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    启动前统一准备工作：编码、临时文件清理、日志初始化、信号处理、编码器检测。

    注意：pycache 清理在 main.py 中执行（必须在导入模块之前）

    Args:
        config: 已加载并应用 CLI 覆盖的配置

    Returns:
        更新后的配置（含编码器可用性检测结果）
    """
    enforce_utf8_windows()

    # 信号处理需尽早注册
    setup_signal_handlers()

    # 日志初始化
    log_folder = config["paths"]["log"]
    setup_logging(log_folder)

    # 启动时清理输出目录中的临时文件
    output_folder = config["paths"]["output"]
    cleaned = cleanup_temp_files(output_folder)
    if cleaned > 0:
        logging.info(f"启动清理: 删除 {cleaned} 个临时文件")

    # 编码器可用性检测
    logging.info("检测编码器可用性...")
    encoder_configs = config.get("encoders", {})
    checked_configs = detect_available_encoders(encoder_configs)
    config["encoders"] = checked_configs

    return config
