# Changelog

本项目所有重要变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Planned (P2-P5，详见 docs/项目交接文档.md)

- HEIC 直读支持（pillow-heif）
- 状态文件迁移到 SQLite
- `--reset-state` / `--verify` CLI 选项
- Windows 长路径（`\\?\`）支持
- tqdm 进度条
- E2E 真实移动测试

## [1.0.0] - 2026-05-10

### Added
- ExifTool `-stay_open` 持久进程协议，批量预热提速 ~10x
- pHash BK-tree 索引，视觉去重接近 O(log N)
- 跨卷原子移动（copy → fsync → rename → unlink）
- 断点续跑：状态文件 v2 格式（`{size: [hash]}`）
- IMG_E 前缀归一化，正确配对编辑过的 Live Photo + AAE
- 47 项 pytest 用例覆盖关键路径

### Changed
- size 短路去重：唯一 size 直接放行不算 MD5
- 状态文件 v1 → v2 迁移（向后兼容加载）
- README、environment.yml 更新部署说明

### Fixed
- ExifTool stderr 污染 JSON 解析（必须 `DEVNULL`）
- ExifTool `-q` flag 抑制 `{ready}` sentinel 导致 readline 永久阻塞
- 跨卷 `shutil.move` 非原子断电残留问题
- 编辑过的 Live Photo 因 `IMG_E` 前缀不匹配被错误拆开

### Removed
- 旧实现的 per-file `subprocess.run("exiftool", ...)`

## [0.x] - 历史

参见 `legacy/` 目录中的早期单文件脚本，仅供参考，不再维护。
