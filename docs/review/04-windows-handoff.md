# 04 · Windows 适配交接

> 面向：在本地 Windows 电脑上用 Claude Code 继续做 Windows 适配的人（以及本地的 Claude）。
> 分支：`claude/review-fixes`。**本文所有 `文件:行号` 以提交 `52198c9` 为准**（本文档提交本身不改代码）。
> **行号偏移**：[#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7)（修复 N1、N2）**已合并**，当前分支的代码行号相对本文已经后移。换算方法如下（左边是本文中的行号）：
>
> | 文件 | 本文行号 | #7 合并后 |
> | --- | --- | --- |
> | `main.py` | 1–39 | 不变 |
> | `main.py` | 40–674 | +14 |
> | `main.py` | 675–1420 | +19 |
> | `main.py` | 1421 及以后 | +20 |
> | `test.py` | 1 | 不变 |
> | `test.py` | 2–4 | +1 |
> | `test.py` | 5–1064 | +2 |
> | `test.py` | 1065 及以后 | +93 |
>
> #7 新增的代码（行号按 #7 合并后计）：`main.py:40-51` 的 `_make_console_output_safe`、`main.py:1440` 的启动调用、`main.py:689-693` 的图片视图 `NoFocus`；测试在 `test.py:1067` 和 `test.py:1106`。
> 这个分支上没有 `CLAUDE.md`：它只在 `main` 上（来自 PR #1），按你的要求没有合进本分支，所以本地 Claude Code 在这个分支上不会自动加载它。
> 云端环境：Linux、Python 3.13、`QT_QPA_PLATFORM=offscreen`、tmpfs 临时目录。项目目标是 Windows + Python 3.12。

## 0. 先看这里

建议的验证顺序：

1. **自动测试**（第 4 节）。Windows 上应该是 **54 项**全部运行、0 跳过，包括云端一直跳过的 Windows 属性测试。
2. **云端复现过、已在 [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) 修复的两个问题**（#7 已合并），需要在 Windows 上确认修复效果：
   - **N1**：方向键 → ↑ ← 原来完全无效，见 W-03。
   - **N2**：输出被重定向时，文件名含 GBK 无法编码的字符会导致删除后崩溃，见 W-10。
3. **只能在 Windows 上验证的数据安全项**：
   - **Q1**：网络盘或 U 盘上删除是否会变成永久删除。
   - **R-04**：文件被占用时的删除行为。
   - **R-05**：进度文件原子写入在 Windows 上的表现。
4. 其余项照第 5 节的手动清单逐条走，结果填进第 7 节的表格。

> ⚠️ 所有手动测试都只在**照片副本**目录中进行，永远不要把程序指向原始照片库。程序会把最后打开的目录记进 `config.json`，测试结束后检查一下这个文件，或者直接删除它。

---

## 1. 几轮修改总览

| 轮次 | 内容 | PR | 状态 |
| --- | --- | --- | --- |
| 第 1 轮 | 按 RUNBOOK 审查：高危修复 H1、审查文档、基准脚本 `bench/` | H1 等提交直接在 `claude/review-fixes` 上；[#2](https://github.com/OranngeAustin/fast-photo-sorter/pull/2)（`claude/review-fixes` → `main`） | #2 **未合并** |
| 第 2 轮 | 性能优化 P1、P2、P3 | [#3](https://github.com/OranngeAustin/fast-photo-sorter/pull/3)、[#4](https://github.com/OranngeAustin/fast-photo-sorter/pull/4)、[#5](https://github.com/OranngeAustin/fast-photo-sorter/pull/5) → `claude/review-fixes` | 已合并 |
| 第 3 轮 | 中级问题 M1、M2、M3、M4、M6 | [#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6) → `claude/review-fixes` | 已合并 |
| 第 4 轮 | 交接时发现的 N1、N2 | [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) → `claude/review-fixes` | 已合并 |

发现编号的含义见 `docs/review/02-findings.md`，性能项 P1–P7 见 `docs/review/03-perf-plan.md`。

### 按模块列出改动

| 模块 | 位置（`main.py`） | 改了什么 | 发现编号 | PR | 回归 / 特征测试（`test.py`） |
| --- | --- | --- | --- | --- | --- |
| 键盘与删除 | `action_delete` 1270-1297；`_current_image_is_shown` 1089-1094 | 当前图还显示「加载中...」（预览和文件名都没出现）时，W/↑ 不再排入删除 | H1 | #2（提交已在本分支） | `test_delete_ignored_until_current_image_is_shown` 1139 |
| 键盘与删除 | `keyPressEvent` 1224-1256（守卫在 1227） | 关闭流程开始后忽略所有按键，避免退出过程中再新建删除计时器或启动扫描 | M1 | #6 | `test_keys_are_ignored_once_shutdown_has_begun` 862 |
| 预览解码子进程 | `_decode_preview_in_subprocess` 70-140（缩放在 104-121） | 先 `thumbnail` 再按 EXIF 转正，方向 5–8 时交换宽高框；JPEG/MPO 用 `reducing_gap=1.0` 触发 draft 降采样；不再复制全尺寸原图 | P1 | #3 | `PreviewDecodeCharacterizationTests` 1236-1331 |
| 预览解码子进程 | `_exif_capture_date` 40-51；调用处 84-87 | 拍摄时间改为先读 Exif 子 IFD 的 `DateTimeOriginal`，再退回 IFD0；子 IFD 损坏时不影响预览 | M3 | #6 | `test_preview_reads_capture_date_from_exif_sub_ifd` 947 |
| 预加载调度 | `request_cache` 1171-1191 | 请求顺序改为当前项 → 后 3 张 → 前 1 张（原来前 1 张排在最前） | P3（同时解决 L6） | #5 | `PreloadWindowCharacterizationTests` 1446-1503 |
| 进度持久化 | `save_progress` 1017-1084；`_clear_progress` 967-974；`_begin_new_session` 895-907；`__init__` 799 | 记住上次成功写入的 `(路径, 内容)`，内容不变就跳过写入；清除进度、写入失败、新会话时重置这个记录 | P2（同时解决 L8） | #4 | `ProgressPersistenceCharacterizationTests` 1333-1444 |
| 进度持久化 | `_write_json_atomically` 632-652；调用处 1079 | 先写同目录、点开头的临时文件，再 `os.replace`；目标被占用（`PermissionError`）时改为把完整内容复制过去 | M6 | #6 | `test_progress_write_is_atomic` 1020 |
| 扫描结果处理 | `on_scan_finished` 933-962（空结果分支 945-955） | 扫描有警告且结果为空时**保留**进度文件，并把警告显示在界面上；只有扫描干净地成功才清除进度 | M2 | #6 | `test_incomplete_empty_scan_keeps_progress` 898 |
| 缩放视图 | `ZoomableView._IMAGEQT_MODES` 661；`set_zoom` 707-761（736-738） | ImageQt 不支持的模式（CMYK/LA/I/F 等）先转 RGBA 再放大 | M4 | #6 | `test_zoom_supports_modes_imageqt_cannot_wrap` 984 |
| 文档 / README | `README.md` | 删除说明（加载中按 W 无效）、预览解码说明、原子写入与扫描失败保留进度、测试数 52 | H1、P1、M2、M6 | #2、#3、#6 | — |
| 基准 | `bench/` | 新增 `bench_preview`、`bench_scan`、`bench_progress`、`bench_memory`、`bench_load_priority`；`bench_progress` 改为通过 `_write_json_atomically` 计数 | P1–P3 | #2、#5、#6 | — |

测试数量：基线 32 项 → #6 合并后 52 项 → #7 合并后 **54 项**（云端 53 通过、1 跳过）。

#7 的改动（已合并）：N1 给图片视图设置 `NoFocus`，方向键不再被它吞掉（回归测试 `test_arrow_keys_reach_window_even_after_clicking_image`）；N2 启动时把 stdout/stderr 的错误策略改为 `backslashreplace`，无法编码的文件名不再导致崩溃（回归测试 `test_delete_survives_console_that_cannot_encode_file_name`）。

---

## 2. 云端没能验证的内容

| 类别 | 具体内容 | 原因 |
| --- | --- | --- |
| 跳过的测试 | `test_scanner_skips_windows_hidden_and_system_entries`（`test.py:1116-1137`，`skipUnless(os.name == "nt")`，用 `ctypes.windll.kernel32.SetFileAttributesW` 设置隐藏/系统属性） | 只能在 Windows 运行 |
| 只用模拟验证过 | 真实回收站：所有测试都 mock 了 `send2trash`（如 `test.py:552`），没有任何测试真正把文件移入回收站 | 云端没有 Windows 回收站 |
| 只用模拟验证过 | `os.replace` 被拒绝后的复制回退：`test_progress_write_is_atomic` 是 mock 出 `PermissionError`，不是真实的共享冲突（WinError 32） | — |
| 只用模拟验证过 | junction 和链接：`test_scanner_handles_deep_logical_tree_without_recursion` 用的是假目录项；没有真实的 junction 或回环测试 | Linux 没有 junction |
| 只用模拟验证过 | spawn 子进程重新导入 `main.py`：测试通过 `python -m unittest` 运行，子进程**不会**把 `main.py` 当作 `__mp_main__` 重新执行；真实的 `pythonw main.py` 路径（每张预览都重新导入 PyQt6）没有测过 | 测试入口不同 |
| GUI 行为 | 所有 GUI 测试都在 offscreen 平台运行，没有覆盖：真实窗口管理器、最大化/全屏（F11）、键盘焦点、输入法、真实鼠标、高 DPI 缩放、字体回退（OSD 用 `QFont("Arial", 14)`，中文会回退到其他字体）、目录选择对话框（测试里被 patch 掉，`test.py:388`） | 无桌面环境 |
| 启动脚本 | `启动照片清理.bat` 从未运行过；`pythonw.exe` 下 `sys.stdout` 为 `None` 的行为也没测过 | 无 Windows |
| 文件系统 | NTFS 属性、长路径（MAX_PATH 260）、网络共享（SMB）、U 盘（FAT32/exFAT）、OneDrive 占位文件、Defender「受控文件夹访问」、只读介质 | 云端只有 tmpfs |
| Python 版本 | 云端是 3.13，项目目标是 3.12；`os.path.isjunction` 要 3.12+ 才有 | — |
| 依赖版本 | 云端装到的是 PyQt6 6.12.0、Pillow 13.0.0、Send2Trash 2.1.0；`requirements.txt` 没有上限，Windows 上可能装到别的版本 | 见 L14 |
| 基准 | 所有性能数据都来自 Linux；`bench/bench_memory.py` 依赖 POSIX 的 `resource` 模块，**在 Windows 上无法运行** | — |
| 用户指南 | README 的测试命令写死了 `D:\anaconda3\envs\py312-clean\python.exe`（`README.md:72`） | 作者本机路径 |

---

## 3. Windows 风险清单

> 「已复现」= 在云端用实验确认过；「推断」= 根据代码和平台行为推断，需要在 Windows 上验证。每条都对应第 5 节的手动测试编号。

### R-01 输出编码：文件名含 GBK 无法编码的字符时，删除后程序崩溃（N2，已复现，✅ 已在 [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) 修复）
- **位置**：`main.py:1307-1311`（`execute_delete` 中，`send2trash` 成功后 `print(f"已移至回收站: {path}")`；`except` 里的 `print` 会再次失败）。其他 `print` 也有同样问题：348（加载线程）、743、855、917、938（扫描警告）、974、1015、1082、1349。
- **说明**：stdout 被重定向到文件或管道，且没有开启 UTF-8 模式时，中文 Windows 用 cp936（GBK）编码，`errors="strict"`。emoji、韩文等字符会抛出 `UnicodeEncodeError`。
  - 云端模拟 GBK stdout，已确认异常会逃出 `execute_delete`，而此时**文件已经被移进回收站**。
  - 在 Qt 槽函数中出现未处理异常时，PyQt6 会调用 `qFatal()`，程序直接退出。
  - 以下情况**不会**触发：`pythonw`（stdout 为 `None`）、交互式控制台（Windows 控制台走 UTF-8）、`-X utf8`（测试命令带了这个参数，所以测试测不出来）。
  - 以下情况**会**触发：`python main.py > log.txt`、IDE 运行窗口、**通过 Claude Code 的 Bash 工具启动程序**。
- **修复**（[#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7)）：启动时调用 `_make_console_output_safe()`，把 stdout/stderr 的错误策略改为 `backslashreplace`，编码保持不变。`pythonw` 下这两个流为 `None`，不受影响。
- **验证**：W-10。这是端到端验证，因为单元测试覆盖不到 `__main__` 里的调用。

### R-02 中文路径与 JSON / 配置文件编码（推断：大概率正常）
- **位置**：进度写入 `main.py:640-641`（`encoding="utf-8"`、`ensure_ascii=False`）；进度读取 983；配置读写 849、914-915。
- **说明**：读写两侧都显式指定了 UTF-8，路径以 `str` 形式在进程间传递。风险在于：其他工具用 ANSI 打开这个 JSON 时会显示乱码，但不影响本程序。
- **验证**：W-09、W-15（用记事本打开 `.sorter_progress.json`，中文路径应可读）。

### R-03 超长路径（> 260 字符）（推断）
- **位置**：扫描 `main.py:438`（`os.scandir`）；解码 83、727（`Image.open`）；删除 1307（`send2trash`）。
- **说明**：`python.exe` 自带 longPathAware 清单，但还需要注册表 `LongPathsEnabled=1`；Shell 的 `IFileOperation` 对超长路径的支持也不确定。如果删除失败，程序应当回到该图并提示，属于安全失败。
- **验证**：W-09。查看注册表：`Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled`，分别在 0 和 1 时各测一次。

### R-04 文件被其他程序占用时删除（推断）
- **位置**：`execute_delete` `main.py:1299-1336`；删除失败时回退并提示的分支在 1316-1330。
- **说明**：其他程序打开文件时没有允许删除共享（不带 `FILE_SHARE_DELETE`），这时移入回收站会失败。程序应当回到该图、显示「无法移入回收站…」，并保留文件。
- **验证**：W-07（用 PowerShell 以只允许读的方式占住文件）。

### R-05 `os.replace` 原子写入（推断）
- **位置**：`_write_json_atomically` `main.py:632-652`；回退只捕获 `PermissionError`（642-647）。
- **说明**：
  - 同步盘客户端或杀毒软件持有目标文件、且没有允许删除共享时，`os.replace` 会失败。这时回退到 `shutil.copyfile`，前提是对方允许写共享。
  - 进度文件被设成**隐藏**（`attrib +h`）时：Windows 上用 `CREATE_ALWAYS` 打开隐藏文件会失败。`os.replace` 能否替换隐藏目标、回退复制是否也会失败，都**没有验证过**。
  - 进度文件被设成**只读**时，替换和复制都会失败，进度不再保存，界面上也不提示（L3）。
  - 程序在写临时文件和替换之间被强杀时，会残留 `.sorter_progress.json.*.tmp`。Windows 上点开头的文件**不会隐藏**，资源管理器里能看到。
- **验证**：W-15、W-16、W-17。

### R-06 删除方式：回收站还是永久删除（推断，可能是最严重的风险）
- **位置**：`main.py:1307`（`send2trash(os.path.normpath(path))`）；1304（`os.path.exists` 为 False 时直接当作删除成功，见 M5）。
- **说明**：本地 NTFS 上应进入回收站。对于**没有回收站的位置**（未开启 NAS 回收站的 SMB 共享、U 盘、某些映射盘），Send2Trash 在 Windows 上通过 `IFileOperation` 加 `FOF_NOCONFIRMATION` 调用，Shell 弹出的「无法回收，是否永久删除？」可能被自动确认，**文件被永久删除**（Q1）。这与 README「不会直接永久删除」的承诺冲突。
- **验证**：W-04（本地）、W-08（网络盘和 U 盘，只用可以丢弃的副本）。

### R-07 多进程 spawn（推断）
- **位置**：预览 `ImageLoader._decode_preview` `main.py:237-284`（spawn 在 243）；扫描 `DirectoryScanner` 510、554-629；`freeze_support()` 1421。
- **说明**：
  - 每张预览都启动一个新解释器，并把 `main.py` 当作 `__mp_main__` 重新执行，包括导入 PyQt6 和 Pillow。Windows 上每次可能要 0.3–1 秒，杀毒软件扫描时更慢。
  - 子进程设了 `daemon=True`，只在**正常退出**时被清理。主进程被强杀后，正在等待网络 I/O 的扫描子进程可能残留。
  - `pythonw` 启动的子进程同样没有 stdout。
- **验证**：W-24、W-30；`bench\bench_preview.py`（A-05）。

### R-08 `.bat` 启动脚本（推断）
- **位置**：`启动照片清理.bat:1-3`（文件名是中文，内容是纯 ASCII、**LF 换行**，仓库里没有 `.gitattributes`）。
- **说明**：
  - 第 3 行写死了 `D:\anaconda3\envs\py312-clean\pythonw.exe`（L9）。
  - 用 `pythonw` 启动时，所有错误都不可见。
  - 如果给 `.bat` 加入中文内容，要么保存为 ANSI（GBK），要么在开头加 `chcp 65001` 并保存为不带 BOM 的 UTF-8，否则 cmd 会读错。
  - 纯 LF 换行对这三行脚本没有影响（`goto`/`call :label` 才会出问题）。
- **验证**：W-02（用本地副本 `.bat` 改成 venv 路径再测，**不要提交**）。

### R-09 高 DPI 缩放（N3，推断）
- **位置**：`main.py:830-835`（`resizeEvent` 把 `self.size()`，即**逻辑像素**，作为解码目标）；1:1 放大在 739-749；OSD 字体 821。
- **说明**：
  - 缩放比例为 150% 或 200% 时，预览只按逻辑尺寸解码（物理分辨率的 1/1.5 到 1/2），显示时再被放大，**可能发糊**。P1 的 draft 尺寸也基于这个目标，比例上不会更差。
  - 「1:1 放大」实际是 1 个图像像素对应 1 个逻辑像素，也就是 1.5–2 个物理像素，并不是真正的像素级 1:1。
  - 修复方向：目标尺寸乘以 `devicePixelRatioF()`，并给 QPixmap 设置 `devicePixelRatio`。
- **验证**：W-21。

### R-10 按键事件与键盘焦点（N1 已复现，✅ 已在 [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) 修复；N4 推断）
- **位置**：`keyPressEvent` `main.py:1224-1256`；`ZoomableView` 655-777（继承 `QGraphicsView`，焦点策略为 StrongFocus）；`keyReleaseEvent` 1258-1260。
- **N1（已在 offscreen 平台复现）**：
  - 窗口显示后焦点默认落在 `ZoomableView` 上，点击图片也会让它获得焦点。
  - `QAbstractScrollArea` 会**吃掉方向键**，所以 → 和 ↑ 完全无效，← 被吃掉时撤销也无效；W/A/D 正常。
  - README 的操作表宣传了方向键。
  - **修复**（[#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7)）：给 `ZoomableView` 设置 `setFocusPolicy(Qt.FocusPolicy.NoFocus)`，所有按键都回落到主窗口；放大后的拖动只依赖鼠标。
- **N4（推断）**：中文输入法处于中文模式时，字母键会进入输入法组合，W/A/D/O 和空格可能到不了程序。
- 自动重复：1227 和 1259 忽略了自动重复事件，所以按住空格只会触发一次放大。
- **验证**：W-03、W-22。

### R-11 隐藏/系统属性、junction、符号链接（推断）
- **位置**：属性常量 `main.py:379`；属性读取 391-393；跳过规则 396-408；junction 判断 355-359（`os.path.isjunction`，需要 Python ≥ 3.12，否则会跟随 junction，只能靠 429 的 `realpath` 去重防止死循环）。
- **验证**：A-03（云端跳过的测试）；W-23（`attrib +h/+s`，以及指回根目录的 junction）。

### R-12 进度文件的位置：受控文件夹访问、只读介质、OneDrive（推断）
- **位置**：`_progress_path` `main.py:964-965`（写在照片根目录）；临时文件在 636；扫描 438-456。
- **说明**：
  - Defender 的「受控文件夹访问」默认保护「图片」「文档」，会阻止未授权的 `python.exe` 写入，进度保存和删除都可能失败，界面上只有删除失败会提示。
  - 在只读介质上，进度无法保存。
  - OneDrive「按需文件」：扫描能看到占位文件，预览时会触发下载，速度很慢；删除会进 OneDrive 回收站。
- **验证**：W-26、W-27。

### R-13 `config.json` 写在程序目录（推断）
- **位置**：`main.py:842-843`、914-917。
- **说明**：程序放在需要管理员权限的目录（如 `Program Files`）时写不进去，下次启动就不会记得上次的目录，错误只 `print`。
- **验证**：W-01（看启动后程序目录下是否生成了 `config.json`）。

### R-14 路径大小写、斜杠和盘符（推断：大概率正常）
- **位置**：目录对话框 `main.py:859` 返回的是 `/` 分隔的路径，910 的 `os.path.abspath` 会规范化；断点续看的精确匹配在 993（区分大小写），失败时退回 942-943、551 的 `normcase` 排序键。
- **说明**：U 盘换了盘符时，应该靠排序键恢复到正确位置。
- **验证**：W-15 的变体（用不同的大小写或盘符打开同一目录）。

### R-15 时间元数据（推断）
- **位置**：OSD 时间退回到 `birthTime` 在 `main.py:342-346`（Windows 上是创建时间，也就是拷贝时间）；排序用的 mtime 在 453。
- **说明**：用资源管理器复制会保留 mtime，但某些工具会重置 mtime，导致排序变化。FAT 的 mtime 精度是 2 秒，时间相同时按路径排序。
- **验证**：W-13。

### R-16 关闭与后台任务（推断）
- **位置**：`_begin_shutdown` `main.py:1374-1388`、`_finish_shutdown` 1390-1407、`closeEvent` 1409-1417、`_cancel_pending_deletions` 868-884。
- **说明**：Windows 上 `terminate()` 就是 `TerminateProcess`。需要确认的是：退出时待删除的文件不会被删，后台进程不会残留。
- **验证**：W-18、W-19、W-24。

---

## 4. 本地自动测试步骤（Windows PowerShell）

前提：已安装 python.org 的 **Python 3.12**（带 `py` 启动器）和 Git。

```powershell
py -0p
```

```powershell
git clone https://github.com/OranngeAustin/fast-photo-sorter.git
```

```powershell
cd fast-photo-sorter
```

```powershell
git switch claude/review-fixes
```

```powershell
git pull --ff-only origin claude/review-fixes
```

```powershell
git log --oneline -1
```

创建虚拟环境（放在仓库内，用 `.git\info\exclude` 在本地忽略它，不修改 `.gitignore`）：

```powershell
py -3.12 -m venv .venv
```

```powershell
Add-Content -Path .git\info\exclude -Value ".venv/"
```

如果激活时提示「禁止运行脚本」，先执行下面这条（只对当前窗口生效）：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
python -m pip install --upgrade pip
```

```powershell
python -m pip install -r requirements.txt
```

记录实际装到的版本（填进第 7 节）：

```powershell
python -c "import sys, PyQt6.QtCore as q, PIL, importlib.metadata as m; print(sys.version); print('PyQt6', q.PYQT_VERSION_STR, 'Qt', q.QT_VERSION_STR); print('Pillow', PIL.__version__); print('Send2Trash', m.version('Send2Trash'))"
```

**A-02 全部测试（离屏）**。预期：`Ran 54 tests` 加 `OK`，**没有 skipped**。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
```

```powershell
python -X utf8 -B -m unittest -v test.py
```

需要保存日志时用 `cmd` 重定向。Windows PowerShell 5.1 会把原生程序的 stderr 包装成红色错误记录，用 `cmd` 可以避开：

```powershell
cmd /c "python -X utf8 -B -m unittest -v test.py > test-offscreen.log 2>&1"
```

```powershell
Get-Content -Encoding UTF8 test-offscreen.log -Tail 5
```

**A-03 单独运行 Windows 专属测试**：

```powershell
python -X utf8 -B -m unittest -v test.FastPhotoSorterTests.test_scanner_skips_windows_hidden_and_system_entries
```

**A-04 全部测试（真实 Windows 平台插件）**。会短暂弹出窗口，能暴露焦点和 DPI 相关的差异：

```powershell
Remove-Item Env:QT_QPA_PLATFORM
```

```powershell
cmd /c "python -X utf8 -B -m unittest -v test.py > test-windows-platform.log 2>&1"
```

```powershell
Get-Content -Encoding UTF8 test-windows-platform.log -Tail 5
```

**A-05 至 A-08 基准（可选，只比较相对值）**：

```powershell
python -X utf8 bench\bench_preview.py
```

```powershell
python -X utf8 bench\bench_load_priority.py
```

```powershell
python -X utf8 bench\bench_progress.py
```

```powershell
python -X utf8 bench\bench_scan.py
```

`bench\bench_memory.py` 依赖 POSIX 的 `resource` 模块，Windows 上不能运行，不用跑。

可能遇到的情况：

- 某个测试在 `TemporaryDirectory` 清理时报 `PermissionError: [WinError 32]`，说明文件还被占用（多半是子进程或句柄还没释放）。把完整报错记进结果表，它可能就是 R-04 / R-07 的线索。
- 如果 `test_scanner_skips_windows_hidden_and_system_entries` 失败，说明隐藏/系统属性的过滤在 Windows 上没有生效（R-11）。

---

## 5. 手动测试清单

### 5.1 安全约定

- 只用**副本**。建议的测试根目录：`D:\FPS测试\照片副本`（路径本身带中文）。
- 网络盘和 U 盘测试只放可以丢弃的副本。
- 每次删除测试后，去回收站确认，必要时还原。
- 测试结束后删除程序目录下的 `config.json`，避免下次启动直接打开测试目录。

### 5.2 要准备的测试素材

| 素材 | 用途 | 获取方式 |
| --- | --- | --- |
| 真实手机照片 10–20 张（含竖拍和 EXIF） | 方向、拍摄时间、清晰度、高 DPI 对比 | 从手机或相册**复制** |
| 真实相机 JPEG 与 PNG 截图若干 | 常规浏览 | 复制 |
| 一张 > 2400 万像素的真实照片 | 大图预览和放大警告 | 复制（没有的话用生成的 05） |
| 大文件（> 50 MB 的 TIFF 或 PNG） | 加载耗时、加载中按 W（H1） | 复制，或用生成的 06 |
| HEIC 照片 1 张 | 应**不出现**（没有注册插件） | 复制 |
| 方向 6 + 拍摄时间 | M3、转正 | 生成脚本的 01 |
| CMYK JPEG / LA PNG / 16 位 PNG | M4 放大 | 生成脚本的 02–04 |
| 超大 JPEG / 超大 PNG（2700 万像素） | draft 预览与「像素过大」提示 | 生成脚本的 05、06 |
| 损坏文件 | 加载失败提示 | 生成脚本的 07 |
| emoji 和韩文文件名 | R-01（N2） | 生成脚本的 08 |
| 超长路径（> 260） | R-03 | 生成脚本的 09（需要 `LongPathsEnabled=1`） |
| PDF、PSD | 应**不出现** | 生成脚本的 10 |
| `$RECYCLE.BIN`、`@eaDir`、`#recycle`、`.thumbnails` 目录，以及 `._` 文件 | 应**不出现** | 生成脚本的 11、12 |
| 只读文件、隐藏文件、系统文件 | R-05、R-11 | 生成脚本的 13–15，再用 `attrib` 设置属性 |
| 正在被其他程序打开的图片 | R-04 | 生成脚本的 16，再用 PowerShell 占用 |
| 指回根目录的 junction | 回环防护 | `New-Item -ItemType Junction` |
| 无权读取的子目录 | M2 | `icacls` |
| 网络共享（NAS）或映射盘、U 盘上的副本 | Q1 | 手工复制 2–3 张可丢弃的图片 |
| 1000 张以上照片副本 | 性能 | 复制 |

### 5.3 生成合成素材

把下面的脚本保存为 `%TEMP%\make_assets.py`（**不要放进仓库**），在激活的 venv 中运行。脚本已经在云端跑通过，Linux 上超长路径的总长度是 328。

```python
"""生成 Windows 手动测试用的合成图片（不会碰任何真实照片）。用法：python make_assets.py <空的测试目录>"""
import sys
from pathlib import Path

from PIL import Image

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)


def plain(path, size=(1200, 800), color=(40, 120, 200)):
    Image.new("RGB", size, color).save(path)


# 01 EXIF：方向 6（应自动转正，红块在右上角）+ 拍摄时间写在 Exif 子 IFD
exif = Image.Exif()
exif[0x0112] = 6
exif.get_ifd(0x8769)[36867] = "2021:05:06 07:08:09"
image = Image.new("RGB", (1600, 1200), (30, 120, 200))
image.paste((255, 0, 0), (0, 0, 400, 300))
image.save(root / "01_旋转90度_拍摄时间2021-05-06.jpg", exif=exif.tobytes())
# 02-04 ImageQt 不能直接显示的模式（M4）
Image.new("CMYK", (1200, 800), (0, 255, 255, 0)).save(root / "02_CMYK_应显示为红色.jpg")
Image.new("LA", (1200, 800), (128, 200)).save(root / "03_灰度加透明_LA.png")
Image.new("I;16", (1200, 800), 30000).save(root / "04_16位灰度.png")
# 05-06 超过 2400 万像素：JPEG 可降采样预览，PNG 应提示「像素过大」
Image.linear_gradient("L").resize((6000, 4500)).convert("RGB").save(
    root / "05_超大JPEG_2700万像素.jpg", quality=85
)
plain(root / "06_超大PNG_2700万像素.png", (6000, 4500), (10, 10, 10))
# 07 损坏文件
(root / "07_损坏文件.jpg").write_bytes(b"not an image")
# 08 GBK 无法编码的文件名（emoji、韩文）
plain(root / "08_旅行😀_한국어.jpg", color=(0, 160, 0))
# 09 超长路径（> 260 字符）
deep = root.joinpath(*[f"第{level}层很长的目录名称" * 6 for level in range(1, 6)])  # 每层约 54 字，共 5 层
try:
    deep.mkdir(parents=True, exist_ok=True)
    plain(deep / "09_超长路径.jpg")
    print("超长路径长度:", len(str(deep / "09_超长路径.jpg")))
except OSError as exc:
    print("未能创建超长路径（Windows 需开启 LongPathsEnabled）:", exc)
# 10 非照片格式：不应出现在列表中
(root / "10_文档.pdf").write_bytes(b"%PDF-1.4\n")
(root / "10_图层.psd").write_bytes(b"8BPS")
# 11 应被跳过的目录与点文件
for folder in ("$RECYCLE.BIN", "@eaDir", "#recycle", ".thumbnails"):
    (root / folder).mkdir(exist_ok=True)
    plain(root / folder / "11_不应出现.jpg")
(root / "._12_不应出现.jpg").write_bytes(b"x")
# 13-16 由 PowerShell 设置只读、隐藏、系统属性，或占用
for name in ("13_只读.jpg", "14_隐藏_不应出现.jpg", "15_系统_不应出现.jpg", "16_被占用.jpg"):
    plain(root / name, color=(200, 200, 40))
# 17-19 普通图片，用于保留、删除、撤销和断点恢复
for index in range(17, 20):
    plain(root / f"{index}_普通图片.jpg", color=(index * 10, 80, 160))
print("完成:", root)
```

```powershell
$root = 'D:\FPS测试\照片副本'
```

```powershell
python "$env:TEMP\make_assets.py" $root
```

设置属性，并创建指回根目录的 junction：

```powershell
attrib +r "$root\13_只读.jpg"
```

```powershell
attrib +h "$root\14_隐藏_不应出现.jpg"
```

```powershell
attrib +s "$root\15_系统_不应出现.jpg"
```

```powershell
New-Item -ItemType Junction -Path "$root\20_junction_回环" -Target $root
```

模拟「其他程序正在打开这张图」：只允许别人读，不允许删除或写入。测试结束后务必执行 `$lock.Close()`。

```powershell
$lock = [System.IO.File]::Open("$root\16_被占用.jpg", 'Open', 'Read', 'Read')
```

模拟「同步盘客户端占着进度文件」：允许别人读写，但不允许删除。用完执行 `$plock.Close()`。

```powershell
$plock = [System.IO.File]::Open("$root\.sorter_progress.json", 'Open', 'Read', 'ReadWrite')
```

准备「无权读取的子目录」（W-25 使用；恢复命令见 W-25）：

```powershell
$m2 = 'D:\FPS测试\扫描失败'
```

```powershell
New-Item -ItemType Directory "$m2\子目录" -Force | Out-Null
```

```powershell
Copy-Item "$root\17_普通图片.jpg", "$root\18_普通图片.jpg" "$m2\子目录\"
```

### 5.4 测试用例

启动方式除特别说明外都是：在激活的 venv 中运行 `python -X utf8 main.py`。列表顺序按文件修改时间排列，生成的素材大致按编号排序，复制进来的真实照片通常排在前面。

| 编号 | 项目 | 操作步骤 | 预期结果 | 关联 |
| --- | --- | --- | --- | --- |
| W-01 | 首次启动与扫描 | 删除程序目录下的 `config.json`；运行程序；在对话框中选择 `$root` | 窗口最大化，显示「正在快速扫描照片，请稍候...」，随后显示第一张图和左下角 OSD。列表包含 01–08、13、16–19（以及开启长路径时的 09），**不包含** 10、11、12、14、15、HEIC，也不会因为 20 的回环而重复或卡死。程序目录下生成 `config.json` | R-11、R-13 |
| W-02 | `.bat` 启动 | 把 `.bat` 复制为 `启动照片清理-本地.bat`（**不提交**），第 3 行改成 `"%~dp0.venv\Scripts\pythonw.exe" "main.py"`；双击运行；再把整个项目放到带空格或中文的路径下重复一次 | 不出现控制台窗口，直接打开上次的目录并恢复进度 | R-08 |
| W-03 | 保留与方向键焦点 | 启动后**不点鼠标**，按 →；再按 D；然后在图片上单击一次，再按 → 和 ↑ | #7 合并后：→ 等同于 D，↑ 等同于 W，← 等同于 A；点击图片前后都有效。#7 合并前（云端复现的现状）：→ ↑ 无效，D 有效（N1）。如实记录所测的是哪个版本 | R-10 |
| W-04 | 删除进入回收站（本地 NTFS） | 定位到 `17_普通图片.jpg`，按 W，等待 3 秒以上 | 立刻进入下一张，OSD 变成橙色「已删除上一张，按 A 撤销」；3 秒后文件从磁盘消失，**出现在回收站**；从回收站还原它 | R-06 |
| W-05 | 撤销 | 在 18 上按 W，3 秒内按 A；再在 18 上按 W，等待 5 秒后按 A | 第一次回到 18，文件不会被删除。第二次停留在当前图，文件已进回收站（撤销超时），程序不崩溃 | — |
| W-06 | 加载中按 W 无效（H1） | 打开一个全是大文件的副本目录（或网络盘），按 D 后**立刻**按 W | 当前图还显示「加载中...」时按 W 没有任何效果，不会进入删除；图片显示出来后再按 W 才生效 | H1 |
| W-07 | 删除被占用的文件 | 执行 `$lock = …16_被占用.jpg…`；定位到 16，按 W，等待 3 秒；然后 `$lock.Close()`，再按 W | 3 秒后回到 16，OSD 显示「[操作失败] 无法移入回收站，已回到该图片，请稍后重试。」，文件仍在。释放占用后按 W 能正常删除 | R-04 |
| W-08 | 网络盘和 U 盘上的删除方式 | 在 NAS 共享（或映射盘）和 U 盘上各放 2 张可丢弃的副本；分别打开，按 W，等待 3 秒 | 记录文件去了哪里：本机回收站、NAS 的 `#recycle`，还是**永久消失**。如果永久消失，就证实了 Q1，需要优先修复 | R-06、Q1、M5 |
| W-09 | 中文、emoji 和超长路径 | 浏览到 08 和 09；查看 OSD 文件名；分别按 W 后再按 A；对 09 也执行一次真正的删除 | 文件名显示正确；预览正常；撤销正常。09 的删除要么成功进回收站，要么显示「无法移入回收站」并回到该图，不能崩溃 | R-02、R-03 |
| W-10 | 输出重定向时的编码崩溃（N2） | 退出程序；执行 `cmd /c "python main.py > run.log 2>&1"`（**不带** `-X utf8`）；定位到 08，按 W，等待 3 秒 | #7 合并后：程序**不崩溃**，删除正常，`run.log` 里能看到 `已移至回收站: …\U0001f600…` 这样的转义。#7 合并前：可能直接退出，`run.log` 里有 `UnicodeEncodeError`，而文件已经进了回收站。最后从回收站还原 08 | R-01 |
| W-11 | 特殊色彩模式放大（M4） | 在 02、03、04 上分别按住空格，松开 | 都能 1:1 放大，并可拖动；02 显示为**红色**；不出现「原图加载失败」 | M4 |
| W-12 | 超大图 | 浏览到 05，按住空格；浏览到 06 | 05 有预览；按住空格时显示「原图像素过大，已保留适屏预览以避免内存耗尽。」。06 显示「图片像素过大，当前格式无法安全生成预览。」，并且 OSD 中有文件名 | — |
| W-13 | 方向与拍摄时间（M3） | 浏览到 01；浏览一张真实的手机竖拍照片 | 01 已转正，红块在**右上角**，OSD 的「拍摄时间」为 `2021:05:06 07:08:09`。真实照片显示的是拍摄时间，而不是复制到电脑的时间 | M3、R-15 |
| W-14 | 损坏文件 | 浏览到 07 | 显示「图片加载失败」，OSD 中有文件名和进度；D、W 都可用 | — |
| W-15 | 断点续看与进度文件 | 浏览到 18，按 Esc 退出，再次启动；用记事本打开 `$root\.sorter_progress.json`；查看目录里有没有 `.sorter_progress.json.*.tmp` | 重启后停在 18；JSON 是 UTF-8 编码，中文路径可读，`status` 为 `in_progress`；没有残留的 `.tmp` 文件。变体：用不同的大小写，或把目录放到 U 盘、换盘符后打开，应该停在同一张或下一张 | R-02、R-05、R-14 |
| W-16 | 进度文件被设为隐藏或只读 | `attrib +h "$root\.sorter_progress.json"`，按几次 D，重启；然后 `attrib -h +r …`，按几次 D，重启；最后 `attrib -r …` | 记录隐藏和只读两种情况下进度是否还能更新，以及程序是否报错或崩溃。只读时预期进度不再更新但不崩溃 | R-05 |
| W-17 | 进度文件被占用（同步盘模拟） | 先确保进度文件存在；执行 `$plock = …`；按几次 D；`$plock.Close()`；重启 | 重启后停在最新位置（通过复制回退写入）；没有 `.tmp` 残留 | R-05 |
| W-18 | 退出时取消待删除 | 在 19 上按 W，**立即**按 Esc | 程序在 1–2 秒内退出；19 **仍然存在**，没有进回收站；重启后停在 19 | R-16、M1 |
| W-19 | 切换目录时取消待删除 | 在 19 上按 W，3 秒内按 O，选择另一个副本目录 | 19 仍然存在；新目录正常显示 | — |
| W-20 | 全屏与退出 | 按 F11 两次；按 Esc | 在全屏和最大化之间切换；Esc 能干净地退出，任务管理器里没有残留进程 | R-16 |
| W-21 | 高 DPI（N3） | 分别在显示缩放 100% 和 150%（或 200%）下打开同一张细节丰富的真实照片，与 Windows「照片」应用对比；按住空格放大 | 记录预览是否明显比「照片」应用发糊；1:1 放大时一个图像像素是否占多于一个屏幕像素；OSD 的位置和字体是否正常 | R-09 |
| W-22 | 输入法（N4） | 切换到微软拼音的**中文模式**，依次按 D、W、A、空格、O；再切换到英文模式重复 | 记录中文模式下哪些键无效或弹出了候选框；英文模式下全部有效 | R-10 |
| W-23 | 过滤规则与回环 | 检查 W-01 中显示的总数与名单 | 隐藏和系统文件（14、15）、特殊目录（11）、点文件（12）、非照片格式（10）都不出现；20 这个 junction 不会被跟随，不出现重复，也不卡死 | R-11 |
| W-24 | 后台进程清理 | 打开任务管理器的「详细信息」页；浏览时观察 `python.exe` 或 `pythonw.exe` 的数量；按 Esc 后再观察。附加：扫描 1000 张以上的目录时，在任务管理器里结束主进程 | 浏览时会短暂多出子进程；退出后全部消失。强杀主进程后，记录有没有残留的子进程 | R-07、R-16 |
| W-25 | 扫描失败时保留进度（M2） | 打开 `$m2`，按一次 D，然后退出；执行 `icacls "$m2\子目录" /deny "${env:USERNAME}:(RX)"`；重新启动（或按 O 选择 `$m2`）；恢复：`icacls "$m2\子目录" /remove:d "$env:USERNAME"`，再次打开 | 无权限时显示「未找到任何图片文件」以及「扫描未完整完成，已保留原有进度：…」和警告内容，`$m2\.sorter_progress.json` 仍然存在。恢复权限后停在第 2 张 | M2 |
| W-26 | 受控文件夹访问（若已开启） | 执行 `Get-MpPreference \| Select-Object EnableControlledFolderAccess`；如果值为 1，在「图片」文件夹的副本里测试 W 和进度保存 | 记录 Defender 是否弹出拦截通知，以及删除和进度保存是否失败 | R-12 |
| W-27 | OneDrive 按需文件（可选） | 在 OneDrive 目录里放几张「仅在线」的副本，打开该目录 | 记录预览是否触发下载、是否很慢，以及删除后文件去了哪里 | R-12 |
| W-28 | 空目录 | 打开一个空的副本目录 | 显示「未找到任何图片文件」；如果目录里有旧的进度文件，会被清除 | M2 |
| W-29 | 「已完成」后新增照片（M7，已知问题） | 在只有 2 张图的目录里全部按 D，退出；再复制 1 张新照片进去，重启 | 当前代码预期显示「全部照片整理完毕」，新照片**不出现**（已知问题）。记录实际行为 | M7 |
| W-30 | 大目录性能 | 打开 1000 张以上照片的副本目录；记录扫描时间；连续按 D 20 次，感受预览等待时间；运行 A-05 到 A-08 | 记录扫描秒数、每张预览的大致等待时间，以及基准中位数 | R-07、P1–P4 |

---

## 6. 已知问题（标为跳过或未处理的发现）

已修复的项目（仅供对照）：H1、M1、M2、M3、M4、M6、L6（由 P3 #5 解决）、L8（由 P2 #4 解决）、P1；N1、N2 已在 [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) 修复（已合并，待 Windows 确认）。

| 编号 | 问题 | 级别 | 跳过原因 | Windows 上要关注什么 |
| --- | --- | --- | --- | --- |
| M5 | 文件「不存在」被当作删除成功（`main.py:1304`），网络盘短暂断开时，文件会被标记为已删但实际还在，之后再也不会出现 | 中 | 需要先确认 Windows + SMB 断线时 `os.stat` 抛出的错误类型（Q4） | W-08 时可以拔网线观察 |
| M7 | 进度标记为「已完成」后，新加入的照片不再出现 | 中 | 需要确认期望行为；修复会改变进度文件语义（Q2） | W-29 |
| M8 | 原图放大在 GUI 线程同步解码，大图会卡顿 | 中 | 属于 UI 重构，风险中等（P7） | W-12 时感受卡顿 |
| L1 | 扫描取消时 `_cancel_deadline` 可能为 `None`（竞态） | 低 | 结果碰巧无害 | — |
| L2 | 每按一次 W 泄漏一个 `QTimer` | 低 | 只记录 | 长时间使用后的内存 |
| L3 | 诊断信息只靠 `print`，`pythonw` 下全部丢失；扫描警告大多不显示 | 低 | 只记录 | 与 N2 一起考虑改用日志 |
| L4 | 当前项及之前全部已删时，会写入 `completed` | 低 | 极少触发 | — |
| L5 | `send2trash` 在 GUI 线程同步执行 | 低 | 改动中等 | 网络盘删除时界面卡顿 |
| L7 / P4 | 每张预览都 spawn 一个新进程 | 低（性能） | 收益大但风险中等 | Windows 上开销更大（W-30） |
| L9 | `.bat` 写死了解释器路径 | 低 | 属于启动配置，按规则不改 | W-02 |
| L10 / P5 | 扫描时每个目录都调用 `realpath` | 低（性能） | 排名靠后 | NAS 扫描慢 |
| L11 | 原图放大在主进程中解码不可信文件 | 低（安全） | 与 M8 一起处理 | — |
| L12 | 子进程结果经 Pipe 以 pickle 传回 | 低（安全） | 同一用户权限，没有提权边界 | — |
| L13 | 进度文件记录绝对路径（含 Windows 用户名） | 低（隐私） | 改格式需要确认（Q5） | 共享 NAS 目录 |
| L14 | `requirements.txt` 没有版本上限 | 低 | 规则：不改依赖声明 | 记录 Windows 上装到的版本 |
| L15 | 将来加日志时不要写进照片目录 | 低 | 提醒，现状无泄露 | — |
| P6 | `load_progress` 改用 bisect | 性能 | 收益可忽略 | — |
| P7 | 放大改到后台线程 | 性能 | UI 重构 | — |
| Q1 | 网络盘和 U 盘上删除可能是**永久删除** | 待确认（可能是高） | 只能在 Windows 验证 | **W-08，最优先** |
| Q3 | 加载中按 W 被忽略（H1 的交互取舍） | 待确认 | 交互偏好 | W-06 |
| **N1** | 窗口焦点落在图片视图上时，方向键被吞掉，README 宣传的 → ↑ ← 无效 | 中（新） | ✅ 已在 [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) 修复（已合并） | W-03 确认 |
| **N2** | stdout 被重定向且非 UTF-8 时，文件名含 GBK 无法编码的字符会导致删除后崩溃 | 中（新） | ✅ 已在 [#7](https://github.com/OranngeAustin/fast-photo-sorter/pull/7) 修复（已合并） | W-10 确认 |
| **N3** | 高 DPI 下预览可能发糊，1:1 放大并非物理像素 1:1 | 低到中（新） | 同上；属于推断 | W-21 |
| **N4** | 中文输入法的中文模式下，字母快捷键可能无效 | 低（新） | 同上；属于推断 | W-22 |
| 其他 | `CLAUDE.md` 只在 `main` 上，里面的测试数还是 32；PR #2 未合并；`bench_memory.py` 只能在 POSIX 上运行；`.gitignore` 里没有 `.venv/` | — | 按你的要求没有合并 main | 本地用 `.git\info\exclude` 忽略 `.venv/` |

---

## 7. 结果记录模板

### 7.1 环境

| 项目 | 值 |
| --- | --- |
| Windows 版本（`winver`） | |
| Python 版本（`python -V`） | |
| PyQt6 / Qt 版本 | |
| Pillow 版本 | |
| Send2Trash 版本 | |
| 显示器分辨率与缩放比例 | |
| 输入法 | |
| `LongPathsEnabled` | |
| 受控文件夹访问（`EnableControlledFolderAccess`） | |
| 测试目录所在的文件系统（NTFS / exFAT / SMB / OneDrive） | |
| 代码提交（`git log --oneline -1`） | |

### 7.2 结果

结果填写：✅ 通过 · ❌ 失败 · ⚠️ 部分通过 · — 不适用。失败时把完整报错或日志片段贴进「报错信息」列。

| 编号 | 项目 | 结果 | 报错信息 / 备注 |
| --- | --- | --- | --- |
| A-01 | 安装依赖、记录版本 | | |
| A-02 | 全部测试（offscreen）：54 项、0 跳过 | | |
| A-03 | `test_scanner_skips_windows_hidden_and_system_entries` | | |
| A-04 | 全部测试（Windows 平台插件） | | |
| A-05 | `bench_preview`（解码与往返中位数） | | |
| A-06 | `bench_load_priority` | | |
| A-07 | `bench_progress` | | |
| A-08 | `bench_scan` | | |
| W-01 | 首次启动与扫描 | | |
| W-02 | `.bat` 启动 | | |
| W-03 | 保留与方向键焦点（N1） | | |
| W-04 | 删除进入回收站 | | |
| W-05 | 撤销 | | |
| W-06 | 加载中按 W 无效（H1） | | |
| W-07 | 删除被占用的文件 | | |
| W-08 | 网络盘和 U 盘上的删除方式（Q1） | | |
| W-09 | 中文、emoji 和超长路径 | | |
| W-10 | 输出重定向时的编码崩溃（N2） | | |
| W-11 | 特殊色彩模式放大（M4） | | |
| W-12 | 超大图 | | |
| W-13 | 方向与拍摄时间（M3） | | |
| W-14 | 损坏文件 | | |
| W-15 | 断点续看与进度文件 | | |
| W-16 | 进度文件隐藏或只读 | | |
| W-17 | 进度文件被占用 | | |
| W-18 | 退出时取消待删除 | | |
| W-19 | 切换目录时取消待删除 | | |
| W-20 | 全屏与退出 | | |
| W-21 | 高 DPI（N3） | | |
| W-22 | 输入法（N4） | | |
| W-23 | 过滤规则与回环 | | |
| W-24 | 后台进程清理 | | |
| W-25 | 扫描失败时保留进度（M2） | | |
| W-26 | 受控文件夹访问 | | |
| W-27 | OneDrive 按需文件 | | |
| W-28 | 空目录 | | |
| W-29 | 「已完成」后新增照片（M7） | | |
| W-30 | 大目录性能 | | |
