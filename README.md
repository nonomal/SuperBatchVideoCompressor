# Super Batch Video Compressor (SBVC)

SBVC 是一个基于 FFmpeg 的批量视频压缩命令行工具，支持 NVENC / Intel QSV 等硬件加速，并内置**高级多编码器调度**与进程管理，适合大批量转码场景。

## 主要特性
- **多编码器并发**：NVENC 和 QSV 同时处理不同文件，充分利用 N 卡和集成显卡
- **智能回退调度**：
  - 同编码器内：硬解+硬编 → 软解+硬编(限帧) → 软解+硬编
  - 跨编码器：NVENC失败 → 移交QSV → QSV失败 → 移交NVENC
  - 最终兜底：CPU 软编码
- **进程管理**：Ctrl+C 时自动终止所有 FFmpeg 子进程，启动时自动清理临时文件
- 自动按分辨率计算目标码率（默认原码率的 50%，最小 500 kbps）
- 可保持输入目录结构输出，所有输出统一转为 `.mp4`
- 日志同时写入文件与控制台

## 硬件编码器说明

| 编码器 | 平台 | 硬件 | 说明 |
|--------|------|------|------|
| NVENC | Windows/Linux | NVIDIA GPU | N 卡硬件编码，速度最快 |
| QSV | Windows/Linux | Intel CPU 集显 | Intel Quick Sync Video |
| VideoToolbox | macOS | Apple 芯片/集显 | Apple 专用，Windows 不可用 |
| CPU | 全平台 | CPU | 软件编码，兼容性最好但最慢 |

> **注意**：VideoToolbox 仅适用于 macOS，在 Windows 系统上应使用 NVENC 和/或 QSV。

## 环境要求
- Python 3.8+（建议虚拟环境）
- 已安装并可通过 `ffmpeg` / `ffprobe` 调用的 FFmpeg
- 如需读取 YAML 配置需安装 `pyyaml`（在 `requirements.txt` 中）
- 对应的硬件驱动：NVIDIA (NVENC)、Intel (QSV)、Apple (VideoToolbox)

## 安装
```bash
pip install -r requirements.txt
```

## 快速开始
- 单编码器模式（默认）：  
  ```bash
  python main.py -i ./input -o ./output --hw-accel auto --codec hevc
  ```

- 多编码器并发模式（推荐）：  
  ```bash
  cp config-example.yaml config.yaml   # 按需修改编码器配置
  python main.py --multi-gpu --config ./config.yaml
  ```

## 高级调度策略

使用 `--multi-gpu` 模式时，采用**多编码器并发 + 智能回退**策略：

```
                     ┌─────────────────────────────────────┐
                     │           新任务入口                 │
                     └─────────────────────────────────────┘
                                      ↓
          ┌───────────────────────────┴───────────────────────────┐
          ↓                                                       ↓
┌─────────────────────┐                               ┌─────────────────────┐
│   NVENC (3个槽位)   │                               │   QSV (2个槽位)     │
│   处理文件 1,2,3    │                               │   处理文件 4,5      │
└─────────────────────┘                               └─────────────────────┘
          ↓ 失败                                                  ↓ 失败
┌─────────────────────┐                               ┌─────────────────────┐
│ 软解+硬编(限帧)     │                               │ 软解+硬编(限帧)     │
│ 软解+硬编           │                               │ 软解+硬编           │
└─────────────────────┘                               └─────────────────────┘
          ↓ 仍然失败                                              ↓ 仍然失败
          └───────────────────────┬───────────────────────────────┘
                                  ↓
                     ┌─────────────────────────────────────┐
                     │      移交其他编码器重试              │
                     │   NVENC失败 → QSV                   │
                     │   QSV失败 → NVENC                   │
                     └─────────────────────────────────────┘
                                  ↓ 所有硬件都失败
                     ┌─────────────────────────────────────┐
                     │      CPU 软编码兜底                  │
                     └─────────────────────────────────────┘
```

### 配置示例
```yaml
# config.yaml
encoders:
  enabled: [nvenc, qsv]    # Windows: N卡 + Intel集显
  cpu_fallback: true       # 启用 CPU 兜底
  nvenc:
    max_concurrent: 3      # NVENC 同时处理 3 个文件
  qsv:
    max_concurrent: 2      # QSV 同时处理 2 个文件
scheduler:
  max_total_concurrent: 5  # 总并发 = 3 + 2
```

### 日志输出示例
```
[编码] nvenc/hw_decode: file1.mp4 -> NVIDIA NVENC (HEVC, 硬解+硬编)
[编码] nvenc/hw_decode: file2.mp4 -> NVIDIA NVENC (HEVC, 硬解+硬编)
[编码] qsv/hw_decode: file4.mp4 -> Intel QSV (HEVC, 硬解+硬编)
[完成] nvenc: file1.mp4 | NVIDIA NVENC (HEVC, 硬解+硬编) | 压缩率: 65%
[编码] nvenc/sw_decode: file3.mp4 -> NVIDIA NVENC (HEVC, 软解+硬编)  # 硬解失败，降级
[进度] 4/10 (40%) [尝试: nvenc:hw_decode → qsv:hw_decode]  # 跨编码器回退
```

## 进程管理
- **Ctrl+C 优雅退出**：按下 Ctrl+C 时，程序会自动终止所有正在运行的 FFmpeg 子进程，然后退出
- **启动时清理**：程序启动时会自动清理输出目录中残留的临时文件（`tmp_*.mp4`）

## 主要命令行参数
- 路径：`-i/--input` 输入目录，`-o/--output` 输出目录，`-l/--log` 日志目录（默认值见 `src/config/defaults.py`）
- 编码：`--hw-accel` auto/nvenc/videotoolbox/qsv/none，`-c/--codec` hevc|avc|av1（默认 hevc）
- 码率：`--force-bitrate <bps>` 强制码率；否则自动按比例计算  
  文件过滤：`--min-size <MB>` 最小处理文件大小（默认 100MB），`--no-keep-structure` 取消保持目录结构
- 并发：`-w/--workers` 单编码器模式线程数（默认 3）；`--enable-software-fallback`/`--cpu-fallback` 启用 CPU 兜底
- 帧率限制：`--max-fps`（默认 30），`--no-fps-limit` / `--no-fps-limit-decode` / `--no-fps-limit-encode`
- 分层回退模式：`--multi-gpu` 启用；`--max-concurrent` 总并发上限，`--dry-run` 仅生成计划

## 配置文件
- 默认从 `config.yaml`（项目根目录）或 `~/.sbvc/config.yaml` 读取，命令行参数优先级最高。
- 示例见 `config-example.yaml`，包含：
  - `paths`：输入/输出/日志目录
  - `encoding`：输出编码、音频码率、码率计算方式
  - `fps`：软件解码/编码时是否限帧
  - `encoders`：启用的编码器、并发上限、回退链（默认 NVENC → QSV → CPU）
  - `scheduler`：调度策略与总并发上限
  - `files`：最小文件大小、是否保持目录结构、是否跳过已存在输出

## 输出与日志
- 输出文件扩展名统一为 `.mp4`，默认保持输入目录结构；若目标文件已存在会跳过处理。
- 日志写入 `log` 目录（文件名包含时间戳），同时输出到控制台。

## 测试
```bash
pytest
```
当前包含编码器映射与码率计算的基础单元测试。
