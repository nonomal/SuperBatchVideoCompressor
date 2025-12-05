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
    from src.utils.process import register_process, unregister_process, is_shutdown_requested
    
    if is_shutdown_requested():
        return False, "程序正在退出"
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace"
        )
        register_process(process)
        try:
            stdout, stderr = process.communicate()
        finally:
            unregister_process(process)
        
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
    forced_value: int = 0,
    max_bitrate_by_resolution: dict = None
) -> int:
    """
    计算目标码率

    Args:
        original_bitrate: 原始码率
        width: 视频宽度
        height: 视频高度
        force_bitrate: 是否强制使用指定码率
        forced_value: 强制码率值
        max_bitrate_by_resolution: 根据分辨率的最大码率配置字典
            格式: {720: 1500000, 1080: 3000000, ...}

    Returns:
        目标码率
    """
    if force_bitrate:
        # 用户强制码率优先，直接返回
        return forced_value

    # 使用配置文件中的最大码率，如果没有提供则使用默认值
    if max_bitrate_by_resolution is None:
        from src.config.defaults import MAX_BITRATE_BY_RESOLUTION
        max_bitrate_by_resolution = MAX_BITRATE_BY_RESOLUTION

    # 根据分辨率确定最大码率上限（短边分档封顶）
    short_side = min(width, height)

    # 从配置中查找合适的最大码率
    # 按分辨率从小到大排序，找到第一个大于等于当前分辨率的档位
    max_bitrate = None
    for resolution_threshold in sorted(max_bitrate_by_resolution.keys()):
        if short_side <= resolution_threshold:
            max_bitrate = max_bitrate_by_resolution[resolution_threshold]
            break

    # 如果超过所有配置的档位，使用最高档位的码率
    if max_bitrate is None and max_bitrate_by_resolution:
        max_bitrate = max_bitrate_by_resolution[max(max_bitrate_by_resolution.keys())]

    # 如果还是没有，使用默认值
    if max_bitrate is None:
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


