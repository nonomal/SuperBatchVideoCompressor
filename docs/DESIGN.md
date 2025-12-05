# SBVC 设计说明

## 目录与职责
```
.
├── main.py                # 入口脚本，调用 CLI 主函数
├── cli.py                 # 命令行解析、单/多编码器运行流程
├── config-example.yaml    # 示例配置，便于拷贝为 config.yaml
├── requirements.txt       # 仅包含 pyyaml 依赖
├── tests/                 # Pytest 用例，覆盖编码器映射与码率计算
├── src/
│   ├── __init__.py        # 包元数据与核心 API 出口
│   ├── config/            # 配置默认值与加载/覆盖逻辑
│   │   ├── defaults.py    # 默认路径、编码、帧率、并发、编码器映射等常量
│   │   ├── loader.py      # 查找 config.yaml / ~/.sbvc/config.yaml，深度合并并应用 CLI 覆盖
│   │   └── __init__.py
│   ├── core/              # 压缩核心：视频信息、码率计算、FFmpeg 命令与压缩流程
│   │   ├── video.py       # ffprobe 获取码率/分辨率/编码/时长/fps
│   │   ├── encoder.py     # 目标码率计算、编码命令构建（含分层回退）、FFmpeg 执行
│   │   ├── compressor.py  # 单文件压缩逻辑、输出路径与跳过规则
│   │   └── __init__.py
│   ├── scheduler/         # 多编码器调度（保留用于并发控制）
│   │   ├── pool.py        # EncoderPool 并发控制、EncoderType/TaskResult 数据结构
│   │   ├── hybrid.py      # HybridScheduler/BatchScheduler
│   │   └── __init__.py
│   └── utils/             # 工具函数
│       ├── files.py       # 视频文件枚举、硬件加速自动检测
│       ├── logging.py     # 日志输出到文件与控制台
│       └── process.py     # 进程管理：FFmpeg进程跟踪、信号处理、临时文件清理
└── LICENSE                # MIT 许可证
```

## 运行流程总览
1. `main.py` 仅调用 `cli.main()`，确保可被 `python main.py ...` 或作为模块运行。
2. `cli.py` 解析命令行参数并加载配置：  
   - 调用 `src.config.loader.load_config` 读取默认配置+配置文件。  
   - 用 `apply_cli_overrides` 将命令行覆盖到配置字典。
   - 设置信号处理器（捕获 Ctrl+C）。
   - 启动时清理输出目录中的临时文件。
3. 根据 `--multi-gpu` 选择模式：  
   - **单编码器模式**：线程池并行调用 `compress_video`，使用单一硬件编码器的内部回退。
   - **分层回退模式**：使用 `build_layered_fallback_commands` 构建分层命令列表，按"解码方式优先，编码器次之"的策略逐一尝试。
4. 压缩流程：视频探测 → 目标码率计算 → 构建编码命令 → 依次尝试 → 成功后落盘 `.mp4`。
5. Ctrl+C 时通过 `terminate_all_ffmpeg()` 优雅终止所有 FFmpeg 子进程。

## 配置设计
- 默认配置在 `src/config/defaults.py`：包含路径、码率比例/下限、帧率限制、并发上限、硬/软编码器映射、支持的扩展名、返回值常量等。
- `src/config/loader.py`：  
  - `find_default_config()` 优先找项目根目录 `config.yaml`，其次 `~/.sbvc/config.yaml`。  
  - `load_config()` 先复制默认配置，再加载 YAML（若安装了 PyYAML），使用 `deep_merge` 深度合并。  
  - `apply_cli_overrides()` 按命令行参数覆盖路径、编码、码率、帧率、文件策略、并发、调度策略与 CPU 兜底开关。

## CLI 与入口 (`cli.py`)
- `parse_arguments()` 定义全部命令行选项，含路径、硬件加速、输出编码、码率/帧率限制、文件筛选、软件/CPU 兜底、多编码器并发、dry-run 等。
- `run_single_encoder_mode()`：  
  - 读取路径/阈值/并发等配置。
  - 自动硬件类型解析 `get_hw_accel_type`。  
  - 用 `ThreadPoolExecutor` 并发调用 `compress_video`；收集结果后用 `summarize_results()` 汇总成功/跳过/失败与节省空间。
