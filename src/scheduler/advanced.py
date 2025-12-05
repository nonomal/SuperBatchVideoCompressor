#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级多编码器调度系统

设计思路：
1. 硬件编码器（NVENC/QSV）同时并发处理不同文件
2. 失败任务进入"降级队列"，按以下顺序重试：
   - 同编码器软解+硬编
   - 其他硬件编码器
   - CPU 软编码（兜底）
3. 每个编码器有独立的并发槽位和等候队列

调度流程：
┌─────────────────────────────────────────────────────────────┐
│                        任务入口                              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  第一层：硬解+硬编（优先分配到有空闲槽位的编码器）            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ NVENC (3槽) │  │ QSV (2槽)   │  │ VT (macOS)  │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
          ↓ 失败                ↓ 失败
┌─────────────────────────────────────────────────────────────┐
│  第二层：软解+硬编（在当前编码器重试）                        │
│  - 限帧率版本                                                │
│  - 不限帧率版本                                              │
└─────────────────────────────────────────────────────────────┘
          ↓ 仍然失败
┌─────────────────────────────────────────────────────────────┐
│  第三层：移交其他硬件编码器（进入其等候队列）                  │
│  NVENC失败 → 移交QSV                                        │
│  QSV失败 → 移交NVENC（如果配置了双向回退）                   │
└─────────────────────────────────────────────────────────────┘
          ↓ 所有硬件编码器失败
┌─────────────────────────────────────────────────────────────┐
│  第四层：CPU 软编码兜底                                      │
└─────────────────────────────────────────────────────────────┘
"""

import threading
import logging
import queue
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any, Set
from concurrent.futures import ThreadPoolExecutor, Future


class EncoderType(Enum):
    """编码器类型"""
    NVENC = "nvenc"
    QSV = "qsv"
    VIDEOTOOLBOX = "videotoolbox"
    CPU = "cpu"


class DecodeMode(Enum):
    """解码模式"""
    HW_DECODE = "hw_decode"           # 硬件解码
    SW_DECODE_LIMITED = "sw_decode_limited"  # 软件解码+限帧
    SW_DECODE = "sw_decode"           # 软件解码


@dataclass
class TaskState:
    """任务状态"""
    filepath: str
    task_id: int
    current_encoder: Optional[EncoderType] = None
    current_decode_mode: DecodeMode = DecodeMode.HW_DECODE
    tried_encoders: Set[EncoderType] = field(default_factory=set)
    tried_modes: Dict[EncoderType, Set[DecodeMode]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 10  # 防止无限循环


@dataclass
class TaskResult:
    """任务结果"""
    success: bool
    filepath: str = ""
    encoder_used: Optional[EncoderType] = None
    decode_mode: Optional[DecodeMode] = None
    error: Optional[str] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    retry_history: List[str] = field(default_factory=list)


class EncoderSlot:
    """编码器槽位管理"""
    
    def __init__(self, encoder_type: EncoderType, max_concurrent: int):
        self.encoder_type = encoder_type
        self.max_concurrent = max_concurrent
        self.semaphore = threading.Semaphore(max_concurrent)
        self.current_tasks = 0
        self.total_completed = 0
        self.total_failed = 0
        self._lock = threading.Lock()
        self.logger = logging.getLogger(f"Slot.{encoder_type.value}")
    
    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """获取槽位"""
        acquired = self.semaphore.acquire(blocking=blocking, timeout=timeout)
        if acquired:
            with self._lock:
                self.current_tasks += 1
                self.logger.debug(f"槽位获取 ({self.current_tasks}/{self.max_concurrent})")
        return acquired
    
    def release(self, success: bool = True):
        """释放槽位"""
        with self._lock:
            self.current_tasks -= 1
            if success:
                self.total_completed += 1
            else:
                self.total_failed += 1
            self.logger.debug(f"槽位释放 ({self.current_tasks}/{self.max_concurrent})")
        self.semaphore.release()
    
    def can_accept(self) -> bool:
        """检查是否有空闲槽位"""
        with self._lock:
            return self.current_tasks < self.max_concurrent
    
    def get_load(self) -> float:
        """获取负载率"""
        with self._lock:
            return self.current_tasks / self.max_concurrent if self.max_concurrent > 0 else 1.0
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计"""
        with self._lock:
            return {
                "encoder": self.encoder_type.value,
                "current": self.current_tasks,
                "max": self.max_concurrent,
                "completed": self.total_completed,
                "failed": self.total_failed,
            }


