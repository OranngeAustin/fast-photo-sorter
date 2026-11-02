# 01 · 架构摸底

> 审查基线：`claude/review-fixes` @ d116f15（与 `main` 相同）。仓库是**扁平结构**，没有子目录形式的模块，所以下面的「审查顺序」精确到 `main.py` 中的组件与行号区间。

## 1. 仓库组成

| 路径 | 行数 | 作用 |
| --- | --- | --- |
| `main.py` | 1343 | 全部应用代码：后台预览解码、目录扫描、缩放视图、主窗口（状态机 + 删除/撤销 + 进度持久化） |
| `test.py` | 973 | `unittest` 离屏测试（32 项；1 项仅限 Windows） |
| `requirements.txt` | 6 | 运行依赖：PyQt6 ≥ 6.11、Pillow ≥ 12.2、Send2Trash ≥ 1.8.3（无锁定版本） |
| `启动照片清理.bat` | 3 | Windows 启动脚本，硬编码 `D:\anaconda3\envs\py312-clean\pythonw.exe` |
| `README.md` | — | 用户文档（中文） |
| `.gitignore` | — | 忽略 `config.json`（含个人路径）、缓存、日志 |
| `docs/review/` | — | 本次审查产出 |

没有 CI 配置、打包脚本、`CLAUDE.md`、数据库或网络服务。目标平台是 **Windows 桌面**（回收站、隐藏/系统属性、junction）；Linux 上可以跑测试，但 Windows 专属分支只能静态审查。

## 2. 模块划分（`main.py` 内部）

| 组件 | 行号 | 运行位置 | 职责 |
| --- | --- | --- | --- |
| `_bounded_preview_size` / `_decode_preview_in_subprocess` | 38–110 | **spawn 子进程** | 打开图片、读 EXIF 时间、超 24 MP 时 JPEG/MPO 走 `draft` 降采样、`exif_transpose` + `thumbnail(LANCZOS)`，限制回传像素 ≤ 6 MP，通过 Pipe 回传 RGBA 字节 |
| `ImageLoader(QThread)` | 113–322 | 加载线程 | 会话隔离的请求队列（滑动窗口：当前项前 1 张、后 3 张）；**每张预览单独 spawn 一个子进程**；请求过期或停止时终止子进程；用 `image_loaded` 信号回传 `QImage` |
| 扫描辅助函数 `_is_linked_directory` / `PHOTO_FORMATS` / `_is_skipped_*` / `_scan_directory_records` | 325–437 | 扫描子进程 | 显式栈遍历目录树；跳过链接、junction、回收站、NAS 快照/缩略图目录、隐藏/系统项、点文件；按扩展名白名单收集 `(mtime, path)`，分批发送 |
| `_scan_directory_in_subprocess` / `DirectoryScanner(QThread)` | 440–599 | 扫描监督线程 + 子进程 | 监督扫描子进程，支持协作式取消和超时终止，结束后按 `(mtime, normcase(path))` 排序并发出 `scan_finished` |
| `ZoomableView(QGraphicsView)` | 602–718 | GUI 线程 | 适屏显示预览；按住空格或左键时**在 GUI 线程同步**解码原图（≤ 24 MP），1:1 显示并保持锚点 |
| `FastPhotoSorter(QMainWindow)` | 721–1330 | GUI 线程 | 会话管理、配置/进度读写、视图刷新、OSD、键盘处理、延迟删除（`QTimer` + `send2trash`）、撤销、协作式关闭 |
| `__main__` | 1333–1343 | — | `freeze_support`、全局黑色调色板、启动窗口 |

## 3. 入口

1. `启动照片清理.bat` → `pythonw.exe main.py`（无控制台，`print` 输出全部丢失）。
2. `python main.py` → `QApplication` → `FastPhotoSorter()` → 100 ms 后 `startup_check()`。
3. 子进程入口（spawn）：`_decode_preview_in_subprocess`、`_scan_directory_in_subprocess`。在 spawn 模式下，子进程会**以 `__mp_main__` 的身份重新执行 `main.py` 顶层代码**，包括导入 PyQt6 和 Pillow。
4. 测试入口：`python -m unittest test.py`（这种方式下子进程不会重新导入 `main.py` 作为主模块）。

## 4. 核心数据流

