#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进程管理模块

管理 FFmpeg 子进程，支持优雅退出时清理所有进程
"""

import os
import signal
import logging
import threading
from typing import Set

# 全局进程集合和锁
_ffmpeg_processes: Set = set()
_process_lock = threading.Lock()
_shutdown_requested = False


def register_process(process) -> None:
    """
    注册一个 FFmpeg 进程到全局集合
    
    Args:
        process: subprocess.Popen 对象
    """
    with _process_lock:
        _ffmpeg_processes.add(process)


def unregister_process(process) -> None:
    """
    从全局集合中移除一个 FFmpeg 进程
    
    Args:
        process: subprocess.Popen 对象
    """
    with _process_lock:
        _ffmpeg_processes.discard(process)


def is_shutdown_requested() -> bool:
    """
    检查是否请求了关闭
    
    Returns:
        是否请求了关闭
    """
    return _shutdown_requested


def terminate_all_ffmpeg() -> None:
    """
    终止所有注册的 FFmpeg 进程
    """
    global _shutdown_requested
    _shutdown_requested = True
    
    with _process_lock:
        processes = list(_ffmpeg_processes)
    
    if not processes:
        return
    
    logging.info(f"正在终止 {len(processes)} 个 FFmpeg 进程...")
    
    for process in processes:
        try:
            if process.poll() is None:  # 进程仍在运行
                process.terminate()
                logging.debug(f"已发送 SIGTERM 到进程 {process.pid}")
        except Exception as e:
            logging.warning(f"终止进程时出错: {e}")
    
    # 等待进程退出，如果超时则强制杀死
    import time
    for process in processes:
        try:
            if process.poll() is None:
                process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                logging.debug(f"已发送 SIGKILL 到进程 {process.pid}")
            except Exception:
                pass
    
    logging.info("所有 FFmpeg 进程已终止")


def cleanup_temp_files(output_folder: str) -> int:
    """
    清理输出目录中的临时文件
    
    Args:
        output_folder: 输出文件夹路径
        
    Returns:
        清理的文件数量
    """
    if not os.path.exists(output_folder):
        return 0
    
    cleaned_count = 0
    for root, _, files in os.walk(output_folder):
        for file in files:
            if file.startswith("tmp_") and file.endswith(".mp4"):
                filepath = os.path.join(root, file)
                try:
                    os.remove(filepath)
                    logging.info(f"[清理] 删除临时文件: {filepath}")
                    cleaned_count += 1
                except Exception as e:
                    logging.warning(f"[清理] 删除临时文件失败 {filepath}: {e}")
    
    return cleaned_count


def setup_signal_handlers() -> None:
    """
    设置信号处理器，捕获 SIGINT (Ctrl+C) 和 SIGTERM
    """
    def signal_handler(signum, frame):
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        logging.warning(f"收到 {sig_name} 信号，正在清理...")
        terminate_all_ffmpeg()
        raise KeyboardInterrupt()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
