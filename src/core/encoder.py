#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FFmpeg 编码器模块

构建和执行 FFmpeg 编码命令
"""

import subprocess
import logging
from typing import List, Dict, Any, Tuple

from src.config.defaults import (
    HW_ENCODERS,
    SW_ENCODERS,
    AUDIO_BITRATE,
    MIN_BITRATE,
    BITRATE_RATIO,
)


def execute_ffmpeg(cmd: list) -> Tuple[bool, str]:
    """
    执行 FFmpeg 命令并检查错误
    
    Args:
        cmd: FFmpeg 命令列表
        
    Returns:
        (成功标志, 错误信息)
    """
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace"
        )
        stdout, stderr = process.communicate()
        
        # 检查是否有特定错误模式
        known_errors = [
            "Impossible to convert between the formats",
            "No such filter:",
            "Unknown encoder",
            "Cannot load nvcuda.dll",
            "No NVENC capable devices found"
        ]
        
        if process.returncode != 0:
            for error_pattern in known_errors:
                if error_pattern in stderr:
                    return False, error_pattern
            # 其他未知错误
            return False, stderr[-500:] if len(stderr) > 500 else stderr
        
        return True, None
    except Exception as e:
        return False, str(e)


def calculate_target_bitrate(
    original_bitrate: int, 
    width: int, 
    height: int,
    force_bitrate: bool = False, 
    forced_value: int = 0
) -> int:
    """
    计算目标码率
    
    Args:
        original_bitrate: 原始码率
        width: 视频宽度
        height: 视频高度
        force_bitrate: 是否强制使用指定码率
        forced_value: 强制码率值
        
    Returns:
        目标码率
    """
    if force_bitrate:
        # 用户强制码率优先，直接返回
        return forced_value
    
    # 根据分辨率确定最大码率上限（短边分档封顶）
    short_side = min(width, height)
    if short_side <= 720:
        max_bitrate = 1500000
    elif short_side <= 1080:
        max_bitrate = 3000000
    elif short_side <= 1440:
        max_bitrate = 5000000
    else:
        max_bitrate = 9000000
    
    # 自动码率 = 原始码率 * 压缩比例
    new_bitrate = int(original_bitrate * BITRATE_RATIO)
    
    # 在最低码率与分辨率封顶之间夹紧
    new_bitrate = max(MIN_BITRATE, min(new_bitrate, max_bitrate))
    
    return new_bitrate


def build_encoding_commands(
    filepath: str, 
    temp_filename: str,
    bitrate: int, 
    source_codec: str,
    hw_accel: str = "auto",
    output_codec: str = "hevc",
    enable_software_encoding: bool = False,
    limit_fps_software_decode: bool = True,
    limit_fps_software_encode: bool = True,
    max_fps: int = 30
) -> List[Dict[str, Any]]:
    """
    构建编码命令列表（按优先级排序）
    
    编码策略（按优先级）：
    1. 硬件全加速模式：硬件解码 + 硬件编码
    2. 混合模式：软件解码 + 硬件编码（可限帧率）
    3. [可选] 纯软件模式：软件解码 + 软件编码（可限帧率）
    
    Args:
        filepath: 输入文件路径
        temp_filename: 临时输出文件路径
        bitrate: 目标码率
        source_codec: 源视频编码格式
        hw_accel: 硬件加速类型
        output_codec: 输出视频编码格式
        enable_software_encoding: 是否启用软件编码回退
        limit_fps_software_decode: 软件解码时是否限制帧率
        limit_fps_software_encode: 软件编码时是否限制帧率
        max_fps: 最大帧率
        
    Returns:
        编码命令列表
    """
    commands = []
    supported_hw_decode_codecs = ["h264", "hevc", "av1", "vp9", "mpeg2video"]
    
    # 获取硬件编码器配置
    hw_config = HW_ENCODERS.get(hw_accel, {})
    hw_encoder = hw_config.get(output_codec)
    hwaccel = hw_config.get("hwaccel")
    hwaccel_output_format = hw_config.get("hwaccel_output_format")
    
    # 获取软件编码器
    sw_encoder = SW_ENCODERS.get(output_codec, "libx264")
    
    # 编码器友好名称
    codec_names = {
        "hevc": "HEVC/H.265",
        "avc": "AVC/H.264",
        "av1": "AV1"
    }
    codec_display = codec_names.get(output_codec, output_codec.upper())
    
    # 硬件加速友好名称
    hw_names = {
        "nvenc": "NVIDIA NVENC",
        "videotoolbox": "Apple VideoToolbox",
        "qsv": "Intel QSV",
        "none": "软件"
    }
    hw_display = hw_names.get(hw_accel, hw_accel)
    
    # ========================================
    # 1. 硬件全加速模式（硬件解码 + 硬件编码）
    # ========================================
    if hw_encoder and source_codec in supported_hw_decode_codecs:
        cmd = ['ffmpeg', '-y', '-hide_banner']
        
        # 添加硬件解码参数
        if hwaccel:
            cmd.extend(['-hwaccel', hwaccel])
            if hwaccel_output_format:
                cmd.extend(['-hwaccel_output_format', hwaccel_output_format])
        
        cmd.extend([
            '-i', filepath,
            '-c:v', hw_encoder, '-b:v', str(bitrate),
            '-c:a', 'aac', '-b:a', AUDIO_BITRATE,
            temp_filename
        ])
        
        commands.append({
            "name": f"{hw_display} 全加速 ({codec_display}, 硬件解码+编码)",
            "cmd": cmd
        })
    
    # ========================================
    # 2. 混合模式（软件解码 + 硬件编码）
    # ========================================
    if hw_encoder:
        # 2a. 限制帧率版本
        if limit_fps_software_decode:
            commands.append({
                "name": f"{hw_display} 编码 ({codec_display}, 软件解码, 限{max_fps}fps)",
                "cmd": [
                    'ffmpeg', '-y', '-hide_banner',
                    '-i', filepath,
                    '-vf', f'fps={max_fps}',
                    '-c:v', hw_encoder, '-b:v', str(bitrate),
                    '-c:a', 'aac', '-b:a', AUDIO_BITRATE,
                    temp_filename
                ]
            })
        
        # 2b. 不限帧率版本（备用）
        commands.append({
            "name": f"{hw_display} 编码 ({codec_display}, 软件解码)",
            "cmd": [
                'ffmpeg', '-y', '-hide_banner',
                '-i', filepath,
                '-c:v', hw_encoder, '-b:v', str(bitrate),
                '-c:a', 'aac', '-b:a', AUDIO_BITRATE,
                temp_filename
            ]
        })
    
    # ========================================
    # 3. [可选] 纯软件编码模式
    # ========================================
    if enable_software_encoding or hw_accel == "none":
        # 编码器特定参数
        encoder_params = []
        if sw_encoder in ("libx265", "libx264"):
            encoder_params = ['-preset', 'medium']
        elif sw_encoder == "libsvtav1":
            encoder_params = ['-preset', '6']
        
        # 3a. 限制帧率版本
        if limit_fps_software_encode:
            cmd = [
                'ffmpeg', '-y', '-hide_banner',
                '-i', filepath,
                '-vf', f'fps={max_fps}',
                '-c:v', sw_encoder
            ]
            cmd.extend(encoder_params)
            cmd.extend([
                '-b:v', str(bitrate),
                '-c:a', 'aac', '-b:a', AUDIO_BITRATE,
                temp_filename
            ])
            commands.append({
                "name": f"CPU 编码 ({sw_encoder}, 限{max_fps}fps)",
                "cmd": cmd
            })
        
        # 3b. 不限帧率版本
        cmd = [
            'ffmpeg', '-y', '-hide_banner',
            '-i', filepath,
            '-c:v', sw_encoder
        ]
        cmd.extend(encoder_params)
        cmd.extend([
            '-b:v', str(bitrate),
            '-c:a', 'aac', '-b:a', AUDIO_BITRATE,
            temp_filename
        ])
        commands.append({
            "name": f"CPU 编码 ({sw_encoder})",
            "cmd": cmd
        })
        
        # 3c. 如果输出不是 AVC，添加 libx264 作为最终回退
        if output_codec != "avc":
            commands.append({
                "name": "CPU 编码 (libx264, 最大兼容回退)",
                "cmd": [
                    'ffmpeg', '-y', '-hide_banner',
                    '-i', filepath,
                    '-c:v', 'libx264', '-preset', 'medium', '-b:v', str(bitrate),
                    '-c:a', 'aac', '-b:a', AUDIO_BITRATE,
                    temp_filename
                ]
            })
    
    return commands
