#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SBVC (Super Batch Video Compressor) - CLI 入口

命令行参数解析和运行模式选择
"""

import os
import sys
import io
import logging
import argparse
import concurrent.futures
from typing import Dict, Any

# 强制使用UTF-8编码，解决Windows环境下中文输出问题
if sys.platform == 'win32':
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 确保可以导入 src 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_config, apply_cli_overrides
from src.config.defaults import (
    DEFAULT_INPUT_FOLDER,
    DEFAULT_OUTPUT_FOLDER,
    DEFAULT_LOG_FOLDER,
    DEFAULT_HW_ACCEL,
    DEFAULT_OUTPUT_CODEC,
    MIN_FILE_SIZE_MB,
    MAX_FPS,
    MAX_WORKERS,
    RESULT_SUCCESS,
    RESULT_SKIP_SIZE,
    RESULT_SKIP_EXISTS,
    RESULT_ERROR,
)
from src.core import compress_video, get_video_files, resolve_output_paths
from src.utils.logging import setup_logging
from src.utils.files import get_hw_accel_type
from src.utils.process import setup_signal_handlers, cleanup_temp_files, terminate_all_ffmpeg


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='SBVC (Super Batch Video Compressor) - 超级批量视频压缩器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用示例:
  # 基本用法
  python main.py -i /path/to/input -o /path/to/output
  
  # 多 GPU 模式
  python main.py -i ./input -o ./output --multi-gpu
  
  # 使用配置文件
  python main.py --config ./config.yaml
        '''
    )
    
    # 基本路径参数
    parser.add_argument('-i', '--input', default=DEFAULT_INPUT_FOLDER,
                        help=f'输入文件夹路径 (默认: {DEFAULT_INPUT_FOLDER})')
    parser.add_argument('-o', '--output', default=DEFAULT_OUTPUT_FOLDER,
                        help=f'输出文件夹路径 (默认: {DEFAULT_OUTPUT_FOLDER})')
    parser.add_argument('-l', '--log', default=DEFAULT_LOG_FOLDER,
                        help=f'日志文件夹路径 (默认: {DEFAULT_LOG_FOLDER})')
    
    # 编码格式选项
    parser.add_argument('--hw-accel', '--hardware',
                        choices=['auto', 'nvenc', 'videotoolbox', 'qsv', 'none'],
                        default=DEFAULT_HW_ACCEL,
                        help=f'硬件加速类型 (默认: {DEFAULT_HW_ACCEL})')
    parser.add_argument('-c', '--codec', choices=['hevc', 'avc', 'av1'],
                        default=DEFAULT_OUTPUT_CODEC,
                        help=f'输出视频编码格式 (默认: {DEFAULT_OUTPUT_CODEC})')
    
    # 处理选项
    parser.add_argument('--min-size', type=int, default=MIN_FILE_SIZE_MB,
                        help=f'最小文件大小阈值(MB) (默认: {MIN_FILE_SIZE_MB})')
    parser.add_argument('--force-bitrate', type=int, default=0,
                        help='强制使用指定码率(bps)，0 表示自动计算')
    parser.add_argument('--no-keep-structure', action='store_true',
                        help='不保持原始目录结构')
    parser.add_argument('-w', '--workers', type=int, default=MAX_WORKERS,
                        help=f'并发处理线程数 (默认: {MAX_WORKERS})')
    
    # 编码回退选项
    parser.add_argument('--enable-software-fallback', action='store_true',
                        help='启用软件编码回退')
    parser.add_argument('--cpu-fallback', action='store_true',
                        help='启用 CPU 编码回退')
    
    # 帧率限制选项
    parser.add_argument('--no-fps-limit', action='store_true',
                        help='禁用所有帧率限制')
    parser.add_argument('--no-fps-limit-decode', action='store_true',
                        help='软件解码时不限制帧率')
    parser.add_argument('--no-fps-limit-encode', action='store_true',
                        help='软件编码时不限制帧率')
    parser.add_argument('--max-fps', type=int, default=MAX_FPS,
                        help=f'最大帧率限制 (默认: {MAX_FPS})')
    
    # 配置文件选项
    parser.add_argument('--config', type=str, default=None,
                        help='配置文件路径 (YAML 格式)')
    
    # 多 GPU 调度选项
    parser.add_argument('--multi-gpu', action='store_true',
                        help='启用多 GPU 混合调度模式')
    parser.add_argument('--encoders', type=str, default=None,
                        help='启用的编码器列表，逗号分隔 (例如: nvenc,qsv)')
    parser.add_argument('--nvenc-concurrent', type=int, default=3,
                        help='NVENC 并发任务上限 (默认: 3)')
    parser.add_argument('--qsv-concurrent', type=int, default=2,
                        help='QSV 并发任务上限 (默认: 2)')
    parser.add_argument('--cpu-concurrent', type=int, default=4,
                        help='CPU 并发任务上限 (默认: 4)')
    parser.add_argument('--max-concurrent', type=int, default=6,
                        help='总并发任务上限 (默认: 6)')
    parser.add_argument('--scheduler', choices=['priority', 'least_loaded', 'round_robin'],
                        default='priority', help='调度策略 (默认: priority)')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅显示任务计划，不实际执行')
    
    return parser.parse_args()


def run_single_encoder_mode(args, config: Dict[str, Any]) -> int:
    """单编码器模式"""
    input_folder = config["paths"]["input"]
    output_folder = config["paths"]["output"]
    min_file_size = config["files"]["min_size_mb"]
    force_bitrate = config["encoding"]["bitrate"]["forced"] > 0
    forced_bitrate = config["encoding"]["bitrate"]["forced"]
    keep_structure = config["files"]["keep_structure"]
    max_workers = args.workers
    output_codec = config["encoding"]["codec"]
    enable_software_encoding = args.enable_software_fallback or args.cpu_fallback
    limit_fps_software_decode = config["fps"]["limit_on_software_decode"]
    limit_fps_software_encode = config["fps"]["limit_on_software_encode"]
    max_fps = config["fps"]["max"]
    max_bitrate_by_resolution = config["encoding"]["bitrate"].get("max_by_resolution")
    
    hw_accel = get_hw_accel_type(args.hw_accel)
    
    logging.info("=" * 60)
    logging.info("SBVC - 超级批量视频压缩器 (单编码器模式)")
    logging.info("=" * 60)
    logging.info(f"输入目录: {input_folder}")
    logging.info(f"输出目录: {output_folder}")
    logging.info(f"硬件加速: {hw_accel}")
    logging.info(f"输出编码: {output_codec}")
    logging.info("-" * 60)
    
    if not os.path.exists(input_folder):
        logging.error(f"输入目录不存在: {input_folder}")
        return 1
    
    os.makedirs(output_folder, exist_ok=True)
    video_files = get_video_files(input_folder)
    total_files = len(video_files)
    
    if total_files == 0:
        logging.warning("未发现任何视频文件")
        return 0
    
    logging.info(f"发现 {total_files} 个视频文件")
    
    # 预先检查输出是否存在，减少无谓开销
    results = []
    files_to_process = []
    processed_count = 0
    for filepath in video_files:
        output_path, _ = resolve_output_paths(
            filepath, input_folder, output_folder, keep_structure
        )
        if os.path.exists(output_path):
            logging.info(f"[跳过] 输出文件已存在(预检查): {output_path}")
            results.append((filepath, (RESULT_SKIP_EXISTS, None, {})))
            processed_count += 1
            continue
        files_to_process.append(filepath)
    
    def process_file(filepath):
        return compress_video(
            filepath=filepath,
            input_folder=input_folder,
            output_folder=output_folder,
            keep_structure=keep_structure,
            force_bitrate=force_bitrate,
            forced_bitrate=forced_bitrate,
            min_file_size_mb=min_file_size,
            hw_accel=hw_accel,
            output_codec=output_codec,
            enable_software_encoding=enable_software_encoding,
            limit_fps_software_decode=limit_fps_software_decode,
            limit_fps_software_encode=limit_fps_software_encode,
            max_fps=max_fps,
            max_bitrate_by_resolution=max_bitrate_by_resolution
        )
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(process_file, f): f for f in files_to_process}
        for future in concurrent.futures.as_completed(future_to_file):
            filepath = future_to_file[future]
            try:
                result = future.result()
                results.append((filepath, result))
            except Exception as e:
                results.append((filepath, (RESULT_ERROR, str(e), {})))
            processed_count += 1
            logging.info(f"[进度] {processed_count}/{total_files} ({processed_count/total_files*100:.1f}%)")
    
    return summarize_results(results, total_files)


def run_multi_encoder_mode(args, config: Dict[str, Any]) -> int:
    """多编码器并发调度模式
    
    高级调度策略：
    1. NVENC 和 QSV 同时并发处理不同文件
    2. 失败任务智能回退：
       - 同编码器：硬解+硬编 → 软解+硬编(限帧) → 软解+硬编
       - 跨编码器：NVENC失败 → 移交QSV，QSV失败 → 移交NVENC
       - 最终兜底：CPU 软编码
    
    例如: NVENC(3) + QSV(2) = 5个并发
    文件1-3 分配给 NVENC，文件4-5 分配给 QSV
    如果 NVENC 硬解失败 → 尝试 NVENC 软解 → 失败则移交 QSV → 最后 CPU 兜底
    """
    import shutil
    import threading
    from src.scheduler.advanced import (
        AdvancedScheduler, DecodeMode, EncoderType, create_advanced_scheduler
    )
    from src.scheduler.pool import TaskResult
    from src.core import (
        get_video_files, resolve_output_paths, get_bitrate, get_resolution, get_codec,
        execute_ffmpeg, calculate_target_bitrate
    )
    from src.config.defaults import HW_ENCODERS, SW_ENCODERS, AUDIO_BITRATE
    
    input_folder = config["paths"]["input"]
    output_folder = config["paths"]["output"]
    min_file_size = config["files"]["min_size_mb"]
    force_bitrate = config["encoding"]["bitrate"]["forced"] > 0
    forced_bitrate = config["encoding"]["bitrate"]["forced"]
    keep_structure = config["files"]["keep_structure"]
    output_codec = config["encoding"]["codec"]
    max_fps = config["fps"]["max"]
    limit_fps_software_decode = config["fps"]["limit_on_software_decode"]
    limit_fps_software_encode = config["fps"]["limit_on_software_encode"]
    max_bitrate_by_resolution = config["encoding"]["bitrate"].get("max_by_resolution")
    
    # 创建高级调度器
    scheduler = create_advanced_scheduler(config)
    
    logging.info("=" * 60)
    logging.info("SBVC - 超级批量视频压缩器 (高级调度模式)")
    logging.info("=" * 60)
    
    stats = scheduler.get_stats()
    logging.info(f"总并发上限: {stats['max_total_concurrent']}")
    logging.info(f"启用编码器: {stats['enabled_encoders']}")
    logging.info(f"CPU 兜底: {'是' if stats['cpu_fallback'] else '否'}")
    for enc_name, enc_stats in stats['encoder_slots'].items():
        logging.info(f"  - {enc_name}: 最大并发 {enc_stats['max']}")
    logging.info("回退策略: 硬解硬编 → 软解硬编 → 其他编码器 → CPU")
    logging.info("-" * 60)
    
    if args.dry_run:
        video_files = get_video_files(input_folder)
        logging.info(f"[DRY RUN] 发现 {len(video_files)} 个视频文件")
        return 0
    
    if not os.path.exists(input_folder):
        logging.error(f"输入目录不存在: {input_folder}")
        return 1
    
    os.makedirs(output_folder, exist_ok=True)
    video_files = get_video_files(input_folder)
    total_files = len(video_files)
    
    if total_files == 0:
        logging.warning("未发现任何视频文件")
        return 0
    
    logging.info(f"发现 {total_files} 个视频文件")
    
    results = []
    files_to_process = []
    completed = 0
    lock = threading.Lock()
    
    # 预检查输出是否已存在
    for filepath in video_files:
        output_path, _ = resolve_output_paths(
            filepath, input_folder, output_folder, keep_structure
        )
        if os.path.exists(output_path):
            logging.info(f"[跳过] 输出文件已存在(预检查): {output_path}")
            results.append((filepath, TaskResult(success=True, stats={"status": RESULT_SKIP_EXISTS})))
            completed += 1
            continue
        files_to_process.append(filepath)
    
    def build_encode_command(
        filepath: str,
        temp_filename: str,
        bitrate: int,
        source_codec: str,
        encoder_type: EncoderType,
        decode_mode: DecodeMode
    ) -> Dict[str, Any]:
        """根据编码器类型和解码模式构建命令"""
        supported_hw_decode_codecs = ["h264", "hevc", "av1", "vp9", "mpeg2video"]
        
        hw_accel_map = {
            EncoderType.NVENC: "nvenc",
            EncoderType.QSV: "qsv",
            EncoderType.VIDEOTOOLBOX: "videotoolbox",
            EncoderType.CPU: "none",
        }
        hw_accel = hw_accel_map.get(encoder_type, "none")
        
        hw_names = {
            "nvenc": "NVIDIA NVENC",
            "qsv": "Intel QSV",
            "videotoolbox": "Apple VideoToolbox",
            "none": "CPU",
        }
        hw_display = hw_names.get(hw_accel, hw_accel)
        
        codec_names = {"hevc": "HEVC", "avc": "H.264", "av1": "AV1"}
        codec_display = codec_names.get(output_codec, output_codec.upper())
        
        # CPU 软编码
        if encoder_type == EncoderType.CPU:
            sw_encoder = SW_ENCODERS.get(output_codec, "libx264")
            encoder_params = ['-preset', 'medium'] if sw_encoder in ("libx265", "libx264") else []
            if sw_encoder == "libsvtav1":
                encoder_params = ['-preset', '6']
            
            cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
            
            if decode_mode == DecodeMode.SW_DECODE_LIMITED and limit_fps_software_encode:
                cmd.extend(['-vf', f'fps={max_fps}'])
                name = f"{hw_display} ({sw_encoder}, 限{max_fps}fps)"
            else:
                name = f"{hw_display} ({sw_encoder})"
            
            cmd.extend(['-c:v', sw_encoder])
            cmd.extend(encoder_params)
            cmd.extend(['-b:v', str(bitrate), '-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
            
            return {"name": name, "cmd": cmd}
        
        # 硬件编码
        hw_config = HW_ENCODERS.get(hw_accel, {})
        hw_encoder = hw_config.get(output_codec)
        hwaccel = hw_config.get("hwaccel")
        hwaccel_output_format = hw_config.get("hwaccel_output_format")
        
        if not hw_encoder:
            # 回退到 CPU
            return build_encode_command(
                filepath, temp_filename, bitrate, source_codec,
                EncoderType.CPU, DecodeMode.SW_DECODE
            )
        
        # 硬解+硬编
        if decode_mode == DecodeMode.HW_DECODE and source_codec in supported_hw_decode_codecs and hwaccel:
            cmd = ['ffmpeg', '-y', '-hide_banner']
            cmd.extend(['-hwaccel', hwaccel])
            if hwaccel_output_format:
                cmd.extend(['-hwaccel_output_format', hwaccel_output_format])
            cmd.extend(['-i', filepath])
            cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
            cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
            return {"name": f"{hw_display} ({codec_display}, 硬解+硬编)", "cmd": cmd}
        
        # 软解+硬编
        cmd = ['ffmpeg', '-y', '-hide_banner', '-i', filepath]
        
        if decode_mode == DecodeMode.SW_DECODE_LIMITED and limit_fps_software_decode:
            cmd.extend(['-vf', f'fps={max_fps}'])
            name = f"{hw_display} ({codec_display}, 软解+硬编, 限{max_fps}fps)"
        else:
            name = f"{hw_display} ({codec_display}, 软解+硬编)"
        
        cmd.extend(['-c:v', hw_encoder, '-b:v', str(bitrate)])
        cmd.extend(['-c:a', 'aac', '-b:a', AUDIO_BITRATE, temp_filename])
        
        return {"name": name, "cmd": cmd}
    
    def encode_file(filepath: str, encoder_type: EncoderType, decode_mode: DecodeMode) -> TaskResult:
        """编码单个文件"""
        stats = {"original_size": 0, "new_size": 0, "original_bitrate": 0, "new_bitrate": 0}
        
        try:
            file_size = os.path.getsize(filepath)
            stats["original_size"] = file_size
            
            if file_size < min_file_size * 1024 * 1024:
                logging.info(f"[跳过] 文件小于 {min_file_size}MB: {filepath}")
                stats["status"] = RESULT_SKIP_SIZE
                return TaskResult(success=True, filepath=filepath, stats=stats)
            
            original_bitrate = get_bitrate(filepath)
            width, height = get_resolution(filepath)
            source_codec = get_codec(filepath)
            stats["original_bitrate"] = original_bitrate
            
            new_bitrate = calculate_target_bitrate(
                original_bitrate, width, height,
                force_bitrate, forced_bitrate,
                max_bitrate_by_resolution
            )
            stats["new_bitrate"] = new_bitrate
            
            new_filename, temp_filename = resolve_output_paths(
                filepath, input_folder, output_folder, keep_structure
            )
            os.makedirs(os.path.dirname(new_filename), exist_ok=True)
            
            if os.path.exists(new_filename):
                logging.info(f"[跳过] 输出文件已存在: {new_filename}")
                stats["status"] = RESULT_SKIP_EXISTS
                return TaskResult(success=True, filepath=filepath, stats=stats)
            
            # 构建编码命令
            cmd_info = build_encode_command(
                filepath, temp_filename, new_bitrate, source_codec,
                encoder_type, decode_mode
            )
            
            logging.info(f"[编码] {encoder_type.value}/{decode_mode.value}: {os.path.basename(filepath)} -> {cmd_info['name']}")
            
            # 执行编码
            success, error = execute_ffmpeg(cmd_info["cmd"])
            
            if not success:
                if os.path.exists(temp_filename):
                    try:
                        os.remove(temp_filename)
                    except:
                        pass
                return TaskResult(success=False, filepath=filepath, error=error, stats=stats)
            
            # 移动文件
            try:
                shutil.move(temp_filename, new_filename)
            except Exception as e:
                return TaskResult(success=False, filepath=filepath, error=str(e), stats=stats)
            
            new_size = os.path.getsize(new_filename)
            stats["new_size"] = new_size
            stats["status"] = RESULT_SUCCESS
            stats["method"] = cmd_info['name']
            
            compression_ratio = (1 - new_size / file_size) * 100 if file_size > 0 else 0
            logging.info(
                f"[完成] {encoder_type.value}: {os.path.basename(filepath)} | "
                f"{cmd_info['name']} | 压缩率: {compression_ratio:.1f}%"
            )
            
            return TaskResult(success=True, filepath=filepath, stats=stats)
            
        except Exception as e:
            logging.error(f"[异常] 处理 {filepath} 时发生错误: {e}")
            return TaskResult(success=False, filepath=filepath, error=str(e), stats=stats)
    
    def process_file(filepath: str):
        nonlocal completed
        
        result = scheduler.schedule_task(filepath, encode_file)
        
        with lock:
            completed += 1
            retry_info = ""
            if result.retry_history:
                retry_info = f" [尝试: {' → '.join(result.retry_history)}]"
            logging.info(f"[进度] {completed}/{total_files} ({completed/total_files*100:.1f}%){retry_info}")
        
        return (filepath, result)
    
    # 使用线程池并发处理
    max_workers = scheduler.max_total_concurrent
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_file, f) for f in files_to_process]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    logging.error(f"任务异常: {e}")
    finally:
        scheduler.shutdown()
    
    # 统计结果
    skip_exists_count = sum(1 for _, r in results if r.stats.get("status") == RESULT_SKIP_EXISTS)
    skip_size_count = sum(1 for _, r in results if r.stats.get("status") == RESULT_SKIP_SIZE)
    success_count = sum(
        1 for _, r in results
        if r.success and r.stats.get("status") not in (RESULT_SKIP_SIZE, RESULT_SKIP_EXISTS)
    )
    fail_count = len(results) - success_count - skip_exists_count - skip_size_count
    
    # 统计编码器使用情况
    encoder_usage = {}
    for _, r in results:
        if r.encoder_used:
            enc = r.encoder_used.value
            encoder_usage[enc] = encoder_usage.get(enc, 0) + 1
    
    logging.info("=" * 60)
    logging.info("任务完成统计")
    logging.info("-" * 60)
    logging.info(
        f"总文件数: {total_files}, 成功: {success_count}, 跳过(文件过小): {skip_size_count}, "
        f"跳过(已存在): {skip_exists_count}, 失败: {fail_count}"
    )
    if encoder_usage:
        logging.info(f"编码器使用统计: {encoder_usage}")
    
    # 显示调度器最终统计
    final_stats = scheduler.get_stats()
    logging.info("编码器详细统计:")
    for enc_name, enc_stats in final_stats['encoder_slots'].items():
        logging.info(f"  - {enc_name}: 完成 {enc_stats['completed']}, 失败 {enc_stats['failed']}")
    logging.info("=" * 60)
    
    return 0 if fail_count == 0 else 1


def summarize_results(results: list, total_files: int) -> int:
    """统计并输出结果摘要"""
    success_count = 0
    skip_size_count = 0
    skip_exists_count = 0
    fail_count = 0
    total_original_size = 0
    total_new_size = 0
    
    for filepath, (status, error, stats) in results:
        if status == RESULT_SUCCESS:
            success_count += 1
            total_original_size += stats.get("original_size", 0)
            total_new_size += stats.get("new_size", 0)
        elif status == RESULT_SKIP_SIZE:
            skip_size_count += 1
        elif status == RESULT_SKIP_EXISTS:
            skip_exists_count += 1
        else:
            fail_count += 1
    
    logging.info("=" * 60)
    logging.info("任务完成统计")
    logging.info("=" * 60)
    logging.info(f"总文件数: {total_files}")
    logging.info(f"成功压缩: {success_count}")
    logging.info(f"跳过(文件过小): {skip_size_count}")
    logging.info(f"跳过(已存在): {skip_exists_count}")
    logging.info(f"失败: {fail_count}")
    
    if success_count > 0 and total_original_size > 0:
        total_saved = total_original_size - total_new_size
        compression_ratio = (1 - total_new_size / total_original_size) * 100
        logging.info("-" * 60)
        logging.info(f"原始总大小: {total_original_size/1024/1024/1024:.2f} GB")
        logging.info(f"压缩后大小: {total_new_size/1024/1024/1024:.2f} GB")
        logging.info(f"节省空间: {total_saved/1024/1024/1024:.2f} GB ({compression_ratio:.1f}%)")
    
    logging.info("=" * 60)
    return 0 if fail_count == 0 else 1


def main():
    """主函数"""
    args = parse_arguments()
    
    try:
        config = load_config(args.config)
        config = apply_cli_overrides(config, args)
        
        # 设置信号处理器
        setup_signal_handlers()
        
        # 设置日志（需要先设置，才能记录清理信息）
        log_folder = config["paths"]["log"]
        setup_logging(log_folder)
        
        # 启动时清理临时文件
        output_folder = config["paths"]["output"]
        cleaned = cleanup_temp_files(output_folder)
        if cleaned > 0:
            logging.info(f"启动清理完成，共删除 {cleaned} 个临时文件")
        
        if args.multi_gpu:
            return run_multi_encoder_mode(args, config)
        else:
            return run_single_encoder_mode(args, config)
        
    except KeyboardInterrupt:
        logging.warning("用户中断操作")
        terminate_all_ffmpeg()
        return 130
    except Exception as e:
        logging.critical(f"程序执行过程中发生严重错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
