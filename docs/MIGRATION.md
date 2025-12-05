# 从旧SBVC.py迁移到新版SuperBatchVideoCompressor指南

本文档帮助你从旧的单文件`SBVC.py`程序迁移到新的模块化`SuperBatchVideoCompressor`。

## 📊 新旧功能对比

| 功能 | 旧程序 | 新程序 | 改进 |
|-----|--------|--------|------|
| 硬件加速 | 仅NVENC | NVENC/QSV/VideoToolbox | ✅ 支持更多平台 |
| 编码格式 | 仅HEVC | HEVC/AVC/AV1 | ✅ 更多选择 |
| 回退机制 | 简单3级回退 | 智能多级回退 | ✅ 更可靠 |
| 并发控制 | 固定3线程 | 可配置+智能调度 | ✅ 更灵活 |
| 配置方式 | 硬编码 | 配置文件+命令行 | ✅ 更易用 |
| 多GPU支持 | ❌ | ✅ | ✅ 新功能 |
| 跨平台 | Windows only | Windows/Linux/macOS | ✅ 跨平台 |

## 🚀 快速迁移步骤

### 1. 安装依赖

```bash
# 安装Python依赖
pip install -r requirements.txt

# 确保FFmpeg已安装
ffmpeg -version
```

### 2. 配置路径

编辑`config.yaml`文件，将旧程序的路径配置到新程序：

```yaml
# 旧程序配置
# input_folder_path = r"F:\lada\output"
# output_folder_path = r"F:\lada\pre"
# log_folder_path = r'I:\BVC'

# 对应的新程序配置
paths:
  input: "F:/lada/output"
  output: "F:/lada/pre"
  log: "I:/BVC"
```

**注意**：Windows路径可以使用 `/` 或 `\\`，避免使用单个 `\`

### 3. 运行新程序

#### 方式一：使用配置文件（推荐）

```bash
# 单编码器模式（与旧程序最接近）
python main.py

# 或者指定配置文件
python main.py --config config.yaml
```

#### 方式二：命令行参数

```bash
# 完全通过命令行参数运行
python main.py -i "F:/lada/output" -o "F:/lada/pre" -l "I:/BVC" --hw-accel nvenc -c hevc -w 3
```

#### 方式三：多GPU混合调度模式（新功能）

```bash
# 使用多编码器混合调度（推荐用于多GPU或混合硬件环境）
python main.py --multi-gpu
```

## ⚙️ 配置说明

### 完全兼容旧程序的最小配置

如果你想要新程序的行为与旧程序完全一致，使用以下配置：

```yaml
paths:
  input: "F:/lada/output"
  output: "F:/lada/pre"
  log: "I:/BVC"

encoding:
  codec: "hevc"
  bitrate:
    forced: 0          # 自动计算码率（旧程序也是自动）

encoders:
  enabled: ["nvenc"]   # 只使用NVENC
  cpu_fallback: false  # 不启用CPU回退（旧程序没有）

  nvenc:
    max_concurrent: 3  # 与旧程序相同

files:
  min_size_mb: 100     # 与旧程序相同
  keep_structure: true # 与旧程序相同
```

### 利用新功能的推荐配置

如果你想利用新程序的高级功能：

```yaml
encoders:
  enabled: ["nvenc", "qsv"]  # 启用多个编码器
  cpu_fallback: true          # 启用CPU兜底

  nvenc:
    max_concurrent: 3
    fallback_to: "qsv"        # NVENC失败时回退到QSV

scheduler:
  strategy: "least_loaded"    # 使用负载均衡调度
  max_total_concurrent: 6     # 提高总并发数
