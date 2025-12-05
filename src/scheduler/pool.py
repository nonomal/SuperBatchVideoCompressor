#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编码器池模块

管理单个编码器的并发任务
"""

import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from enum import Enum


class EncoderType(Enum):
    """编码器类型枚举"""
    NVENC = "nvenc"
    QSV = "qsv"
    VIDEOTOOLBOX = "videotoolbox"
    CPU = "cpu"


@dataclass
class EncoderConfig:
    """编码器配置"""
    encoder_type: EncoderType
    enabled: bool = True
    max_concurrent: int = 2
    device: Optional[int] = None
    fallback_to: Optional[str] = None
    preset: str = "medium"  # 仅 CPU 使用


@dataclass
class TaskResult:
    """任务结果"""
    success: bool
    encoder_used: Optional[EncoderType] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    fallback_chain: list = field(default_factory=list)


class EncoderPool:
    """
    单个编码器的任务池
    
    管理特定编码器的并发任务数量
    """
    
    def __init__(self, config: EncoderConfig):
        """
        初始化编码器池
        
        Args:
            config: 编码器配置
        """
        self.config = config
        self.encoder_type = config.encoder_type
        self.max_concurrent = config.max_concurrent
        self.semaphore = threading.Semaphore(config.max_concurrent)
        self.current_tasks = 0
        self.total_completed = 0
        self.total_failed = 0
        self._lock = threading.Lock()
        self.logger = logging.getLogger(f"EncoderPool.{self.encoder_type.value}")
    
    def can_accept_task(self) -> bool:
        """检查是否可以接受新任务"""
        with self._lock:
            return self.current_tasks < self.max_concurrent
    
    def acquire(self, blocking: bool = False, timeout: Optional[float] = None) -> bool:
        """
        获取一个任务槽位
        
        Returns:
            是否成功获取
        """
        acquired = self.semaphore.acquire(blocking=blocking, timeout=timeout)
        if acquired:
            with self._lock:
                self.current_tasks += 1
                self.logger.debug(
                    f"任务槽位已获取 ({self.current_tasks}/{self.max_concurrent})"
                )
        return acquired
    
    def release(self, success: bool = True):
        """
        释放一个任务槽位
        
        Args:
            success: 任务是否成功
        """
        with self._lock:
            self.current_tasks -= 1
            if success:
                self.total_completed += 1
            else:
                self.total_failed += 1
            self.logger.debug(
                f"任务槽位已释放 ({self.current_tasks}/{self.max_concurrent})"
            )
        self.semaphore.release()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            return {
                "encoder": self.encoder_type.value,
                "current_tasks": self.current_tasks,
                "max_concurrent": self.max_concurrent,
                "total_completed": self.total_completed,
                "total_failed": self.total_failed,
            }
