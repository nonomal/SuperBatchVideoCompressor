# Super Batch Video Compressor (SBVC)

SBVC 是一个基于 FFmpeg 的批量视频压缩命令行工具，支持 NVENC / Intel QSV / VideoToolbox 等硬件加速，并内置**智能多编码器调度**与进程管理，适合大批量转码场景。

## 主要特性

- **自动检测硬件**：启动时自动检测可用编码器，不可用的自动禁用
- **多编码器真正并发**：NVENC 和 QSV 同时处理不同文件（如 3+2=5 并发）
- **智能回退调度**：
  - 同编码器内：硬解+硬编 → 软解+硬编(限帧) → 软解+硬编
  - 跨编码器：NVENC 失败 → QSV，QSV 失败 → NVENC
  - 最终兜底：CPU 软编码（可开关）
  - 所有方法失败的任务跳过，继续处理队列
- **进程管理**：Ctrl+C 自动终止所有 FFmpeg 进程，启动时清理临时文件
- 自动按分辨率计算目标码率
- 可保持输入目录结构输出

## 硬件编码器

| 编码器 | 平台 | 硬件 | 说明 |
|--------|------|------|------|
| nvenc | Windows/Linux | NVIDIA GPU | 最快，需要 N 卡 + 驱动 |
| videotoolbox | macOS | Apple 芯片 | 仅 macOS 可用 |
| qsv | Windows/Linux | Intel 集显 | 需要 Intel CPU 集成显卡 |
| cpu | 全平台 | CPU | 软编码兜底，最慢但兼容性最好 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 复制并修改配置文件
cp config-example.yaml config.yaml
# 编辑 config.yaml 设置输入/输出路径

# 运行
python main.py

# 预览任务（不实际执行）
python main.py --dry-run
```

## 配置文件

编码器 `enabled: true` 表示"想用"，启动时自动检测是否真正可用：

```yaml
encoders:
  nvenc:
    enabled: true          # 想用 NVENC，启动时自动检测
    max_concurrent: 3      # 并发数
  qsv:
    enabled: true          # 想用 QSV，启动时自动检测
    max_concurrent: 2
  cpu:
    enabled: true          # CPU 兜底

scheduler:
  max_total_concurrent: 5  # 总并发 = 3 + 2
```

启动时日志示例：
```
检测编码器可用性...
✓ NVENC 可用
✓ QSV 可用
✓ CPU 可用
```

## 调度策略

```
┌─────────────────────────────────────────────────────────────┐
│                        任务入口                              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  启用的硬件编码器并发处理（负载均衡分配）                    │
│  ┌─────────────┐  ┌─────────────┐                          │
│  │ NVENC (3槽) │  │ QSV (2槽)   │                          │
│  │ 文件1,2,3   │  │ 文件4,5     │                          │
│  └─────────────┘  └─────────────┘                          │
└─────────────────────────────────────────────────────────────┘
          ↓ 失败
┌─────────────────────────────────────────────────────────────┐
│  同编码器降级重试                                           │
│  硬解+硬编 → 软解+硬编(限帧) → 软解+硬编                    │
└─────────────────────────────────────────────────────────────┘
          ↓ 仍然失败
┌─────────────────────────────────────────────────────────────┐
│  移交其他硬件编码器                                         │
│  NVENC失败 → QSV, QSV失败 → NVENC                          │
└─────────────────────────────────────────────────────────────┘
          ↓ 所有硬件都失败
┌─────────────────────────────────────────────────────────────┐
│  CPU 软编码兜底（如果启用）                                  │
│  如果也失败 → 跳过此任务，继续队列                          │
└─────────────────────────────────────────────────────────────┘
```

## 目录结构保持

默认情况下，程序会保持输入目录的结构：

```
输入目录:                  输出目录:
L:/input/                 J:/Output3/
├── video1.mkv           ├── video1.mp4
├── Season 01/           ├── Season 01/
│   ├── ep01.mkv        │   ├── ep01.mp4
│   └── ep02.mkv        │   └── ep02.mp4
└── Movies/              └── Movies/
    └── 2024/                └── 2024/
        └── film.mkv            └── film.mp4
```

**配置方式：**

1. **配置文件**（推荐）：
   ```yaml
   files:
     keep_structure: true   # 保持目录结构
     # keep_structure: false  # 扁平化输出（所有文件输出到同一目录）
   ```

2. **命令行参数**：
   ```bash
   # 不保持目录结构（所有文件输出到同一目录）
   python main.py --no-keep-structure
   ```

**提示：** 运行时会显示路径映射示例，可以确认输出结构是否符合预期。

## 命令行参数

```
python main.py [选项]

选项:
  -i, --input PATH          输入文件夹路径
  -o, --output PATH         输出文件夹路径
  -c, --codec CODEC         输出编码 (hevc/avc/av1)
  --config PATH             配置文件路径
  --max-concurrent N        总并发数
  --no-keep-structure       不保持原始目录结构（扁平化输出）
  --dry-run                 预览模式，不实际执行
```

## 环境要求

- Python 3.8+
- FFmpeg（需在 PATH 中）
- pyyaml（`pip install pyyaml`）
- 硬件驱动：NVIDIA / Intel

## 测试

### 运行所有测试

```bash
pytest
```

### 运行特定测试

```bash
# 测试目录保持功能
pytest tests/test_keep_structure.py -v

# 测试编码器功能
pytest tests/test_encoder.py -v

# 生成覆盖率报告
pytest --cov=src --cov-report=html
```

### 持续集成

项目使用 GitHub Actions 进行自动化测试，包括：

- ✅ 多平台测试（Ubuntu、Windows、macOS）
- ✅ 多 Python 版本测试（3.8-3.14）
- ✅ 代码质量检查
- ✅ 目录保持功能专项测试
- ✅ CLI 功能测试

每次提交都会自动运行所有测试，确保功能稳定可靠。
