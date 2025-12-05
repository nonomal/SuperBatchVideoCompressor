#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志配置模块
"""

import os
import sys
import logging
import datetime


def setup_logging(log_folder: str) -> str:
    """
    配置日志记录器，同时输出到文件和控制台
    
    Args:
        log_folder: 日志文件夹路径
        
    Returns:
        日志文件路径
    """
    # 确保日志文件夹存在
    os.makedirs(log_folder, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    log_file = os.path.join(log_folder, f'transcoding_{timestamp}.log')
    
    # 创建格式化器
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # 配置根日志记录器
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 清除已有的处理器
    logger.handlers.clear()
    
    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return log_file
