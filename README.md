# Super Batch Video Compressor (SBVC)

SBVC 是一个基于 FFmpeg 的批量视频压缩命令行工具，支持 NVENC / Apple VideoToolbox / Intel QSV 等硬件加速，并内置多编码器混合调度与 CPU 兜底回退，适合大批量转码场景。

## 主要特性
- 自动按分辨率计算目标码率（默认原码率的 50%，最小 500 kbps，带强制码率选项）
- 支持硬件解码+编码、软件解码+硬件编码、纯软件编码多级回退，自动跳过过小文件和已存在的输出
- 可保持输入目录结构输出到指定文件夹，所有输出统一转为 `.mp4`
- 多编码器混合调度：NVENC / QSV / VideoToolbox / CPU 并发上限可配，支持优先级、最少负载、轮询三种策略
- 自动检测硬件加速（`--hw-accel auto`），可通过配置文件或命令行覆盖
- 日志同时写入文件与控制台，默认保存在指定日志目录

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
  将自动检测硬件，按默认比例压缩，大于 100MB 的视频会输出到 `./output`，并保持目录结构。

- 多 GPU/多编码器混合调度：  
  ```bash
  cp config-example.yaml config.yaml   # 按需修改编码器并发/回退链
  python main.py --multi-gpu --config ./config.yaml
  ```
  使用配置中的并发上限与调度策略（默认优先级），可加 `--dry-run` 仅查看任务计划。

## 主要命令行参数
- 路径：`-i/--input` 输入目录，`-o/--output` 输出目录，`-l/--log` 日志目录（默认值见 `src/config/defaults.py`）
- 编码：`--hw-accel` auto/nvenc/videotoolbox/qsv/none，`-c/--codec` hevc|avc|av1（默认 hevc）
- 码率：`--force-bitrate <bps>` 强制码率；否则自动按比例计算  
  文件过滤：`--min-size <MB>` 最小处理文件大小（默认 100MB），`--no-keep-structure` 取消保持目录结构
- 并发：`-w/--workers` 单编码器模式线程数（默认 3）；`--enable-software-fallback`/`--cpu-fallback` 启用 CPU 兜底
- 帧率限制：`--max-fps`（默认 30），`--no-fps-limit` / `--no-fps-limit-decode` / `--no-fps-limit-encode`
- 混合调度：`--multi-gpu` 启用；`--encoders nvenc,qsv` 指定启用列表；  
  `--nvenc-concurrent` / `--qsv-concurrent` / `--cpu-concurrent` 单编码器并发，`--max-concurrent` 总并发上限，`--scheduler` 选择 priority|least_loaded|round_robin，`--dry-run` 仅生成计划

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
