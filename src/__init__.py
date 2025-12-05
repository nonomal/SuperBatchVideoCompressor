# SBVC - 超级批量视频压缩器
"""
SBVC (Super Batch Video Compressor) 包

主要模块:
- config: 配置加载
- core: 核心编码逻辑
- scheduler: 多 GPU 调度
- utils: 工具函数
"""

__version__ = "2.0.0"
__author__ = "BlueSkyXN"

from src.config import load_config, apply_cli_overrides
from src.core import compress_video, get_video_files
from src.scheduler import HybridScheduler, create_scheduler_from_config

__all__ = [
    "__version__",
    "load_config",
    "apply_cli_overrides",
    "compress_video",
    "get_video_files",
    "HybridScheduler",
    "create_scheduler_from_config",
]
