# 00 · 审查与优化总结

> 按 `docs/review/RUNBOOK.md` 无人值守执行，阶段 0–7 全部完成。没有推送到 `main`，没有 force push，没有合并任何 PR，没有删除任何远程分支。
> **后续**：之后按你的要求解决了冲突，并依次合并了 #3 → #4 → #5（目标均为 `claude/review-fixes`）；#2 仍未合并。
> 详细记录：[`PROGRESS.md`](PROGRESS.md) · [`01-architecture.md`](01-architecture.md) · [`02-findings.md`](02-findings.md) · [`03-perf-plan.md`](03-perf-plan.md)

## 一句话结论

代码整体质量较高（会话隔离、子进程解码、协作式关闭都考虑得很周到）。审查发现 **1 个高危问题**（图片还在「加载中」就能按 W 删除，可能删掉用户没看到的照片），已修复。安全方面**没有高危或中危问题**，依赖未发现已知漏洞。性能方面选了 3 个低风险热点优化，各自降低 46%–49%。

## 完成了什么

| 阶段 | 结果 |
| --- | --- |
| 0 准备 | 基线：32 项测试，31 通过、0 失败、1 跳过（仅 Windows）；没有基准测试 |
| 1 架构 | `01-architecture.md`：单文件应用的组件划分、数据流、外部依赖、高风险区域、审查顺序 |
| 2 代码审查 | `/code-review`（high）加人工复核：**高 1 / 中 8 / 低 10**；用实验确认了 EXIF 时间读取（M3）和放大格式支持（M4）两处 bug |
| 3 安全审计 | 注入、鉴权、凭据（含全部 git 历史）、反序列化、路径、`pip-audit`：无高危或中危；补充低级别问题 L11–L15 |
| 4 修复高危 | H1 已修复并加回归测试（先确认它在修复前失败）→ PR #2 |
| 5 性能分析 | 新增 `bench/`（预览解码、spawn 往返、扫描、进度读写、内存、加载优先级）；找出 7 个热点，按「收益高、风险低」选出前 3 项 |
| 6 算法优化 | P1、P2、P3 各开一个分支：先提交特征测试，再提交优化；每项都只尝试 1 种方案就达到 ≥ 20% → PR #3、#4、#5 |
| 7 总结 | 本文件 |

## 所有 PR（#3–#5 已按要求合并到 `claude/review-fixes`；#2 待审阅）

