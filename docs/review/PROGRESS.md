# 审查与优化进度

> 依据 `docs/review/RUNBOOK.md` 无人值守执行。状态图例：✅ 完成 · 🚧 进行中 · ⬜ 未开始 · ⏭ 跳过（附原因）

## 阶段清单

| 阶段 | 内容 | 状态 | 产出 / 备注 |
| --- | --- | --- | --- |
| 0 | 准备：安装依赖、跑测试基线、建分支、建本文件 | ✅ 完成 | 见下方「基线」 |
| 1 | 架构摸底 → `01-architecture.md` | ✅ 完成 | 单文件应用；按风险排出 9 段审查顺序 |
| 2 | 代码审查 → `02-findings.md` | ✅ 完成 | 用 `/code-review`（high）加人工复核：高 1 / 中 8 / 低 10 / 待人工确认 4 |
| 3 | 安全审计 → `02-findings.md`「安全」一节 | ✅ 完成 | 无高危或中危；新增 L11–L15、Q5；`pip-audit` 未发现已知漏洞 |
| 4 | 修复高危问题（`claude/review-fixes` → PR 到 main） | ✅ 完成 | H1 已修复并加回归测试；33 项测试 32 通过、0 失败、1 跳过；PR [OranngeAustin/fast-photo-sorter#2](https://github.com/OranngeAustin/fast-photo-sorter/pull/2) |
| 5 | 性能分析 → `03-perf-plan.md` | ✅ 完成 | 基准脚本在 `bench/`；7 个热点；入选 P1 JPEG draft、P2 进度写入去重、P3 预加载优先级 |
| 6 | 算法优化（前 3 项，各自 `claude/perf-*` 分支 → PR 到 `claude/review-fixes`） | ✅ 完成 | 6.1 P1 ✅ [#3](https://github.com/OranngeAustin/fast-photo-sorter/pull/3)：解码 −48.6%，峰值内存 −22% · 6.2 P2 ✅ [#4](https://github.com/OranngeAustin/fast-photo-sorter/pull/4)：写入次数 −50%，按键耗时 −46% · 6.3 P3 ✅ [#5](https://github.com/OranngeAustin/fast-photo-sorter/pull/5)：冷窗口当前图到达 −46% |
| 7 | 总结 → `00-SUMMARY.md` | ✅ 完成 | 含全部 PR 链接、跳过项、待人工确认、下一步建议 |

## 基线（阶段 0）

- 环境：Linux 云容器，Python 3.13.18（项目声明以 3.12 测试），`QT_QPA_PLATFORM=offscreen`
- 依赖（按 `requirements.txt` 下限安装到隔离 venv，得到的最新版本）：PyQt6 6.12.0 / PyQt6-Qt6 6.12.0、Pillow 13.0.0、Send2Trash 2.1.0
- 测试命令：`QT_QPA_PLATFORM=offscreen python -X utf8 -B -m unittest -v test.py`
- 测试结果：**共 32 项，31 通过，0 失败，0 错误，1 跳过**（`test_scanner_skips_windows_hidden_and_system_entries`，仅 Windows 运行）；耗时约 3.9 s
- 已有失败测试：无。后续标准：失败数保持 0，跳过数不增加。
- 基准测试：仓库内无基准测试（阶段 5 新增，放在 `bench/`，不影响现有测试）。

## 决策记录

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | `claude/review-fixes` 从 `origin/main`（d116f15，已包含 RUNBOOK）创建，并取消对 `origin/main` 的上游跟踪，推送时始终显式写 `origin claude/review-fixes` | 避免任何误推到 main 的可能 |
| D2 | 依赖装在仓库外的隔离 venv，不修改 `requirements.txt` | 遵守「不升级依赖大版本 / 不改生产配置」 |
| D3 | 运行环境是 Linux 而非项目目标平台 Windows：Windows 专属行为（隐藏/系统属性、junction、回收站）只能静态审查，相关结论在发现清单里标注把握程度 | 云端无法运行 Windows |
| D4 | 阶段 0 时仓库没有 `CLAUDE.md`，所以没有需要跳过的目录。执行期间有人把 PR #1 合并进 `main`（929f857，新增 `CLAUDE.md` 和 `.claude/settings.json`，**不是本流程合并的**）。复核后：其中没有忽略目录清单；本次改动符合它的约定（面向用户的新文字都是中文，新逻辑沿用 session ID 模式）；PR #2 仍可以干净合并进新的 `main` | RUNBOOK 要求跳过 CLAUDE.md 中的忽略目录 |
| D5 | 阶段 2 用 `/code-review`（high 档），审查对象是 `main.py` 全文件，而不是 diff | 用户补充说明：仓库没有 code-reviewer 子代理；审查目标是整个仓库，没有待审 diff |
| D6 | 阶段 4 只修 H1。M1（关闭期间仍响应按键）虽然也涉及删除，但属于「中」级，按 RUNBOOK 不修 | RUNBOOK：只修「高」级且把握大的问题 |
| D7 | 收益最大的 P4（常驻解码子进程）不进入阶段 6，改列为人工下一步建议 | 风险中等（进程生命周期、关闭路径），与「最保守、可回退」原则冲突；入选的 3 项都是低风险 |
| D8 | P1 的 JPEG 采用 `reducing_gap=1.0`（像素约差 3/255），没有用 Pillow 默认的 2.0 | 实测默认 2.0 在 1080p 屏幕上看 12 MP 照片时只快约 3%；1.0 快约 49%，且特征测试中与公平 LANCZOS 参考的差异远低于 6/255 阈值。非 JPEG 格式保持逐字节一致 |
| D9 | 为检查 #3/#4/#5 之间的合并冲突，先在本地建了临时分支 `tmp-merge-check`（从未推送），随即用 `git branch -D` 删除；之后改用分离 HEAD 的临时 worktree 验证 | 如实记录：RUNBOOK 禁止删除分支。这个本地分支是本次会话刚建的临时分支，没有推送，也不影响任何已有分支或远程分支；之后的检查都避免了创建分支 |

## 日志

- 阶段 0：安装依赖并跑通基线；创建 `claude/review-fixes` 与本文件。
- 阶段 1：读完全部代码，写出 `01-architecture.md`。用实验确认两处行为：EXIF `DateTimeOriginal` 位于 Exif 子 IFD，`getexif()` 顶层读不到；`ImageQt` 不支持 CMYK/LA/I/F 模式。
- 阶段 2：`/code-review` 报告 10 条，人工复核补充到 19 条，另有 4 条待人工确认，写入 `02-findings.md`。只有 H1（加载中按 W 会删除未看到的照片）定为「高」且把握大。
- 阶段 3：静态检查注入、鉴权、凭据、反序列化、路径；对全部 git 历史搜索凭据；`pip-audit` 未发现已知漏洞。追加 5 条低级别问题和 1 条待确认项。
- 阶段 4：先写 H1 回归测试，确认它在修复前失败；修复后全部测试通过；推送并创建 PR #2（目标 main，未合并）。
- 阶段 5：新增 `bench/`（preview、scan、progress、memory），测得基线并写出 `03-perf-plan.md`。
- 阶段 6.1（P1）：`claude/perf-jpeg-draft`，先提交特征测试，再提交优化；第 1 种方案即达标；PR #3 → `claude/review-fixes`（未合并）。
- 阶段 6.2（P2）：`claude/perf-progress-writes`，先提交特征测试，再提交优化；第 1 种方案即达标；PR #4 → `claude/review-fixes`（未合并）。
- 阶段 6.3（P3）：`claude/perf-load-priority`，先提交特征测试，再提交优化并新增 `bench_load_priority.py`；第 1 种方案即达标；PR #5 → `claude/review-fixes`（未合并）。
- 阶段 7：用临时 worktree 验证三个 perf 分支合入后共 47 项测试、0 失败；写出 `00-SUMMARY.md`。全部阶段完成。
- 收尾复核：`origin/main` 在执行期间被他人更新（合并了 PR #1）；确认 PR #2 与新 main 没有冲突，新 `CLAUDE.md` 不影响已完成的工作。
- 审查后追加（用户要求）：`claude/fix-medium-issues` 修复 M1、M2、M3、M4、M6，每项都先写回归测试并确认它在修复前失败；38 项测试 37 通过、0 失败、1 跳过；PR #6 → `claude/review-fixes`（未合并）。
