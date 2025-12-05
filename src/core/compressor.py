#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频压缩器模块

实现视频压缩的核心逻辑
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Tuple, Dict, Any

from src.config.defaults import (
    RESULT_SUCCESS,
    RESULT_SKIP_SIZE,
    RESULT_SKIP_EXISTS,
    SUPPORTED_VIDEO_EXTENSIONS,
)
from src.core.video import get_bitrate, get_resolution, get_codec
from src.core.encoder import (
    build_encoding_commands,
    execute_ffmpeg,
    calculate_target_bitrate,
)


def get_video_files(input_folder: str) -> list:
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


def resolve_output_paths(
    filepath: str,
    input_folder: str,
    output_folder: str,
    keep_structure: bool = True
) -> Tuple[str, str]:
    """
    根据输入文件和配置生成输出路径和临时路径
    
    Returns:
        (最终输出文件路径, 临时文件路径)
    """
    if keep_structure:
        relative_path = os.path.relpath(filepath, input_folder)
        output_path = Path(output_folder) / Path(relative_path).with_suffix('.mp4')
    else:
        base_name = Path(filepath).stem + ".mp4"
        output_path = Path(output_folder) / base_name
    
    new_filename = str(output_path)
    temp_filename = os.path.join(os.path.dirname(new_filename), "tmp_" + os.path.basename(new_filename))
    return new_filename, temp_filename


def compress_video(
    filepath: str,
    input_folder: str,
    output_folder: str,
    keep_structure: bool = True,
    force_bitrate: bool = False,
    forced_bitrate: int = 0,
    min_file_size_mb: int = 100,
    hw_accel: str = "auto",
    output_codec: str = "hevc",
    enable_software_encoding: bool = False,
    limit_fps_software_decode: bool = True,
    limit_fps_software_encode: bool = True,
    max_fps: int = 30
) -> Tuple[Any, str, Dict[str, Any]]:
    """
    压缩单个视频文件
    
    Args:
        filepath: 输入文件路径
        input_folder: 输入文件夹根路径
        output_folder: 输出文件夹根路径
        keep_structure: 是否保持目录结构
        force_bitrate: 是否强制码率
        forced_bitrate: 强制码率值
        min_file_size_mb: 最小文件大小阈值（MB）
        hw_accel: 硬件加速类型
        output_codec: 输出视频编码格式
        enable_software_encoding: 是否启用软件编码回退
        limit_fps_software_decode: 软件解码时是否限制帧率
        limit_fps_software_encode: 软件编码时是否限制帧率
        max_fps: 最大帧率
        
    Returns:
        (结果状态, 错误信息, 统计信息字典)
    """
    stats = {
        "original_size": 0,
        "new_size": 0,
        "original_bitrate": 0,
        "new_bitrate": 0
    }
    
    try:
        # 检查文件大小
        file_size = os.path.getsize(filepath)
        stats["original_size"] = file_size
        
        if file_size < min_file_size_mb * 1024 * 1024:
            logging.info(f"[跳过] 文件小于 {min_file_size_mb}MB: {filepath}")
            return RESULT_SKIP_SIZE, None, stats
        
        # 获取视频信息
        original_bitrate = get_bitrate(filepath)
        width, height = get_resolution(filepath)
        source_codec = get_codec(filepath)
        stats["original_bitrate"] = original_bitrate
        
        # 计算目标码率
        new_bitrate = calculate_target_bitrate(
            original_bitrate, width, height,
            force_bitrate, forced_bitrate
        )
        stats["new_bitrate"] = new_bitrate
        
        # 确定输出文件路径
        new_filename, temp_filename = resolve_output_paths(
            filepath, input_folder, output_folder, keep_structure
        )
        new_dirname = os.path.dirname(new_filename)
        os.makedirs(new_dirname, exist_ok=True)
        
        # 检查输出文件是否已存在
        if os.path.exists(new_filename):
            logging.info(f"[跳过] 输出文件已存在: {new_filename}")
            return RESULT_SKIP_EXISTS, None, stats
        
        # 构建编码命令
        encoding_commands = build_encoding_commands(
            filepath, temp_filename, new_bitrate, source_codec,
            hw_accel=hw_accel,
            output_codec=output_codec,
            enable_software_encoding=enable_software_encoding,
            limit_fps_software_decode=limit_fps_software_decode,
            limit_fps_software_encode=limit_fps_software_encode,
            max_fps=max_fps
        )
        
        # 逐一尝试编码命令
        success = False
        last_error = None
        
        for i, cmd_info in enumerate(encoding_commands):
            logging.info(f"[尝试] 方法 {i+1}/{len(encoding_commands)} ({cmd_info['name']}): {os.path.basename(filepath)}")
            success, error = execute_ffmpeg(cmd_info["cmd"])
            
            if success:
                logging.info(f"[成功] 使用 {cmd_info['name']} 完成压缩")
                break
            else:
                last_error = error
                logging.warning(f"[失败] {cmd_info['name']}: {error}")
                
                # 清理临时文件
                if os.path.exists(temp_filename):
                    try:
                        os.remove(temp_filename)
                    except Exception as e:
                        logging.error(f"删除临时文件失败: {e}")
        
        if not success:
            error_msg = f"所有编码方法均失败。最后错误: {last_error}"
            logging.error(f"[错误] {filepath}: {error_msg}")
            return error_msg, last_error, stats
        
        # 压缩成功，移动临时文件到目标位置（支持跨文件系统）
        try:
            shutil.move(temp_filename, new_filename)
        except Exception as e:
            logging.error(f"文件移动失败 {temp_filename} -> {new_filename}: {e}")
            raise
        
        # 获取新文件大小
        new_size = os.path.getsize(new_filename)
        stats["new_size"] = new_size
        
        # 计算压缩率
        compression_ratio = (1 - new_size / file_size) * 100 if file_size > 0 else 0
        
        logging.info(
            f"[完成] {os.path.basename(filepath)} | "
            f"码率: {original_bitrate/1000:.0f}k -> {new_bitrate/1000:.0f}k | "
            f"大小: {file_size/1024/1024:.1f}MB -> {new_size/1024/1024:.1f}MB | "
            f"压缩率: {compression_ratio:.1f}%"
        )
        
        return RESULT_SUCCESS, None, stats
        
    except Exception as e:
        logging.error(f"[异常] 处理 {filepath} 时发生错误: {e}")
        
        # 清理临时文件
        if 'temp_filename' in locals() and os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except:
                pass
        
        return str(e), str(e), stats
