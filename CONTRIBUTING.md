# Contributing

欢迎贡献代码、报告 bug、提出新功能。

## 开发环境

```bash
conda env create -f environment.yml
conda activate image
```

下载 [ExifTool Windows 版](https://exiftool.org/) 并解压到项目根目录。

## 提交流程

1. Fork & clone 仓库
2. 新建分支：`git checkout -b feat/your-feature` 或 `fix/your-bug`
3. 修改代码 + 加测试
4. 跑全量测试：`pytest tests/ -v`（必须 47 项全过）
5. 提交时遵循 Conventional Commits：
   - `feat:` 新功能
   - `fix:` bug 修复
   - `refactor:` 重构（无行为变化）
   - `docs:` 仅文档
   - `test:` 仅测试
   - `chore:` 杂项
6. 推送 & 提 PR

## 代码规范

- 全量 type hints
- 数据结构用 `dataclass`，不用裸 `dict`
- 用 `logging` 而非 `print`
- 单文件 ≤ 600 行，单函数 ≤ 100 行
- 新加的功能必须配 pytest 用例

## 报告 bug

提 issue 时请附：
- 操作系统 + Python 版本
- ExifTool 版本（`exiftool -ver`）
- 复现步骤 + 完整命令
- 错误日志或控制台输出
- `target/.organizer_state.json`（如能提供）

## 提出新功能

先开 issue 讨论设计，避免做完了被拒。参考 [项目交接文档.md](docs/项目交接文档.md) 了解已规划的待办事项（P2-P5）。

## 文档

主文档在 `docs/` 下：
- [项目技术分析文档.md](docs/项目技术分析文档.md) — 架构与模块拆解
- [功能介绍.md](docs/功能介绍.md) — 功能清单
- [项目交接文档.md](docs/项目交接文档.md) — 维护者交接 / 历史踩坑

修代码时同步更新相关文档。
