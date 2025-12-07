#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志配置模块，提供彩色/纯文本/JSON 输出。"""

import os
import sys
import json
import logging
import datetime
from typing import Any, Dict

# 可选彩色支持（Windows 需要 colorama 处理 ANSI 转义）
try:
    import colorama

    colorama.init()
    COLORAMA_AVAILABLE = True
except Exception:  # pragma: no cover - 可选依赖
    COLORAMA_AVAILABLE = False


LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[41m",
}
COLOR_RESET = "\033[0m"


def _resolve_level(level: Any) -> int:
    """将字符串/数字转换为日志级别。"""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return LEVEL_MAP.get(level.upper(), logging.INFO)
    return logging.INFO


def _format_context(record: logging.LogRecord) -> str:
    """从 extra 提取上下文字段，串联为简短尾部。"""
    parts = []
    for key in ("file", "enc", "decode", "attempt"):
        value = getattr(record, key, None)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    retry_history = getattr(record, "retry_history", None)
    if retry_history:
        if isinstance(retry_history, (list, tuple)):
            parts.append(f"retry={'→'.join(str(r) for r in retry_history)}")
        else:
            parts.append(f"retry={retry_history}")
    return f" ({' '.join(parts)})" if parts else ""


class ConsoleFormatter(logging.Formatter):
    """控制台格式化器，支持彩色/纯文本。"""

    def __init__(self, enable_color: bool = False):
        super().__init__()
        self.enable_color = enable_color

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname
        message = record.getMessage()
        context = _format_context(record)
        line = f"[{ts}] {level:<5} {message}{context}"

        if self.enable_color:
            color = LEVEL_COLORS.get(record.levelno, "")
            return f"{color}{line}{COLOR_RESET}"
        return line


class FileFormatter(logging.Formatter):
    """文件格式，携带上下文字段。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        message = record.getMessage()
        context = _format_context(record)
        return f"{ts} | {record.levelname:<7} | {record.name} | {message}{context}"


class JsonFormatter(logging.Formatter):
    """JSON 行格式，便于采集或 CI 解析。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        for key in ("file", "enc", "decode", "attempt", "retry_history"):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _should_use_color(stream, plain: bool) -> bool:
    """判断是否启用彩色输出。"""
    if plain:
        return False
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if sys.platform == "win32" and not COLORAMA_AVAILABLE:
        return False
    return True


def setup_logging(
    log_folder: str,
    level: Any = "INFO",
    plain: bool = False,
    json_console: bool = False,
    console_level: Any = None,
) -> str:
    """
    配置日志记录器，同时输出到文件和控制台。

    Args:
        log_folder: 日志文件夹路径
        level: 根日志级别（字符串或数字）
        plain: 控制台禁用彩色/装饰
        json_console: 控制台使用 JSON 行输出
        console_level: 控制台单独的级别，默认为 level

    Returns:
        日志文件路径
    """
    os.makedirs(log_folder, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    log_file = os.path.join(log_folder, f"transcoding_{timestamp}.log")

    root_level = _resolve_level(level)
    console_level = _resolve_level(console_level or level)

    logger = logging.getLogger()
    logger.handlers.clear()
    # 根 logger 放宽到 DEBUG，由 handler 控制输出级别
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileFormatter())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    if json_console:
        console_handler.setFormatter(JsonFormatter())
    else:
        console_handler.setFormatter(
            ConsoleFormatter(enable_color=_should_use_color(sys.stdout, plain))
        )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # 记录启动日志（仅文件/控制台当前配置可见）
    logger.log(root_level, "日志初始化完成")

    return log_file