class AdvancedScheduler:
    """
    高级多编码器调度器
    
    特性：
    1. 多编码器并发：NVENC 和 QSV 同时处理不同文件
    2. 智能回退：失败任务进入降级队列
    3. 负载均衡：优先分配到负载低的编码器
    """
    
    def __init__(
        self,
        encoder_configs: Dict[str, Dict[str, Any]],
        max_total_concurrent: int = 5,
        cpu_fallback: bool = True,
        priority_order: Optional[List[str]] = None
    ):
        """
        初始化调度器
        
        Args:
            encoder_configs: 编码器配置 {
                "nvenc": {"enabled": True, "max_concurrent": 3},
                "qsv": {"enabled": True, "max_concurrent": 2},
            }
            max_total_concurrent: 总并发上限
            cpu_fallback: 是否启用 CPU 兜底
            priority_order: 编码器优先级 ["nvenc", "qsv", "cpu"]
        """
        self.max_total_concurrent = max_total_concurrent
        self.total_semaphore = threading.Semaphore(max_total_concurrent)
        self.cpu_fallback = cpu_fallback
        self.priority_order = priority_order or ["nvenc", "videotoolbox", "qsv"]
        
        self._lock = threading.Lock()
        self._shutdown = False
        self._task_counter = 0
        
        self.logger = logging.getLogger("AdvancedScheduler")
        
        # 初始化编码器槽位
        self.encoder_slots: Dict[EncoderType, EncoderSlot] = {}
        self.enabled_encoders: List[EncoderType] = []
        
        for name in self.priority_order:
            if name in encoder_configs:
                config = encoder_configs[name]
                if config.get("enabled", False):
                    encoder_type = EncoderType(name)
                    max_concurrent = config.get("max_concurrent", 2)
                    self.encoder_slots[encoder_type] = EncoderSlot(encoder_type, max_concurrent)
                    self.enabled_encoders.append(encoder_type)
        
        # CPU 兜底
        if cpu_fallback:
            cpu_config = encoder_configs.get("cpu", {})
            max_concurrent = cpu_config.get("max_concurrent", 4)
            self.encoder_slots[EncoderType.CPU] = EncoderSlot(EncoderType.CPU, max_concurrent)
        
        self.logger.info(
            f"调度器初始化: 编码器={[e.value for e in self.enabled_encoders]}, "
            f"CPU兜底={cpu_fallback}, 总并发={max_total_concurrent}"
        )
    
    def _select_encoder(self, task_state: TaskState) -> Optional[EncoderType]:
        """
        选择编码器
        
        策略：
        1. 首次任务：选择负载最低的可用编码器
        2. 重试任务：选择未尝试过的编码器
        """
        with self._lock:
            # 过滤已尝试过所有模式的编码器
            available = []
            for encoder_type in self.enabled_encoders:
                if encoder_type in task_state.tried_encoders:
                    # 检查是否所有模式都试过了
                    tried_modes = task_state.tried_modes.get(encoder_type, set())
                    if len(tried_modes) >= 3:  # HW_DECODE, SW_DECODE_LIMITED, SW_DECODE
                        continue
                
                slot = self.encoder_slots.get(encoder_type)
                if slot and slot.can_accept():
                    available.append((encoder_type, slot.get_load()))
            
            if not available:
                # 尝试 CPU 兜底
                if self.cpu_fallback and EncoderType.CPU not in task_state.tried_encoders:
                    cpu_slot = self.encoder_slots.get(EncoderType.CPU)
                    if cpu_slot and cpu_slot.can_accept():
                        return EncoderType.CPU
                return None
            
            # 选择负载最低的
            available.sort(key=lambda x: x[1])
            return available[0][0]
    
    def _get_next_decode_mode(
        self, 
        encoder_type: EncoderType, 
        task_state: TaskState
    ) -> Optional[DecodeMode]:
        """获取下一个解码模式"""
        tried = task_state.tried_modes.get(encoder_type, set())
        
        # CPU 编码只有软解模式
        if encoder_type == EncoderType.CPU:
            if DecodeMode.SW_DECODE_LIMITED not in tried:
                return DecodeMode.SW_DECODE_LIMITED
            if DecodeMode.SW_DECODE not in tried:
                return DecodeMode.SW_DECODE
            return None
        
        # 硬件编码器：硬解 → 软解限帧 → 软解
        if DecodeMode.HW_DECODE not in tried:
            return DecodeMode.HW_DECODE
        if DecodeMode.SW_DECODE_LIMITED not in tried:
            return DecodeMode.SW_DECODE_LIMITED
        if DecodeMode.SW_DECODE not in tried:
            return DecodeMode.SW_DECODE
        
        return None
    
    def schedule_task(
        self,
        filepath: str,
        encode_func: Callable[[str, EncoderType, DecodeMode], TaskResult],
        timeout: Optional[float] = None
    ) -> TaskResult:
        """
        调度单个任务
        
        Args:
            filepath: 文件路径
            encode_func: 编码函数，签名为 (filepath, encoder_type, decode_mode) -> TaskResult
            timeout: 超时时间
            
        Returns:
            TaskResult
        """
        if self._shutdown:
            return TaskResult(success=False, filepath=filepath, error="调度器已关闭")
        
        # 获取总并发槽位
        acquired = self.total_semaphore.acquire(blocking=True, timeout=timeout)
        if not acquired:
            return TaskResult(success=False, filepath=filepath, error="获取并发槽位超时")
        
        try:
            with self._lock:
                self._task_counter += 1
                task_id = self._task_counter
            
            task_state = TaskState(filepath=filepath, task_id=task_id)
            retry_history = []
            
            while task_state.retry_count < task_state.max_retries:
                # 选择编码器
                encoder_type = self._select_encoder(task_state)
                
                if encoder_type is None:
                    # 没有可用编码器，等待一会儿再试
                    if task_state.retry_count < 3:
                        time.sleep(0.5)
                        task_state.retry_count += 1
                        continue
                    break
                
                # 选择解码模式
                decode_mode = self._get_next_decode_mode(encoder_type, task_state)
                
                if decode_mode is None:
                    # 该编码器所有模式都试过了，标记并继续
                    task_state.tried_encoders.add(encoder_type)
                    task_state.retry_count += 1
                    continue
                
                # 获取编码器槽位
                slot = self.encoder_slots.get(encoder_type)
                if not slot or not slot.acquire(blocking=True, timeout=5):
                    task_state.retry_count += 1
                    continue
                
                # 记录尝试
                task_state.current_encoder = encoder_type
                task_state.current_decode_mode = decode_mode
                
                if encoder_type not in task_state.tried_modes:
                    task_state.tried_modes[encoder_type] = set()
                task_state.tried_modes[encoder_type].add(decode_mode)
                
                retry_info = f"{encoder_type.value}:{decode_mode.value}"
                retry_history.append(retry_info)
                
                self.logger.info(
                    f"[任务 {task_id}] 尝试 {retry_info} - {filepath}"
                )
                
                try:
                    # 执行编码
                    result = encode_func(filepath, encoder_type, decode_mode)
                    
                    if result.success:
                        slot.release(success=True)
                        result.retry_history = retry_history
                        result.encoder_used = encoder_type
                        result.decode_mode = decode_mode
                        return result
                    else:
                        slot.release(success=False)
                        task_state.errors.append(f"{retry_info}: {result.error}")
                        self.logger.warning(
                            f"[任务 {task_id}] {retry_info} 失败: {result.error}"
                        )
                        task_state.retry_count += 1
                        
                except Exception as e:
                    slot.release(success=False)
                    task_state.errors.append(f"{retry_info}: {str(e)}")
                    self.logger.error(f"[任务 {task_id}] 异常: {e}")
                    task_state.retry_count += 1
            
            # 所有尝试都失败
            error_msg = "; ".join(task_state.errors[-3:])  # 只保留最后3个错误
            return TaskResult(
                success=False,
                filepath=filepath,
                error=f"所有编码方式均失败: {error_msg}",
                retry_history=retry_history
            )
            
        finally:
            self.total_semaphore.release()
    
    def shutdown(self):
        """关闭调度器"""
        self._shutdown = True
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "max_total_concurrent": self.max_total_concurrent,
            "enabled_encoders": [e.value for e in self.enabled_encoders],
            "cpu_fallback": self.cpu_fallback,
            "encoder_slots": {
                encoder_type.value: slot.get_stats()
                for encoder_type, slot in self.encoder_slots.items()
            }
        }


def create_advanced_scheduler(config: Dict[str, Any]) -> AdvancedScheduler:
    """从配置创建高级调度器"""
    encoders_config = config.get("encoders", {})
    scheduler_config = config.get("scheduler", {})
    
    # 构建编码器配置
    encoder_configs = {}
    enabled_list = encoders_config.get("enabled", [])
    
    for name in ["nvenc", "qsv", "videotoolbox", "cpu"]:
        cfg = encoders_config.get(name, {})
        encoder_configs[name] = {
            "enabled": name in enabled_list or cfg.get("enabled", False),
            "max_concurrent": cfg.get("max_concurrent", 2),
        }
    
    # CPU 特殊处理
    if encoders_config.get("cpu_fallback", True):
        encoder_configs["cpu"]["enabled"] = True
    
    return AdvancedScheduler(
        encoder_configs=encoder_configs,
        max_total_concurrent=scheduler_config.get("max_total_concurrent", 5),
        cpu_fallback=encoders_config.get("cpu_fallback", True),
        priority_order=enabled_list if enabled_list else None
    )
