# Changelog

本项目所有重要变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Planned（详见 docs/项目交接文档.md）

- 状态文件迁移到 SQLite
- `--reset-state` / `--verify` CLI 选项
- Windows 长路径（`\\?\`）支持
- tqdm 进度条

## [1.1.0] - 2026-07-25

### Fixed
- **跨次运行去重失效**：size 短路只登记 `{size: []}` 而从不计算哈希，导致第二次运行时与第一次重复的文件全部漏检。状态升级 v3，新增 `size_pending` 欠账表，同 size 文件再次出现时回补哈希
- **文件系统时间冒充拍摄时间**：`File:FileModifyDate` 被列入 `DATE_KEYS`，使任何文件都"有"拍摄时间 —— Pillow 分支成死代码，无 EXIF 的照片按拷贝时间归到错误年份且标注 `source=exiftool`。该字段已移出候选，并新增 `date_from_mtime` 计数与汇总告警
- **dry-run 有磁盘副作用**：预演会创建 target/duplicates 及完整的年/月空目录树。mkdir 已移到 dry-run 判断之后
- **Ctrl+C 保护有洞**：MD5 并发与 ExifTool 预热在主循环之前，该阶段中断会穿透 `main()` 的 `except Exception`（`KeyboardInterrupt` 非其子类），状态不存盘、退出码非 130。中断处理上移到 `run()` 收口
- **非跨卷错误被当作跨卷**：`os.replace` 的裸 `except OSError` 会把权限拒绝等错误也退化成 copy 两阶段。改为判定 `errno.EXDEV` / winerror 17

### Added
- HEIC 解码支持（pillow-heif），启用 `--phash` 而缺该依赖时显式告警
- pHash 并发计算：28 张 HEIC / 85.6 MB 实测 6.01s → 2.49s（2.41x）
- ExifTool 预热与 MD5/pHash 线程池重叠执行；预热支持批次间中止
- 27 项新用例（跨次去重、dry-run 副作用、时间来源、中断保护、HEIC 解码、非跨卷错误、状态版本迁移），共 74 项

### Changed
- 实现按职责拆分为 `imageorg/` 包（10 个模块，最大 589 行）；`organizer.py` 保留为兼容入口，`python organizer.py` 与 `from organizer import X` 用法不变
- 状态文件 v2 → v3；v2 可直接加载（旧的短路文件哈希已永久缺失，加载时告警说明）

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
