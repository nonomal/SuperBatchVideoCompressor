#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
混合调度器模块

实现多编码器并行调度和智能回退
"""

import threading
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Callable, Any

from src.scheduler.pool import EncoderPool, EncoderConfig, EncoderType, TaskResult


class HybridScheduler:
    """
    混合调度器
    
    管理多个编码器池，实现智能任务分发和回退
    """
    
    def __init__(
        self,
        encoder_configs: List[EncoderConfig],
        max_total_concurrent: int = 6,
        strategy: str = "priority",
        cpu_fallback: bool = True
    ):
        """
        初始化混合调度器
        
        Args:
            encoder_configs: 编码器配置列表
            max_total_concurrent: 总并发上限
            strategy: 调度策略 (priority, least_loaded, round_robin)
            cpu_fallback: 是否启用 CPU 兜底
        """
        self.encoder_pools: Dict[EncoderType, EncoderPool] = {}
        self.fallback_chain: Dict[EncoderType, Optional[EncoderType]] = {}
        self.enabled_encoders: List[EncoderType] = []
        self.max_total_concurrent = max_total_concurrent
        self.total_semaphore = threading.Semaphore(max_total_concurrent)
        self.strategy = strategy
        self.cpu_fallback = cpu_fallback
        self._lock = threading.Lock()
        self._round_robin_index = 0
        self.logger = logging.getLogger("HybridScheduler")
        
        # 初始化编码器池
        for config in encoder_configs:
            if config.enabled:
                pool = EncoderPool(config)
                self.encoder_pools[config.encoder_type] = pool
                
                # 非 CPU 编码器加入调度列表
                if config.encoder_type != EncoderType.CPU:
                    self.enabled_encoders.append(config.encoder_type)
                
                # 构建回退链
                if config.fallback_to:
                    fallback_type = EncoderType(config.fallback_to)
                    self.fallback_chain[config.encoder_type] = fallback_type
        
        self.logger.info(
            f"调度器初始化完成: 编码器={[e.value for e in self.enabled_encoders]}, "
            f"策略={strategy}, 总并发={max_total_concurrent}"
        )
    
    def _get_next_encoder(self) -> Optional[EncoderType]:
        """根据调度策略选择下一个编码器（线程安全）"""
        with self._lock:
            if not self.enabled_encoders:
                return None
            
            # CPU 兜底作为额外候选（不参与调度策略权重，但可在没有可用硬件时被选择）
            cpu_pool = self.encoder_pools.get(EncoderType.CPU) if self.cpu_fallback else None
            cpu_available = cpu_pool and cpu_pool.can_accept_task()
            
            if self.strategy == "priority":
                for encoder_type in self.enabled_encoders:
                    pool = self.encoder_pools.get(encoder_type)
                    if pool and pool.can_accept_task():
                        return encoder_type
                if cpu_available:
                    return EncoderType.CPU
            
            elif self.strategy == "least_loaded":
                best_encoder = None
                min_load = float('inf')
                candidates = list(self.enabled_encoders)
                if cpu_available:
                    candidates.append(EncoderType.CPU)
                for encoder_type in candidates:
                    pool = self.encoder_pools.get(encoder_type)
                    if pool and pool.can_accept_task():
                        load = pool.current_tasks / pool.max_concurrent
                        if load < min_load:
                            min_load = load
                            best_encoder = encoder_type
                return best_encoder
            
            elif self.strategy == "round_robin":
                for _ in range(len(self.enabled_encoders)):
                    encoder_type = self.enabled_encoders[self._round_robin_index]
                    self._round_robin_index = (
                        self._round_robin_index + 1
                    ) % len(self.enabled_encoders)
                    pool = self.encoder_pools.get(encoder_type)
                    if pool and pool.can_accept_task():
                        return encoder_type
                if cpu_available:
                    return EncoderType.CPU
            
            return None
    
    def _get_fallback_encoder(self, current: EncoderType) -> Optional[EncoderType]:
        """获取回退编码器"""
        fallback = self.fallback_chain.get(current)
        if fallback and fallback in self.encoder_pools:
            return fallback
        
        if self.cpu_fallback and current != EncoderType.CPU:
            if EncoderType.CPU in self.encoder_pools:
                return EncoderType.CPU
        
        return None
    
    def schedule_task(
        self,
        task_func: Callable[[EncoderType], TaskResult],
        timeout: Optional[float] = None
    ) -> TaskResult:
        """
        调度单个任务
        
        Args:
            task_func: 任务函数，接收编码器类型参数，返回 TaskResult
            timeout: 获取总并发槽位的超时时间
            
        Returns:
            任务结果
        """
        acquired = self.total_semaphore.acquire(blocking=True, timeout=timeout)
        if not acquired:
            return TaskResult(success=False, error="获取总并发槽位超时")
        
        try:
            fallback_chain = []
            current_encoder = self._get_next_encoder()
            start = time.time()

            if not self.encoder_pools:
                return TaskResult(success=False, error="没有可用编码器")

            def _timed_out() -> bool:
                if timeout is None:
                    return False
                return (time.time() - start) >= timeout

            # 添加重试计数器，防止无限循环
            max_wait_attempts = 200  # 最多等待10秒（200 * 0.05秒）
            wait_attempts = 0

            while True:
                if current_encoder is None:
                    if _timed_out():
                        return TaskResult(
                            success=False,
                            error="获取编码器槽位超时",
                            fallback_chain=fallback_chain
                        )
                    wait_attempts += 1
                    if wait_attempts >= max_wait_attempts:
                        return TaskResult(
                            success=False,
                            error="等待可用编码器超时（所有编码器均忙碌）",
                            fallback_chain=fallback_chain
                        )
                    time.sleep(0.05)
                    current_encoder = self._get_next_encoder()
                    continue

                # 重置等待计数器（已找到可用编码器）
                wait_attempts = 0
                
                pool = self.encoder_pools.get(current_encoder)
                if pool is None:
                    return TaskResult(
                        success=False,
                        error="所有编码器均失败",
                        fallback_chain=fallback_chain
                    )
                
                remaining_timeout = None
                if timeout is not None:
                    elapsed = time.time() - start
                    remaining_timeout = max(0, timeout - elapsed)
                
                if not pool.acquire(blocking=True, timeout=remaining_timeout):
                    return TaskResult(
                        success=False,
                        error="获取编码器槽位超时",
                        fallback_chain=fallback_chain
                    )
                
                try:
                    self.logger.info(f"使用 {current_encoder.value} 编码器处理任务")
                    fallback_chain.append(current_encoder.value)
                    
                    result = task_func(current_encoder)
                    result.fallback_chain = fallback_chain
                    
                    if result.success:
                        pool.release(success=True)
                        result.encoder_used = current_encoder
                        return result
                    else:
                        pool.release(success=False)
                        self.logger.warning(f"{current_encoder.value} 编码失败: {result.error}")
                        next_encoder = self._get_fallback_encoder(current_encoder)
                        if next_encoder is None:
                            # 没有更多回退选项，立即返回失败
                            return TaskResult(
                                success=False,
                                error=f"所有编码器均失败，最后错误: {result.error}",
                                fallback_chain=fallback_chain
                            )
                        current_encoder = next_encoder
                        
                except Exception as e:
                    pool.release(success=False)
                    self.logger.error(f"任务执行异常: {e}")
                    next_encoder = self._get_fallback_encoder(current_encoder)
                    if next_encoder is None:
                        return TaskResult(
                            success=False,
                            error=f"所有编码器均失败，异常: {e}",
                            fallback_chain=fallback_chain
                        )
                    current_encoder = next_encoder
            
            # 理论上不会到达这里
            return TaskResult(
                success=False,
                error="所有编码器均失败",
                fallback_chain=fallback_chain
            )
            
        finally:
            self.total_semaphore.release()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取调度器统计信息"""
        return {
            "strategy": self.strategy,
            "max_total_concurrent": self.max_total_concurrent,
            "enabled_encoders": [e.value for e in self.enabled_encoders],
            "cpu_fallback": self.cpu_fallback,
            "encoder_pools": {
                encoder_type.value: pool.get_stats()
                for encoder_type, pool in self.encoder_pools.items()
            }
        }


