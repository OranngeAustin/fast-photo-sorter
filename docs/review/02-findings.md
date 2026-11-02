# 02 · 审查发现

> 范围：`claude/review-fixes` @ d116f15 的全部代码。阶段 2 使用 `/code-review`（high，对象为 `main.py`），并按 `01-architecture.md` 的顺序人工复核；阶段 3 的安全审计结果在文末「安全」一节，编号接续。
> 「把握程度」：**高** = 读代码确定，或已用实验复现；**中** = 推理充分但依赖时序或环境；**低** = 需要 Windows 或真实 NAS 环境确认。
> 行号以 d116f15 的 `main.py` 为准。
> **修复状态**：H1 → [#2](https://github.com/OranngeAustin/fast-photo-sorter/pull/2)；M1、M2、M3、M4、M6 → [#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6)（#6 已合并到 `claude/review-fixes`；#2 → `main` 尚未合并）。其余项未处理。

## 高

| 编号 | 位置 | 问题 | 原因 | 修复建议 | 把握 |
| --- | --- | --- | --- | --- | --- |
| H1 | `main.py:1185`（`action_delete`），`main.py:1045-1049`（`update_view` 加载分支） | 当前图片仍显示「加载中...」、OSD 被隐藏时，按 W 也会把它排入删除。用户可能删掉一张**从未看到过、也看不到文件名**的照片。 | `action_delete` 只检查 `_has_current_image()`（索引是否有效），不检查当前项是否已经显示（缓存中有预览，或有错误提示加文件名）。每张预览都要单独 spawn 子进程，Windows 上要几百毫秒，快速连按两次 W 时第二次删除的就是还没出现的图。撤销只能撤回最近一次，且加载期间 OSD 隐藏，不容易察觉。这违背了 README「删除前可以确认是哪一个文件」，也违背了 `test_failed_preview_keeps_file_name_visible` 体现的设计意图。 | `action_delete` 增加前置条件：当前索引在 `cache` 中有路径匹配的条目（即已显示过预览或「加载失败 + 文件名」）才允许删除；否则忽略这次按键。只限制破坏性的 W，D（保留）不受影响。补一个回归测试。 | 高 |

## 中

| 编号 | 位置 | 问题 | 原因 | 修复建议 | 把握 |
| --- | --- | --- | --- | --- | --- |
| M1（✅ 已修复：[#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6)） | `main.py:1141`（`keyPressEvent`） | 退出流程开始后（`_closing=True`，界面显示「正在停止后台任务」）仍响应 W/D/O 等按键。 | `keyPressEvent` 没有检查 `_closing`。如果关闭需要等待（例如扫描慢速 NAS，最长要等 5 秒以上），此时按 W 会在 `_cancel_pending_deletions` 之后新建一个 3 秒删除计时器，文件会在用户请求退出之后被删除，违背 README「退出时安全取消未到期的删除」。按 O 还会在关闭期间启动新扫描、改写 `config.json`。 | `keyPressEvent` 开头加上：`_closing` 为真时直接返回。同时在 `action_delete` 中加同样的保护（双保险）。 | 高 |
| M2（✅ 已修复：[#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6)） | `main.py:885-887`（`on_scan_finished`） | 扫描失败或只扫到部分目录、结果为空时，会**删除进度文件**。 | 只要结果列表为空就调用 `_clear_progress()`，没有区分「确实没有照片」和「扫描出错」（子进程崩溃、子目录无权限、SMB 短暂断开）。照片都放在子目录里时，一次瞬时错误就会让进度归零。 | 只在 `warning` 为空（扫描完整成功）时清除进度；有警告时保留进度文件，并在界面上显示警告。 | 高 |
| M3（✅ 已修复：[#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6)） | `main.py:63-65`（`_decode_preview_in_subprocess`） | 「拍摄时间」几乎总是读不到 EXIF，而是退回显示文件创建时间（`birthTime`）。 | `DateTimeOriginal`（36867）存放在 Exif 子 IFD（0x8769）中，而代码只查 `getexif()` 的 IFD0。已用 Pillow 13 实验确认：`36867 in getexif()` 为 False，`getexif().get_ifd(0x8769)[36867]` 能读到。拷贝过的文件创建时间等于拷贝时间，标签具有误导性。 | 先查 `exif.get_ifd(ExifTags.IFD.Exif)`，再退回 IFD0 和 `DateTime`（306）。 | 高 |
| M4（✅ 已修复：[#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6)） | `main.py:680-681`（`ZoomableView.set_zoom`） | CMYK JPEG、带 alpha 的灰度 PNG（LA）、32 位整型/浮点 TIFF（I/F）**永远无法放大**，只会提示「原图加载失败」。 | `ImageQt` 不支持这些模式（已实验确认会抛 `ValueError: unsupported image mode`）。预览路径先 `convert("RGBA")` 所以正常，放大路径没有转换。 | 放大前，模式不在 ImageQt 支持列表中时先 `convert("RGBA")`（或统一转 RGBA）。 | 高 |
| M5 | `main.py:1217`（`execute_delete`） | 文件「不存在」被当作删除成功。网络共享短暂不可达时，`os.path.exists` 返回 False，索引被记为已删除，进度越过它，但文件实际还在。下次启动按排序键恢复时会跳过它，这张照片既没删掉也不会再出现。 | `os.path.exists` 遇到任何 `OSError` 都返回 False，无法区分「已被外部删除」和「暂时访问不到」。 | 区分两种情况：用 `os.stat`/`os.lstat` 捕获 `FileNotFoundError` 才算「已不存在」；其他 `OSError` 按删除失败处理（回到该图并提示）。 | 中 |
| M6（✅ 已修复：[#6](https://github.com/OranngeAustin/fast-photo-sorter/pull/6)） | `main.py:1005-1009`（`save_progress`） | 进度文件以截断方式原地写入，每次按键都会写。 | `open(..., "w")` + `json.dump` 不是原子操作。崩溃、断电或磁盘满时会留下空或截断的 JSON，`load_progress` 解析失败后从第 0 张开始，「已完成」状态也会丢失。 | 先写同目录临时文件（以点开头，扫描器会自动跳过），再 `os.replace` 原子替换。 | 高 |
| M7 | `main.py:918-920`（`load_progress`）、`main.py:967-973`（`save_progress`） | 一个文件夹标记为「已完成」后，再往里加的新照片**永远不会显示**。 | `completed` 状态不保存最后的排序键，加载时直接跳到 `len(image_paths)`。新照片 mtime 更晚，本应排在末尾等待筛选。 | 「已完成」时也记录最后一项的 `last_sort_key`，加载时定位到它之后的第一项。这会改变进度文件语义，见「待人工确认」Q2。 | 高（行为）/ 需确认（是否属于设计取舍） |
| M8 | `main.py:671-683`（`set_zoom`） | 按住空格或左键放大时，在 **GUI 线程**同步解码原图：Image.open → exif_transpose（完整解码 + 拷贝）→ ImageQt（转 RGBA）→ `QImage.copy` → `QPixmap`。24 MP 图片会卡住界面，峰值内存约 300 MB 以上，而且每次按下都重新解码。 | 放大路径没有做后台处理，也没有缓存。 | 短期：缓存当前路径的原图 QPixmap，或至少复用 RGBA 转换，减少中间拷贝。长期：放到后台线程，先显示「加载原图中」。 | 高 |

## 低

| 编号 | 位置 | 问题 | 原因 | 修复建议 | 把握 |
| --- | --- | --- | --- | --- | --- |
| L1 | `main.py:576-578`（`DirectoryScanner.run`） | `time.monotonic() >= self._cancel_deadline` 可能在 `_cancel_deadline is None` 时执行，抛出 `TypeError`。 | 只调用了 `requestInterruption()`（例如关闭期间新开的扫描器）或 `cancel()` 的两步之间发生线程切换时，循环底部的检查早于设置截止时间。异常会被外层 `except` 吞掉，结果碰巧无害。 | 在底部分支里，若 `_cancel_deadline is None` 先调用 `_request_child_cancellation()`。 | 中 |
| L2 | `main.py:1192`（`action_delete`） | 每次按 W 都会创建一个 `QTimer(self)`，触发或取消后从不释放，长时间筛选会累积成千上万个 QObject 和闭包。 | 父对象持有计时器，没有调用 `deleteLater`。 | 在 `execute_delete`、`action_undo`、`_cancel_pending_deletions` 中对计时器调用 `deleteLater()`。 | 高 |
| L3 | `main.py:877-878`、`318`、`685`、`1221/1224` 等 | 所有诊断信息（扫描警告、加载失败、删除结果）只用 `print` 输出，而启动脚本用 `pythonw.exe`（没有 stdout），**全部丢失**。部分目录扫描失败时用户毫不知情。 | 没有日志或 UI 通道。 | 把扫描警告显示在 OSD 或状态文字中；可选写入用户目录下的日志文件（注意别写进照片目录）。 | 高 |
| L4 | `main.py:977-991`（`save_progress`） | 若 checkpoint 索引及其之前的所有索引都在 `deleted_indices` 中，会写入 `completed`，尽管后面还有照片。 | 回退扫描找不到有效路径时直接当作已完成处理。只有删除失败回退和删除成功交错时才会出现，很少见。 | 找不到有效路径时，改为用 checkpoint 项自身的排序键写 `in_progress`。 | 中 |
| L5 | `main.py:1220`（`execute_delete`） | `send2trash` 在 GUI 线程同步执行，网络共享上可能卡顿几百毫秒。 | 同步 I/O。 | 交给后台线程执行，完成后通过信号回到 GUI；需要注意顺序与回退逻辑，属于中等改动。 | 中 |
| L6 | `main.py:1089-1107`（`request_cache`） | 预加载窗口按索引升序排队，**前一张（index-1）排在当前项前面**。恢复进度、撤销、回退等场景下，当前图要多等一次 spawn 加解码。 | `range(current-1, current+4)` 是升序。 | 改为当前项优先：`[c, c+1, c+2, c+3, c-1]`。 | 高 |
| L7 | `main.py:207-230`（`_decode_preview`） | 每张预览都启动一个新的 spawn 子进程，并重新执行 `main.py` 顶层代码（导入 PyQt6 和 Pillow）。这是预览延迟的主要来源。 | 为了能安全终止而选择「一次请求一个进程」。 | 改为常驻可复用的解码子进程，只在请求过期时终止并重建（详见 `03-perf-plan.md`）。 | 高 |
| L8 | `main.py:1108`、`1182`、`1209` | 每次导航写两次进度文件（`action_*` 一次，`update_view → request_cache` 再一次）。 | 两条路径都调用 `save_progress`。 | 缓存最近一次写入的内容，内容不变时跳过写入（详见 `03-perf-plan.md`）。 | 高 |
| L9 | `启动照片清理.bat:3` | 硬编码作者本机的 `D:\anaconda3\envs\py312-clean\pythonw.exe`，其他机器上无法使用（README 已说明）。 | 个人环境路径。 | 依次尝试 `%~dp0.venv\Scripts\pythonw.exe`、`pyw`、`pythonw`。属于启动配置，本次不改。 | 高 |
| L10 | `main.py:399-405`（`_scan_directory_records`） | 每个目录都调用一次 `os.path.realpath`，对已经不跟随链接的目录树来说是冗余的系统调用（POSIX 上每层路径都要 lstat，Windows 上要打开句柄）。 | 环路检测，但链接和 junction 已经被跳过。 | 只对根目录计算 realpath，子目录直接用拼接路径作为键（详见 `03-perf-plan.md`）。 | 中 |

## 待人工确认

| 编号 | 问题 | 为什么不能自动定论 | 建议 |
| --- | --- | --- | --- |
| Q1 | **网络共享 / 不支持回收站的卷上，删除可能是永久删除。** README 承诺「不会直接永久删除」，但 Windows 上 Send2Trash 使用 `IFileOperation` 并带 `FOF_NOCONFIRMATION`。对于没有回收站的位置（SMB 共享且 NAS 未开启网络回收站、部分 U 盘或映射盘），Shell 的「无法回收，是否永久删除？」确认可能被自动跳过，文件直接被永久删除。扫描器专门跳过 `#recycle`、`@Recycle`、`@eaDir`，说明用户确实在 NAS 上使用本工具。 | 需要在 Windows 上对 SMB 共享和 Send2Trash 2.x 实测，Linux 容器无法验证。 | 在 Windows 上对网络盘实测。如果确认会永久删除：对 `\\` UNC 路径或 `GetDriveTypeW == DRIVE_REMOTE` 的目录在首次删除前弹出明确警告或禁用删除。 |
| Q2 | M7「已完成」后新照片不再出现，是 bug 还是有意设计？ | README 只说「全部筛选完后不会意外从头开始」，没有说明新增照片的处理方式。修复会改变进度文件的语义（「已完成」时也写 `last_sort_key`），属于持久化数据格式变化。 | 确认期望行为后再改。建议：已完成时也记录最后排序键，新照片自动续上。 |
| Q3 | H1 的修复会让「图片还在加载时按 W」被忽略。是否有用户习惯盲按 W 来加速？ | 属于交互取舍。按「最保守、可回退」原则，本次选择阻止对未显示图片的删除。 | 如果希望保留盲删，可以改成按键排队、等图片显示后再生效，或改为显示提示。 |
| Q4 | M5「文件不存在当作删除成功」的修复方向 | 需要确认 Windows 上 SMB 断线时 `os.stat` 抛出的错误类型（`FileNotFoundError`，还是其他 `OSError` 如 `WinError 53/64`）。 | 在 Windows + NAS 上拔网线复现后再修。 |

## 安全

> 阶段 3：全仓库审计（`main.py`、`test.py`、`启动照片清理.bat`、`requirements.txt`、全部 git 历史）。编号接续上文。结论：**未发现高危或中危安全问题**。本应用是本地单用户桌面程序，没有网络服务、数据库、模板、账号体系或 shell 调用。

### 检查项与结论

| 类别 | 检查方法 | 结论 |
| --- | --- | --- |
| SQL / 命令 / 模板注入 | grep `subprocess`、`os.system`、`popen`、`shell=`、`eval`、`exec`、SQL、模板引擎；阅读 `.bat` | 无。`.bat` 只调用固定解释器，`%~dp0` 已加引号。子进程通过 `multiprocessing` 传参数，不经过 shell。 |
| 鉴权与越权 | 架构审查 | 不适用：没有账号、网络端口或权限分级。所有操作都以当前用户身份执行。 |
| 敏感信息硬编码 / 写入日志 | grep 全部 git 历史中的 password/secret/token/key/私钥头 | 历史中没有凭据。`.bat` 含作者本机路径（L9，非机密）。`config.json`（含个人路径）已被 `.gitignore` 排除。日志只有 `print` 输出的文件路径，在 `pythonw` 下不会落地。 |
| 不安全反序列化 | 查看所有 `recv()`、`json.load` | `json.load` 只读取 `config.json` 和进度文件，值仅用于比较或作为目录路径，不会被执行。`multiprocessing` Pipe 的 `recv()` 内部用 pickle，但数据来自本程序自己启动的子进程（见 L12）。 |
| 不可信输入（图片文件） | 阅读解码路径 | 预览解码在子进程中进行，崩溃不会影响 GUI。解码前先检查像素数（24 MP），加上 Pillow 自带的 `MAX_IMAGE_PIXELS` 炸弹保护。放大路径在 GUI 进程解码，但同样先检查像素上限（见 L11）。 |
| 路径与符号链接 | 阅读扫描器和删除路径 | 扫描器不跟随目录链接和 junction；文件链接因 `is_file(follow_symlinks=False)` 为 False 被排除。删除只作用于扫描时得到的路径；即便被替换成链接，`send2trash` 移走的也是链接本身而非目标。 |
| 依赖已知漏洞 | `pip-audit -r requirements.txt` 和对已安装环境运行 `pip-audit`（2026-11-01） | **No known vulnerabilities found**（PyQt6 6.12.0、Pillow 13.0.0、Send2Trash 2.1.0）。下限版本 PyQt6 6.11、Pillow 12.2、Send2Trash 1.8.3 也没有已公开的高危漏洞。 |

### 安全发现

| 编号 | 位置 | 问题 | 原因 | 修复建议 | 把握 |
| --- | --- | --- | --- | --- | --- |
| L11 | `main.py:671-683`（`ZoomableView.set_zoom`） | 原图放大在 **GUI 主进程**内用 Pillow 解码不可信文件，没有享受预览路径的子进程隔离。若 Pillow 解码器存在内存破坏漏洞，恶意图片可以让主程序崩溃，或在用户权限下执行代码。 | 只有预览走子进程。README「每次 Pillow 预览解码都在只读 multiprocessing 子进程中执行」的说法对放大路径不成立。 | 中长期：放大也走（常驻）子进程解码（与 M8、L7 一起处理）。短期：保持 Pillow 版本及时更新。 | 中 |
| L12 | `main.py:236/242/556`（Pipe `recv()`） | 解码子进程处理不可信图片，若被攻破，它回传给父进程的数据会被 **pickle 反序列化**。 | 子进程与父进程是同一用户、同一权限，没有提权边界：攻破子进程本身就已经有同等权限，所以实际增益为零。属于纵深防御问题。另外 README 称子进程「只读」，这只是约定，并不是沙箱。 | 如需加固：回传改为固定结构的 `send_bytes` 头加原始像素（不走 pickle），并在 README 中把「只读」改为「仅执行读取操作」。 | 高（现象）/ 低（实际风险） |
| L13 | `main.py:993-1003`（`save_progress`） | `.sorter_progress.json` 写在照片目录中，并以绝对路径记录 `last_path`（通常含 Windows 用户名，如 `C:\Users\<name>\…`、映射盘符）。照片目录是共享 NAS 时，其他人能看到这些信息。 | 进度文件记录的是绝对路径。 | 改为记录相对根目录的路径（这会改变进度文件格式，需要兼容旧文件，见待人工确认 Q5）。 | 高 |
| L14 | `requirements.txt` | 只有下限、没有上限或锁定：本次安装得到的 **Pillow 13.0.0** 已经超过 README 声明的「以 12.2 测试」的大版本。未来安装可能拉到未验证的大版本（含解码器行为变化）。 | 没有约束或锁文件。 | 增加 `constraints.txt` 或锁文件（例如 `Pillow>=12.2,<14`），并定期运行 `pip-audit`。本次不改依赖声明（RUNBOOK：不升级大版本 / 不改配置）。 | 高 |
| L15 | `main.py:318、685、878、1221、1224` | 诊断信息里有完整文件路径；改为写日志文件时要注意不要把路径列表写入共享目录。 | 目前只 `print`，在 `pythonw` 下不落地，所以**现状无泄露**，仅作为提醒。 | 如果新增日志（L3），写到 `%LOCALAPPDATA%` 而不是照片目录。 | 高 |

### 待人工确认（安全相关）

| 编号 | 问题 | 建议 |
| --- | --- | --- |
| Q5 | L13 把进度改为相对路径会改变持久化格式。是否接受？ | 新格式同时写 `last_relpath`，读取时优先使用它，兼容旧文件。 |
| Q1（见上） | 网络共享上 Send2Trash 是否会永久删除 | 这既是数据安全问题，也是本仓库唯一可能的「高」风险，需要在 Windows 上实测。 |
