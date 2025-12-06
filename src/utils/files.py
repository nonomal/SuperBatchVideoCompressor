#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件操作工具模块
"""

import os
import subprocess
import logging
from typing import List

from src.config.defaults import SUPPORTED_VIDEO_EXTENSIONS


def get_video_files(input_folder: str) -> List[str]:
    """
    获取输入文件夹中的所有视频文件

    Args:
        input_folder: 输入文件夹路径

    Returns:
        视频文件路径列表
    """
    video_files = []
    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(SUPPORTED_VIDEO_EXTENSIONS):
                video_files.append(os.path.join(root, file))
    return video_files


def detect_hw_accel() -> str:
    """
    自动检测当前平台支持的硬件加速类型

    Returns:
        硬件加速类型: nvenc, videotoolbox, qsv, 或 none
    """
    import platform

    system = platform.system()

    # Mac 优先使用 VideoToolbox
    if system == "Darwin":
        return "videotoolbox"

    # Windows/Linux 尝试检测 NVIDIA GPU
    if system in ("Windows", "Linux"):
        try:
            result = subprocess.run(
                ["nvidia-smi"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return "nvenc"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 尝试检测 Intel QSV
        try:
            if system == "Linux":
                result = subprocess.run(
                    ["vainfo"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and "Intel" in result.stdout:
                    return "qsv"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return "none"


def get_hw_accel_type(hw_accel_arg: str) -> str:
    """
    获取实际使用的硬件加速类型

    Args:
        hw_accel_arg: 命令行参数值 (auto/nvenc/videotoolbox/qsv/none)

    Returns:
        实际硬件加速类型
    """
    if hw_accel_arg == "auto":
        detected = detect_hw_accel()
        logging.info(f"自动检测硬件加速: {detected}")
        return detected
    return hw_accel_arg
