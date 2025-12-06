#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest 配置文件
"""

import os
import sys
import pytest

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_config():
    """返回测试用配置"""
    return {
        "paths": {
            "input": "/tmp/test_input",
            "output": "/tmp/test_output",
            "log": "/tmp/test_log",
        },
        "encoding": {
            "codec": "hevc",
            "audio_bitrate": "128k",
            "bitrate": {
                "forced": 0,
                "ratio": 0.5,
                "min": 500000,
            },
        },
        "fps": {
            "max": 30,
            "limit_on_software_decode": True,
            "limit_on_software_encode": True,
        },
        "encoders": {
            "enabled": ["nvenc", "qsv"],
            "cpu_fallback": True,
            "nvenc": {"enabled": True, "max_concurrent": 2},
            "qsv": {"enabled": True, "max_concurrent": 2},
            "cpu": {"enabled": True, "max_concurrent": 2},
        },
        "scheduler": {
            "max_total_concurrent": 4,
            "strategy": "priority",
        },
        "files": {
            "min_size_mb": 10,
            "keep_structure": True,
        },
    }