def build_single_encoder_commands(
    filepath: str,
    temp_filename: str,
    bitrate: int,
    source_codec: str,
    hw_accel: str,
    output_codec: str = "hevc",
    limit_fps_software_decode: bool = True,
    limit_fps_software_encode: bool = True,
    max_fps: int = 30
) -> List[Dict[str, Any]]:
    """
    构建单个编码器的命令列表（用于多编码器调度模式）
    
    为指定的编码器生成解码模式回退序列：
    - 硬件编码器: 硬解+硬编 → 软解+硬编(限帧) → 软解+硬编
    - CPU编码器: 软解+软编(限帧) → 软解+软编
    
    Args:
        filepath: 输入文件路径
        temp_filename: 临时输出文件路径
        bitrate: 目标码率
        source_codec: 源视频编码格式
        hw_accel: 硬件加速类型 (nvenc/qsv/videotoolbox/none)
        output_codec: 输出视频编码格式
        limit_fps_software_decode: 软件解码时是否限制帧率
        limit_fps_software_encode: 软件编码时是否限制帧率
        max_fps: 最大帧率
        
    Returns:
        编码命令列表 [{"name": str, "cmd": list}, ...]
    """
    commands = []
    supported_hw_decode_codecs = ["h264", "hevc", "av1", "vp9", "mpeg2video"]
    
    # 编码器友好名称
    codec_names = {"hevc": "HEVC/H.265", "avc": "AVC/H.264", "av1": "AV1"}
    codec_display = codec_names.get(output_codec, output_codec.upper())
    
    hw_names = {
        "nvenc": "NVIDIA NVENC",
        "videotoolbox": "Apple VideoToolbox",
        "qsv": "Intel QSV",
        "none": "CPU"
    }
    hw_display = hw_names.get(hw_accel, hw_accel)
    
    # ========================================
    # CPU/软件编码
    # ========================================
    if hw_accel == "none":
        sw_encoder = SW_ENCODERS.get(output_codec, "libx264")
        encoder_params = []
        if sw_encoder in ("libx265", "libx264"):
            encoder_params = ['-preset', 'medium']
        elif sw_encoder == "libsvtav1":
            encoder_params = ['-preset', '6']
        
        # 1. 限帧率版本
        if limit_fps_software_encode:
            cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
            cmd.extend(['-vf', f'fps={max_fps}'])
            cmd.extend(['-c:v', sw_encoder])
            cmd.extend(encoder_params)
            cmd.extend(['-b:v', str(bitrate), '-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
            commands.append({"name": f"{hw_display} ({sw_encoder}, 限{max_fps}fps)", "cmd": cmd})
        
        # 2. 不限帧率版本
        cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
        cmd.extend(['-c:v', sw_encoder])
        cmd.extend(encoder_params)
        cmd.extend(['-b:v', str(bitrate), '-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
        commands.append({"name": f"{hw_display} ({sw_encoder})", "cmd": cmd})
        
        return commands
    
    # ========================================
    # 硬件编码
    # ========================================
    hw_config = HW_ENCODERS.get(hw_accel, {})
    hw_encoder = hw_config.get(output_codec)
    hwaccel = hw_config.get("hwaccel")
    hwaccel_output_format = hw_config.get("hwaccel_output_format")
    
    if not hw_encoder:
        # 该硬件不支持此编码格式，回退到软件
        return build_single_encoder_commands(
            filepath, temp_filename, bitrate, source_codec,
            "none", output_codec, limit_fps_software_decode, limit_fps_software_encode, max_fps
        )
    
    # 1. 硬件解码 + 硬件编码（如果源编码支持硬件解码）
    if source_codec in supported_hw_decode_codecs and hwaccel:
        cmd = ['ffmpeg', '-y', '-hide_banner']
        cmd.extend(['-hwaccel', hwaccel])
        if hwaccel_output_format:
            cmd.extend(['-hwaccel_output_format', hwaccel_output_format])
        cmd.extend(['-i', filepath])
        cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
        cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
        commands.append({"name": f"{hw_display} ({codec_display}, 硬解+硬编)", "cmd": cmd})
    
    # 2. 软件解码 + 硬件编码（限帧率）
    if limit_fps_software_decode:
        cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
        cmd.extend(['-vf', f'fps={max_fps}'])
        cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
        cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
        commands.append({"name": f"{hw_display} ({codec_display}, 软解+硬编, 限{max_fps}fps)", "cmd": cmd})
    
    # 3. 软件解码 + 硬件编码（不限帧率）
    cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
    cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
    cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
    commands.append({"name": f"{hw_display} ({codec_display}, 软解+硬编)", "cmd": cmd})
    
    return commands


def build_layered_fallback_commands(
    filepath: str,
    temp_filename: str,
    bitrate: int,
    source_codec: str,
    enabled_encoders: List[str],
    output_codec: str = "hevc",
    limit_fps_software_decode: bool = True,
    limit_fps_software_encode: bool = True,
    max_fps: int = 30,
    cpu_fallback: bool = True
) -> List[Dict[str, Any]]:
    """
    构建分层回退命令列表
    
    回退策略：解码方式优先，编码器次之
    第一层：所有硬件编码器的 硬解+硬编
    第二层：所有硬件编码器的 软解+硬编
    第三层：CPU 软解+软编（如果启用）
    
    编码器优先级: nvenc > videotoolbox > qsv > cpu
    
    Args:
        filepath: 输入文件路径
        temp_filename: 临时输出文件路径
        bitrate: 目标码率
        source_codec: 源视频编码格式
        enabled_encoders: 启用的编码器列表 ["nvenc", "qsv", ...]
        output_codec: 输出视频编码格式
        limit_fps_software_decode: 软件解码时是否限制帧率
        limit_fps_software_encode: 软件编码时是否限制帧率
        max_fps: 最大帧率
        cpu_fallback: 是否启用 CPU 兜底
        
    Returns:
        分层回退命令列表 [{"name": str, "cmd": list, "encoder": str, "layer": str}, ...]
    """
    commands = []
    supported_hw_decode_codecs = ["h264", "hevc", "av1", "vp9", "mpeg2video"]
    
    # 编码器优先级排序
    priority_order = ["nvenc", "videotoolbox", "qsv"]
    hw_encoders_sorted = [e for e in priority_order if e in enabled_encoders]
    
    codec_names = {"hevc": "HEVC/H.265", "avc": "AVC/H.264", "av1": "AV1"}
    codec_display = codec_names.get(output_codec, output_codec.upper())
    
    hw_names = {
        "nvenc": "NVIDIA NVENC",
        "videotoolbox": "Apple VideoToolbox",
        "qsv": "Intel QSV",
    }
    
    # ========================================
    # 第一层：硬解+硬编（所有硬件编码器）
    # ========================================
    if source_codec in supported_hw_decode_codecs:
        for hw_accel in hw_encoders_sorted:
            hw_config = HW_ENCODERS.get(hw_accel, {})
            hw_encoder = hw_config.get(output_codec)
            hwaccel = hw_config.get("hwaccel")
            hwaccel_output_format = hw_config.get("hwaccel_output_format")
            
            if not hw_encoder or not hwaccel:
                continue
            
            cmd = ['ffmpeg', '-y', '-hide_banner']
            cmd.extend(['-hwaccel', hwaccel])
            if hwaccel_output_format:
                cmd.extend(['-hwaccel_output_format', hwaccel_output_format])
            cmd.extend(['-i', filepath])
            cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
            cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
            
            hw_display = hw_names.get(hw_accel, hw_accel)
            commands.append({
                "name": f"{hw_display} ({codec_display}, 硬解+硬编)",
                "cmd": cmd,
                "encoder": hw_accel,
                "layer": "hw_decode_hw_encode"
            })
    
    # ========================================
    # 第二层：软解+硬编 限帧率（所有硬件编码器）
    # ========================================
    if limit_fps_software_decode:
        for hw_accel in hw_encoders_sorted:
            hw_config = HW_ENCODERS.get(hw_accel, {})
            hw_encoder = hw_config.get(output_codec)
            
            if not hw_encoder:
                continue
            
            hw_display = hw_names.get(hw_accel, hw_accel)
            cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
            cmd.extend(['-vf', f'fps={max_fps}'])
            cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
            cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
            commands.append({
                "name": f"{hw_display} ({codec_display}, 软解+硬编, 限{max_fps}fps)",
                "cmd": cmd,
                "encoder": hw_accel,
                "layer": "sw_decode_hw_encode_limited"
            })
    
    # ========================================
    # 第三层：软解+硬编 不限帧率（所有硬件编码器）
    # ========================================
    for hw_accel in hw_encoders_sorted:
        hw_config = HW_ENCODERS.get(hw_accel, {})
        hw_encoder = hw_config.get(output_codec)
        
        if not hw_encoder:
            continue
        
        hw_display = hw_names.get(hw_accel, hw_accel)
        cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
        cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
        cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
        commands.append({
            "name": f"{hw_display} ({codec_display}, 软解+硬编)",
            "cmd": cmd,
            "encoder": hw_accel,
            "layer": "sw_decode_hw_encode"
        })
    
    # ========================================
    # 第三层：CPU 软解+软编（兜底）
    # ========================================
    if cpu_fallback:
        sw_encoder = SW_ENCODERS.get(output_codec, "libx264")
        encoder_params = []
        if sw_encoder in ("libx265", "libx264"):
            encoder_params = ['-preset', 'medium']
        elif sw_encoder == "libsvtav1":
            encoder_params = ['-preset', '6']
        
        # 软解+软编（限帧率）
        if limit_fps_software_encode:
            cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
            cmd.extend(['-vf', f'fps={max_fps}'])
            cmd.extend(['-c:v', sw_encoder])
            cmd.extend(encoder_params)
            cmd.extend(['-b:v', str(bitrate), '-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
            commands.append({
                "name": f"CPU ({sw_encoder}, 限{max_fps}fps)",
                "cmd": cmd,
                "encoder": "cpu",
                "layer": "sw_decode_sw_encode_limited"
            })
        
        # 软解+软编（不限帧率）
        cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
        cmd.extend(['-c:v', sw_encoder])
        cmd.extend(encoder_params)
        cmd.extend(['-b:v', str(bitrate), '-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
        commands.append({
            "name": f"CPU ({sw_encoder})",
            "cmd": cmd,
            "encoder": "cpu",
            "layer": "sw_decode_sw_encode"
        })
    
    return commands
