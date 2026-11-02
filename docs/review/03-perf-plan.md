# 03 · 性能分析与优化计划

> 环境：Linux 云容器，Python 3.13.18，Pillow 13.0.0，PyQt6 6.12.0，tmpfs 临时目录。**云端计时有波动，只比较相对提升**；Windows 上 spawn 和 NAS I/O 的绝对开销会更大。
> 基准脚本在 `bench/`（不属于测试套件），每项运行 5 次取中位数：
> `QT_QPA_PLATFORM=offscreen python -X utf8 bench/<脚本>.py`

## 1. 基线测量

| 基准 | 场景 | 中位数 | 说明 |
| --- | --- | --- | --- |
| `bench_preview` · decode_in_process | 4032×3024 JPEG（7.5 MB）→ 1920×1080 预览，只算子进程内的工作 | **366.9 ms** | cProfile：完整 JPEG 解码约 157 ms，`exif_transpose` 中的 `copy()` 约 32 ms，`thumbnail(LANCZOS)` 从 4032 宽缩放约 178 ms，转 RGBA 加 `tobytes` 约 5 ms |
| `bench_preview` · loader_round_trip | 同上，经 `ImageLoader._decode_preview`（spawn + 重新导入 main + 解码 + Pipe 回传） | **610.5 ms** | spawn 加导入开销约 **245 ms/张**（Linux；Windows 通常更高） |
| `bench_preview` · window_of_5 | 新打开或恢复时预加载窗口的 5 张，串行 | **2580 ms** | |
| `bench_memory` | 单次预览解码的峰值 RSS 增量 | **35.7 MB** | 完整解码 12 MP 图像（每像素 3 字节）后再复制一份 |
| `bench_scan` · `_scan_directory_records` | 781 个目录、6248 张照片加 6248 个非图片文件，根目录深 7 层 | **59.1 ms** | cProfile：`realpath` 约 20%（每个目录逐层 lstat），`splitext` 约 19%，`DirEntry.stat` 约 14% |
| `bench_scan` · 监督端排序 | 6248 条按 `(mtime, normcase)` 排序 | 1.5 ms | 可忽略 |
| `bench_progress` · 1000 次按 D | 含进度写入 | **812 ms**（约 0.81 ms/次） | 实测 **10 次按键写了 20 次**进度文件 |
| `bench_progress` · load_progress（路径存在） | n = 100k | 2.0 ms | `in` 加 `.index` 两次 O(n) |
| `bench_progress` · load_progress（路径已删除） | n = 100k | 13.5 ms | 按排序键线性查找 O(n) |

## 2. 热点清单

| # | 热点 | 位置 | 当前复杂度 / 成本 | 耗时 / 内存 | 优化方案 | 预计收益 | 风险 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | 预览解码：≤ 24 MP 的 JPEG 也按全分辨率解码，再额外复制一份 | `main.py:62-91` | O(源像素)：12 MP 图要做完整 IDCT、一次 36 MB 复制，再 LANCZOS 处理 1200 万像素 | 367 ms，约 36 MB | 所有 JPEG/MPO 在解码前用 `draft("RGB", 目标尺寸)` 做 DCT 域降采样（libjpeg 只会降到不小于目标尺寸的 1/2、1/4、1/8）；方向不需要旋转时不再复制。复杂度降为 O(源像素 / s²)，s ∈ {1, 2, 4, 8} | 1080p 屏幕上的 12 MP 照片**解码约快 40%**，内存约少 3/4 | **低**：输出尺寸不变，像素有细微差异（DCT 缩放代替部分 LANCZOS），先补特征测试锁定尺寸、方向、元数据 |
| P2 | 进度文件每次按键写两次 | `main.py:1108、1182、1209`（`save_progress`） | 每次导航 2 次同步写入（GUI 线程） | 0.81 ms/次（tmpfs）；NAS 上每次写入通常要几到几十毫秒 | 记住上一次成功写入的路径和内容，内容相同时跳过；`_clear_progress` 或写入失败时清空记录 | 写入次数 **减半**，按键耗时约 -50% | **低**：只省掉内容完全相同的写入 |
| P3 | 预加载顺序：先解码前一张（index-1），再解码当前项 | `main.py:1089-1107`（`request_cache`） | 恢复进度、撤销、删除失败回退时，当前图的等待时间 = 2 次往返 | 2 × 610 ms | 请求按优先级排序：当前项、后 1–3 张，最后才是前一张 | 这些场景下**当前图首次出现的时间约 -50%**（少等一次 spawn + 解码） | **低**：只改排队顺序，窗口内容不变 |
| P4 | 每张预览都 spawn 一个新解释器，并重新导入 `main.py`（PyQt6 + Pillow） | `main.py:207-254` | 每张固定约 245 ms，Windows 上更高 | 610 ms 中有 245 ms | 常驻可复用的解码子进程：请求过期或停止时才终止并重建 | 往返时间约 -40%（Linux），Windows 上更多 | **中**：子进程生命周期、关闭、超时、测试替身的协议都要改 |
| P5 | 扫描器对每个目录调用 `realpath` | `main.py:398-405` | 每个目录 O(深度) 次 lstat（POSIX），或打开一次句柄（Windows） | 约占扫描 CPU 的 20%；NAS 上每次都是网络往返 | 只对根目录计算 realpath，子目录直接用 `normcase(拼接路径)` 作为键（不跟随链接，路径天然唯一） | 本地约 -20%，NAS 上更多 | 低到中：理论上不再防御硬链接目录造成的环路（macOS HFS+） |
| P6 | `load_progress` 的线性查找 | `main.py:925-945` | O(n) | n = 100k 时 2–13.5 ms，每次会话一次 | 对有序的 `path_sort_keys` 用 `bisect` | 绝对值很小 | 低 |
| P7 | 原图放大：GUI 线程同步完整解码，经过多次中间拷贝 | `main.py:671-683` | O(源像素)，多次复制 | 24 MP 约 0.5–1.5 s 卡顿，峰值约 300 MB | 后台解码、缓存、减少拷贝 | 体验收益大 | 中：涉及 UI 状态机 |

## 3. 排序与入选

按「收益高、风险低」排序（收益 1–5，风险 1–5，得分 = 收益 − 风险；同分时风险低的优先）：

| 排名 | 热点 | 收益 | 风险 | 得分 | 入选 |
| --- | --- | --- | --- | --- | --- |
| 1 | P1 JPEG draft 解码 | 4 | 1 | 3 | ✅ → `claude/perf-jpeg-draft` |
| 2 | P2 进度写入去重 | 3 | 1 | 2 | ✅ → `claude/perf-progress-writes` |
| 3 | P3 预加载优先级 | 3 | 1 | 2 | ✅ → `claude/perf-load-priority` |
| 4 | P4 常驻解码子进程 | 5 | 3 | 2 | ⏭ 收益最大但风险中等，无人值守时按保守原则不做，列为人工下一步建议 |
| 5 | P5 扫描 realpath | 3 | 2 | 1 | ⏭ |
| 6 | P6 bisect 恢复 | 1 | 1 | 0 | ⏭ 收益可忽略 |
| 7 | P7 放大后台化 | 3 | 3 | 0 | ⏭ 属于 UI 重构 |

阶段 6 的验收标准（RUNBOOK）：基准中位数提升 ≥ 20% 且全部测试通过；最多尝试 3 种方案；提升不到 5% 则放弃并回退。