- `run_multi_encoder_mode()`：  
  - 使用分层回退策略，不依赖调度器选择编码器。
  - 通过 `build_layered_fallback_commands` 生成按优先级排序的命令列表。
  - 线程池并行提交，每个任务按分层顺序尝试命令直到成功。
  - 统计编码器使用情况。
- `main()` 设置信号处理、清理临时文件、包裹流程并处理 Ctrl+C/异常。

## 核心压缩 (`src/core`)
- `video.py`：用 `ffprobe` 读取码率、分辨率、编码、时长、帧率（支持分数帧率解析）；失败时提供保底值。
- `encoder.py`：  
  - `calculate_target_bitrate()` 按原码率与分辨率计算目标码率，受比例、最小/最大值和强制码率控制。  
  - `build_encoding_commands()` 生成单编码器模式的 FFmpeg 命令列表：硬解+硬编、软解+硬编（可限帧）、纯软件编码（可限帧，含 x264 兼容回退）。
  - `build_layered_fallback_commands()` 生成分层回退命令列表：
    - 第1层：所有硬件编码器的硬解+硬编
    - 第2层：所有硬件编码器的软解+硬编（限帧）
    - 第3层：所有硬件编码器的软解+硬编（不限帧）
    - 第4层：CPU 软解+软编
  - `execute_ffmpeg()` 运行命令并捕获已知错误模式，注册/注销进程以支持优雅退出。
- `compressor.py`：  
  - `get_video_files()` 遍历输入目录匹配扩展名。  
  - `compress_video()` 负责单文件处理：文件大小过滤、元数据探测、目标码率计算、输出路径/临时文件生成、已存在文件跳过。依次执行编码命令，成功则重命名临时文件；失败记录最后错误。

## 调度模块 (`src/scheduler`)
> 注：在分层回退模式下，调度器的编码器选择逻辑不再使用，但保留用于单编码器模式和未来扩展。

- `pool.py`：  
  - `EncoderType` 枚举（nvenc/qsv/videotoolbox/cpu）。  
  - `EncoderConfig` 数据类描述并发上限、设备、回退指向、preset。  
  - `EncoderPool` 用信号量控制单编码器并发，跟踪当前/累计成功失败。  
  - `TaskResult` 传递任务成功与错误、使用的编码器、统计与回退链。
- `hybrid.py`：  
  - `HybridScheduler` 管理多个 `EncoderPool`，策略支持 `priority` / `least_loaded` / `round_robin`。
  - `create_scheduler_from_config()` 读取配置构造 `EncoderConfig` 列表。

## 工具模块 (`src/utils`)
- `files.py`：`get_video_files()`（与核心同名，供 CLI 早期使用）与硬件加速自动检测 `detect_hw_accel()`；`get_hw_accel_type()` 处理 `auto` 与日志输出。
- `logging.py`：`setup_logging()` 创建时间戳日志文件并绑定到根 logger，同步输出到控制台。
- `process.py`：进程管理模块
  - `register_process()` / `unregister_process()`：跟踪所有 FFmpeg 子进程
  - `terminate_all_ffmpeg()`：优雅终止所有进程（先 SIGTERM，超时后 SIGKILL）
  - `cleanup_temp_files()`：清理输出目录中的临时文件（`tmp_*.mp4`）
  - `setup_signal_handlers()`：设置 SIGINT/SIGTERM 信号处理器

## 其他文件
- `README.md`：项目简介与使用说明。
- `scheduler.py`：空文件，保留占位。
- `tests/test_encoder.py`：覆盖目标码率计算、编码命令构建以及编码器映射存在性。
- `LICENSE`：MIT 许可。

## 输入/输出与约束
- 输入：`--input` 指定的目录内所有受支持的视频扩展名；可通过 `--min-size` 过滤小文件。  
- 输出：统一写入 `.mp4`，默认保持目录结构，目标文件存在则跳过；临时文件以 `tmp_` 前缀生成后重命名。  
- 日志：写入 `--log` 目录并同时打印。  
- 外部依赖：要求可执行的 `ffmpeg`/`ffprobe`；可选 `pyyaml` 以读取配置文件；硬件编码依赖对应驱动。
