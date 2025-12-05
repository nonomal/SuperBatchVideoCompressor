# 核心模块
"""视频处理核心功能"""

from src.core.video import get_bitrate, get_resolution, get_codec
from src.core.encoder import (
    build_encoding_commands,
    execute_ffmpeg,
    build_single_encoder_commands,
    build_layered_fallback_commands,
    calculate_target_bitrate
)
from src.core.compressor import compress_video, get_video_files, resolve_output_paths

__all__ = [
    "get_bitrate",
    "get_resolution",
    "get_codec",
    "build_encoding_commands",
    "build_single_encoder_commands",
    "build_layered_fallback_commands",
    "calculate_target_bitrate",
    "execute_ffmpeg",
    "compress_video",
    "get_video_files",
    "resolve_output_paths",
]