```
startup_check ──config.json(last_directory)──► load_directory(dir)
   │                                             │ _begin_new_session: 取消待删除/扫描，session_id += 1，清空状态
   │                                             │ 写 config.json
   ▼                                             ▼
select_directory (QFileDialog)            DirectoryScanner ──spawn──► _scan_directory_records
                                                 │ ◄── ("batch", [(mtime, path)…]) / ("finished", warnings)
                                                 ▼ 排序
                                          on_scan_finished(session_id, records, warning)
                                                 │ image_paths / path_mtimes / path_sort_keys
                                                 │ 空结果 → _clear_progress()（删除进度文件）
                                                 ▼
                                          load_progress(.sorter_progress.json) → current_index
                                                 ▼
update_view ─► request_cache ─► ImageLoader.update_window(session, [(idx, path)…]) ─► save_progress()
   ▲                                     │ 队列 → 每个请求 spawn ─► _decode_preview_in_subprocess
   │                                     ▼
   └──────── on_image_loaded(session, idx, path, QImage, metadata) → cache[idx]

按键：D/→ action_keep → idx+1 → save_progress → update_view
      W/↑ action_delete → QTimer(3 s) → execute_delete → send2trash(path)
      A/← action_undo → 停止计时器，回到该索引
      空格/左键 → ZoomableView.set_zoom → Image.open + exif_transpose + ImageQt（GUI 线程）
      Esc/关闭 → _begin_shutdown → 取消待删除、停止加载器和扫描器 → 轮询直至线程结束 → close
```

持久化状态：

| 文件 | 位置 | 内容 | 写入时机 |
| --- | --- | --- | --- |
| `config.json` | `main.py` 所在目录 | `{"last_directory": …}` | 每次 `load_directory` |
| `.sorter_progress.json` | 所选照片根目录 | `status` / `last_path` / `last_mtime` / `last_sort_key` | 每次导航（实际**每次按键写两次**：动作本身一次，`request_cache` 再一次）、取消删除、删除完成 |

## 5. 外部依赖

| 依赖 | 用途 | 风险点 |
| --- | --- | --- |
| PyQt6 | GUI、线程、信号 | 跨线程信号必须是排队连接；`QTimer(self)` 父对象持有的计时器生命周期 |
| Pillow | 解码、EXIF、`draft`、`ImageQt` | 处理不可信的图片文件（解码器漏洞面）；`ImageQt` 不支持部分图像模式；EXIF 子 IFD 读取方式 |
| Send2Trash | 移入回收站 | Windows 上依赖 Shell `IFileOperation`；对**不支持回收站的卷（如网络共享）**的行为需要确认 |
| multiprocessing (spawn) | 预览/扫描隔离 | 每个请求都启动新解释器，并重新导入 `main.py`（含 PyQt6）的开销；Pipe 用 pickle 传输 |
| 文件系统 / NAS | 扫描、进度文件 | 慢速或断开的共享目录；目录名大小写；非原子写入 |

## 6. 高风险区域

1. **删除路径**（`action_delete` / `execute_delete` / `action_undo` / `_cancel_pending_deletions`，1185–1270、809–825）：唯一的破坏性操作。风险包括：用户还没看到图片就被删除、删除发生在退出请求之后、会话切换时的竞态，以及回收站在网络共享上可能失效。
2. **进度持久化**（`load_progress` / `save_progress` / `_clear_progress` / `on_scan_finished`，873–1009）：会影响用户是否漏看或重看照片。风险包括：扫描失败时删除进度文件、非原子写入、「已完成」状态吞掉新增照片。
3. **后台加载与会话隔离**（`ImageLoader`，113–322）：跨线程、跨进程，旧会话结果不能串到新会话；也是主要的性能热点（每张预览一次 spawn）。
4. **扫描器**（325–599）：遍历不可信目录树，过滤规则决定哪些文件会出现在删除键下；取消和超时路径存在竞态。
5. **缩放视图**（602–718）：在 GUI 线程同步解码原图，会阻塞界面并带来内存峰值；部分图像模式无法放大。
6. **关闭流程**（1272–1330）：协作式停止与轮询，关闭期间仍会响应按键。

## 7. 审查顺序（按风险从高到低）

| 顺序 | 范围 | 理由 |
| --- | --- | --- |
| 1 | `main.py:1141-1270`：键盘分发 + 删除/撤销 | 破坏性操作，直接决定会不会删错文件 |
| 2 | `main.py:809-1009`：会话切换、扫描结果处理、进度读写 | 状态一致性和进度丢失 |
| 3 | `main.py:1272-1330`：关闭流程 | 退出时是否会遗留删除操作或线程 |
| 4 | `main.py:113-322`、`38-110`：加载线程与解码子进程 | 并发、会话隔离、资源回收、性能 |
| 5 | `main.py:325-599`：扫描器 | 过滤规则、取消竞态、性能 |
| 6 | `main.py:602-718`：缩放视图 | GUI 阻塞、内存、格式兼容 |
| 7 | `main.py:1011-1139`：视图与 OSD | 展示正确性 |
| 8 | `启动照片清理.bat`、`requirements.txt`、`README.md` | 配置与文档一致性 |
| 9 | `test.py` | 覆盖缺口（删除确认、关闭期间按键、EXIF） |