class BatchScheduler:
    """批量任务调度器"""
    
    def __init__(self, hybrid_scheduler: HybridScheduler, max_workers: Optional[int] = None):
        self.scheduler = hybrid_scheduler
        self.max_workers = max_workers or hybrid_scheduler.max_total_concurrent
        self.logger = logging.getLogger("BatchScheduler")
    
    def process_batch(
        self,
        tasks: List[Dict[str, Any]],
        task_func: Callable[[Dict[str, Any], EncoderType], TaskResult],
        progress_callback: Optional[Callable[[int, int, TaskResult], None]] = None
    ) -> List[TaskResult]:
        """批量处理任务"""
        results = []
        total = len(tasks)
        completed = 0
        lock = threading.Lock()
        
        def process_single(task: Dict[str, Any]) -> TaskResult:
            nonlocal completed
            
            def encoder_task(encoder_type: EncoderType) -> TaskResult:
                return task_func(task, encoder_type)
            
            result = self.scheduler.schedule_task(encoder_task)
            
            with lock:
                completed += 1
                if progress_callback:
                    progress_callback(completed, total, result)
            
            return result
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(process_single, task) for task in tasks]
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append(TaskResult(success=False, error=str(e)))
        
        return results


def create_scheduler_from_config(config: Dict[str, Any]) -> HybridScheduler:
    """
    从配置字典创建调度器
    
    Args:
        config: 配置字典（从 YAML/JSON 加载）
        
    Returns:
        HybridScheduler 实例
    """
    encoder_configs = []
    encoders_config = config.get("encoders", {})
    scheduler_config = config.get("scheduler", {})
    
    encoder_mapping = {
        "nvenc": EncoderType.NVENC,
        "qsv": EncoderType.QSV,
        "videotoolbox": EncoderType.VIDEOTOOLBOX,
        "cpu": EncoderType.CPU,
    }
    
    enabled_list = encoders_config.get("enabled")
    
    for name, encoder_type in encoder_mapping.items():
        encoder_cfg = encoders_config.get(name, {})
        
        # 默认根据 enabled 列表决定开关；显式配置的 enabled 优先级更高
        enabled = (name in enabled_list) if enabled_list is not None else False
        if "enabled" in encoder_cfg:
            enabled = bool(encoder_cfg["enabled"])
        
        if name == "cpu":
            cpu_fallback_enabled = encoders_config.get("cpu_fallback", True)
            # CPU 是否开启由 cpu_fallback 控制，显式 enabled=False 可以关闭
            enabled = cpu_fallback_enabled if "enabled" not in encoder_cfg else bool(encoder_cfg["enabled"]) and cpu_fallback_enabled
        
        encoder_configs.append(EncoderConfig(
            encoder_type=encoder_type,
            enabled=enabled,
            max_concurrent=encoder_cfg.get("max_concurrent", 2),
            device=encoder_cfg.get("device"),
            fallback_to=encoder_cfg.get("fallback_to"),
            preset=encoder_cfg.get("preset", "medium"),
        ))
    
    return HybridScheduler(
        encoder_configs=encoder_configs,
        max_total_concurrent=scheduler_config.get("max_total_concurrent", 6),
        strategy=scheduler_config.get("strategy", "priority"),
        cpu_fallback=encoders_config.get("cpu_fallback", True),
    )