| PR | 分支 → 目标 | 内容 | 关键数据 | 测试 |
| --- | --- | --- | --- | --- |
| [OranngeAustin/fast-photo-sorter#2](https://github.com/OranngeAustin/fast-photo-sorter/pull/2) | `claude/review-fixes` → `main` | H1 修复与全部审查文档、基准脚本 | — | 33 项：32 通过、1 跳过 |
| [OranngeAustin/fast-photo-sorter#3](https://github.com/OranngeAustin/fast-photo-sorter/pull/3)（✅ 已合并） | `claude/perf-jpeg-draft` → `claude/review-fixes` | P1：预览先缩放再转正，JPEG 按 draft 尺寸解码 | 子进程解码 351 → 177 ms（**−48.6%**），单张往返 −36.6%，峰值 RSS 160 → 124 MB（−22%） | 39 项：38 通过、1 跳过 |
| [OranngeAustin/fast-photo-sorter#4](https://github.com/OranngeAustin/fast-photo-sorter/pull/4)（✅ 已合并） | `claude/perf-progress-writes` → `claude/review-fixes` | P2：进度检查点内容未变时不重复写入 | 每次按键写入次数 2 → 1（**−50%**），1000 次按键耗时约 −46% | 37 项：36 通过、1 跳过 |
| [OranngeAustin/fast-photo-sorter#5](https://github.com/OranngeAustin/fast-photo-sorter/pull/5)（✅ 已合并） | `claude/perf-load-priority` → `claude/review-fixes` | P3：预加载先解码当前照片 | 窗口为空时当前图到达 455 → 245 ms（**约 −46%**） | 37 项：36 通过、1 跳过 |

**main 的变化**：执行期间有人合并了 PR #1（新增 `CLAUDE.md` 和 `.claude/settings.json`，不是本流程合并的）。已确认 #2 与新 `main` 没有冲突，新 `CLAUDE.md` 也没有忽略目录清单。合并 #3–#5 后，记得把 `CLAUDE.md` 中「32 tests」更新为 47。

**合并结果**：已按 #3 → #4 → #5 的顺序合并。#4、#5 合并前都先把最新的 `claude/review-fixes` 合入各自分支，解决了 `test.py` 末尾（各分支追加的测试类全部保留）和 README 测试数量两处冲突，没有改写历史。合并后 `claude/review-fixes` 共 **47 项测试，46 通过、0 失败、1 跳过**，与 `main` 无冲突。

## 跳过项及原因

| 项目 | 原因 |
| --- | --- |
| 中级 M1–M8、低级 L1–L15 | RUNBOOK 阶段 4 只修「高」级且把握大的问题，其余只记录（见 `02-findings.md`） |
| P4 常驻解码子进程 | 收益最大（每张预览省约 245 ms spawn 开销，Windows 上更多），但涉及子进程生命周期和关闭路径，风险中等。无人值守时按「最保守、可回退」原则没有做 |
| P5 扫描时每个目录都 realpath、P6 bisect 恢复、P7 放大后台化 | 排名在前 3 之后（P6 收益可忽略，P7 属于 UI 重构） |
| Windows 专属行为验证（隐藏/系统属性测试、junction、回收站） | 云端是 Linux；`test_scanner_skips_windows_hidden_and_system_entries` 在基线和各分支上都是「跳过」 |

没有任何任务因为「连续 3 次失败」而回退。

## 待人工确认

| 编号 | 问题 | 需要你做什么 |
| --- | --- | --- |
| **Q1** | **网络共享上删除可能是永久删除**：Windows 上 Send2Trash 使用 `IFileOperation` 并带 `FOF_NOCONFIRMATION`，对于没有回收站的位置（SMB 共享未开 NAS 回收站、部分映射盘），可能直接永久删除。这与 README「不会直接永久删除」的承诺冲突。如果属实，这是本仓库**最严重的风险** | 在 Windows 上对 NAS 共享目录里的测试文件按 W，检查回收站和 NAS `#recycle` |
| Q2 | M7：标记「已完成」后，新加入的照片永远不会出现。是 bug 还是有意设计？修复需要改变进度文件语义 | 确认期望行为 |
| Q3 | H1 的修复会忽略「加载中」时按下的 W。是否有用户习惯盲按？ | 确认交互取舍；如需要，可改为按键排队，等图片显示后再生效 |
| Q4 | M5：SMB 断线时 `os.stat` 抛出的错误类型 | 在 Windows + NAS 上断网复现 |
| Q5 | L13：进度文件改为记录相对路径（避免在共享目录暴露用户名）会改变持久化格式 | 确认是否接受（可以做成兼容旧文件） |

## 建议你下一步做什么

1. **先验证 Q1**（10 分钟）：这关系到误删能否恢复，结果决定是否要给网络盘加删除警告。
2. ~~审阅并合并 #3 → #4 → #5~~ 已完成；接下来审阅并合并 #2 到 `main`。
3. 开一个小 PR 修中级里低风险、高价值的几项：**M1**（关闭期间忽略按键，一行代码）、**M2**（扫描有警告时不删进度文件）、**M3**（EXIF 拍摄时间从 Exif 子 IFD 读取）、**M4**（放大前转 RGBA）、**M6**（进度原子写入）。这些都有明确的复现方式，修复范围很小。
4. 再考虑 P4（常驻解码子进程）：这是剩下最大的性能收益（Windows 上每张预览可能省 0.3–1 秒），建议先在 Windows 上用 `bench/bench_preview.py` 量一下 spawn 开销。
5. 给 `requirements.txt` 加上限或锁文件（L14）：本次安装已经拉到超出测试范围的 Pillow 13。