```

## 🔄 功能对应关系

### 旧程序的功能在新程序中的实现

| 旧程序 | 新程序 | 说明 |
|--------|--------|------|
| `force_bitrate_flag` | `encoding.bitrate.forced` | 强制码率开关 |
| `forced_bitrate` | 命令行: `--force-bitrate` | 强制码率值 |
| `keep_structure_flag` | `files.keep_structure` | 保持目录结构 |
| `min_file_size` | `files.min_size_mb` | 最小文件大小 |
| `max_workers=3` | `-w 3` 或 `nvenc.max_concurrent: 3` | 并发数 |

### 旧程序的回退机制

**旧程序**：
1. 完全GPU模式（`-hwaccel cuda -hwaccel_output_format cuda`）
2. 混合模式（`-hwaccel cuda`）
3. 标准模式（无硬件加速参数）

**新程序**（更完善）：
1. 硬件全加速（硬解+硬编）
2. 混合模式+限帧（软解+硬编+30fps）
3. 混合模式（软解+硬编）
4. CPU编码+限帧
5. CPU编码
6. libx264回退（终极兼容）

## 📝 命令行示例

### 完全模拟旧程序行为

```bash
python main.py \
  -i "F:/lada/output" \
  -o "F:/lada/pre" \
  -l "I:/BVC" \
  --hw-accel nvenc \
  -c hevc \
  -w 3 \
  --min-size 100
```

### 启用软件回退（推荐）

```bash
python main.py \
  -i "F:/lada/output" \
  -o "F:/lada/pre" \
  --hw-accel nvenc \
  --enable-software-fallback
```

### 多GPU混合调度（最强大）

```bash
python main.py --multi-gpu \
  --encoders nvenc,qsv \
  --scheduler least_loaded \
  --max-concurrent 6
```

## ⚠️ 注意事项

### 1. 路径格式

**旧程序**：必须使用原始字符串 `r"F:\lada\output"`

**新程序**：支持多种格式
- `"F:/lada/output"` ✅ 推荐
- `"F:\\lada\\output"` ✅ 可用
- `r"F:\lada\output"` ✅ 可用（Python字符串）

### 2. 默认行为差异

| 行为 | 旧程序 | 新程序 | 建议 |
|-----|--------|--------|------|
| 失败回退 | 只有3次尝试 | 最多6次尝试 | 保持新行为 |
| 跳过已存在文件 | ✅ | ✅ | 一致 |
| 限制帧率 | ❌ | ✅ 默认30fps | 可通过`--no-fps-limit`关闭 |
| CPU兜底 | ❌ | ✅ 可选 | 建议启用 |

### 3. 性能优化

**旧程序**：固定3线程

**新程序优化建议**：
- 单GPU：保持3线程 `nvenc.max_concurrent: 3`
- 双GPU：提高到6线程 `nvenc.max_concurrent: 6`
- GPU+CPU混合：使用多编码器模式 `--multi-gpu`

## 🐛 常见问题

### Q1: 新程序输出文件名不同？
A: 两个程序输出都是`.mp4`格式，保持目录结构也一致。如果不同，检查`files.keep_structure`配置。

### Q2: 性能比旧程序慢？
A: 检查以下配置：
- 确保`nvenc.max_concurrent`设置为3（与旧程序一致）
- 检查是否误开了帧率限制：`--no-fps-limit`
- 确认硬件加速正常：查看日志中的"使用xxx编码器"

### Q3: 能否完全禁用新功能，只用NVENC？
A: 可以，使用以下配置：
```yaml
encoders:
  enabled: ["nvenc"]
  cpu_fallback: false
fps:
  limit_on_software_decode: false
  limit_on_software_encode: false
```

### Q4: 如何查看详细的编码过程？
A: 查看日志文件 `logs/transcoding_YYYYMMDDHHMMSS.log`，包含每个文件的编码方法、回退记录。

## 📈 迁移检查清单

- [ ] 安装Python依赖 `pip install -r requirements.txt`
- [ ] 确认FFmpeg可用 `ffmpeg -version`
- [ ] 修改`config.yaml`中的路径配置
- [ ] 运行小批量测试（5-10个文件）
- [ ] 对比输出质量和文件大小
- [ ] 检查日志确认编码器使用正确
- [ ] 性能测试（处理速度对比）
- [ ] 全量迁移

## 💡 推荐升级路径

### 阶段1：保守迁移（1-2天）
使用最小配置，完全模拟旧程序行为，确保稳定性。

### 阶段2：启用回退（1周）
启用`cpu_fallback: true`，提高成功率。

### 阶段3：性能优化（2-4周）
根据硬件环境启用多编码器混合调度，提升吞吐量。

## 📞 支持

如果迁移过程中遇到问题，请提供：
1. 旧程序的配置参数
2. 新程序的`config.yaml`内容
3. 日志文件片段
4. 错误信息截图

---

**祝迁移顺利！新程序将为你带来更强大、更可靠的视频压缩体验。** 🎉
