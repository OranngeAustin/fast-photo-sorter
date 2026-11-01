# 审查与优化进度

> 依据 `docs/review/RUNBOOK.md` 无人值守执行。状态图例：✅ 完成 · 🚧 进行中 · ⬜ 未开始 · ⏭ 跳过（附原因）

## 阶段清单

| 阶段 | 内容 | 状态 | 产出 / 备注 |
| --- | --- | --- | --- |
| 0 | 准备：安装依赖、跑测试基线、建分支、建本文件 | ✅ 完成 | 见下方「基线」 |
| 1 | 架构摸底 → `01-architecture.md` | ⬜ 未开始 | |
| 2 | 代码审查 → `02-findings.md` | ⬜ 未开始 | |
| 3 | 安全审计 → `02-findings.md`「安全」一节 | ⬜ 未开始 | |
| 4 | 修复高危问题（`claude/review-fixes` → PR 到 main） | ⬜ 未开始 | |
| 5 | 性能分析 → `03-perf-plan.md` | ⬜ 未开始 | |
| 6 | 算法优化（前 3 项，各自 `claude/perf-*` 分支 → PR 到 `claude/review-fixes`） | ⬜ 未开始 | |
| 7 | 总结 → `00-SUMMARY.md` | ⬜ 未开始 | |

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
| D4 | 仓库无 `CLAUDE.md`，因此没有需要跳过的目录 | RUNBOOK 要求跳过 CLAUDE.md 中的忽略目录 |

## 日志

- 阶段 0：安装依赖并跑通基线；创建 `claude/review-fixes` 与本文件。
