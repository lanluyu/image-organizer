# Image Organizer

[![Tests](https://github.com/USER/REPO/actions/workflows/test.yml/badge.svg)](https://github.com/USER/REPO/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

iPhone 照片/视频整理工具: 按拍摄时间归档到 `年/月/`，自动去重，保留 Live Photo 配对。

## 特性

- **按 Exif 时间归档** — ExifTool 持久进程 (`-stay_open`) 批量预热 → Pillow 兜底 → 文件 mtime 最后兜底；几千张照片省去几千次冷启动，提速 ~10x
- **字节去重 (size 短路)** — 先按文件大小分桶，size 唯一的文件直接放行，只对同 size 文件算 MD5
- **视觉去重** (可选 `--phash`) — 同一张图不同压缩/分辨率也能识别；BK-tree 索引让查询接近 O(log N)，万张照片可用
- **Live Photo + AAE 配对** — `IMG_xxxx.HEIC` / `IMG_xxxx.MOV` / 编辑变体 `IMG_Exxxx.AAE` 自动归到同组
- **跨卷原子移动** — 同盘 `os.replace`；跨盘 copy → fsync → rename → unlink 两阶段，断电不留半成品
- **断点续跑** — 状态写入 `target/.organizer_state.json`，中断后下次自动跳过已处理
- **批量容错** — 单点失败不中断主流程，末尾打印完整 FAIL/ERROR 清单
- **并发哈希** — `--workers` 控制线程数，IO 密集场景 2-4x 加速
- **Dry-run 预演** — `--dry-run` 只打印不移动

## 快速开始

```bash
# 基础用法
python organizer.py \
    --source     "D:/AHAHA/DCIM/Input_Photos" \
    --target     "D:/AHAHA/DCIM/Organized_Photos" \
    --duplicates "D:/AHAHA/DCIM/Duplicates"

# 先预演看一眼，再实际跑
python organizer.py --source ... --target ... --duplicates ... --dry-run

# 启用视觉去重 + 8 线程并发
python organizer.py --source ... --target ... --duplicates ... --phash --workers 8

# 中断后续跑 (默认开启)；想从头开始用 --no-resume
python organizer.py --source ... --target ... --duplicates ... --no-resume
```

## 依赖

```bash
conda env create -f environment.yml
conda activate image
```

或手动:

```bash
pip install Pillow
pip install imagehash   # 仅 --phash 时需要
```

**ExifTool** (强烈推荐): 从 https://exiftool.org/ 下载 Windows 版。任选一种部署方式：
- 官方 zip 直接解压到项目根：`./exiftool.exe` + `./exiftool_files/`（推荐，官方分发自带）
- 旧约定：`./exiftool/exiftool.exe`

`detect_default_exiftool()` 会自动按上述顺序探测。
没有 ExifTool 时只能依赖 Pillow，HEIC/视频时间会读不到，自动降级到文件 mtime。

## 退出码

| Code | 含义 | 用法 |
|---|---|---|
| 0 | 全部成功 | PowerShell `if ($LASTEXITCODE -eq 0) { ... }` |
| 1 | 有业务失败 (例如个别文件读不到时间) | 主流程已完成，需查清单 |
| 2 | 脚本异常 | 需人工排查 |
| 130 | 用户 Ctrl+C 中断 | 状态已存盘，可续跑 |

## 目录结构

```
Image/
├── organizer.py          # 主入口 (新版)
├── README.md
├── environment.yml
├── exiftool/             # 自行下载放置
│   └── exiftool.exe
└── legacy/               # 旧版本，仅供参考，不再维护
    └── README.md
```

## 设计要点

**为什么 size 短路重要**: 全量 MD5 每个文件都要读完整内容。流程是 `stat → 按 size 分桶 → 唯一 size 直接放行 → 只对同 size 桶 (含历史 size) 算 MD5`。照片几乎不可能字节数完全一样而内容不同，所以唯一 size 的文件可以零 IO 放行。状态文件持久化 `{size: [hash...]}`，断点续跑时跨次去重也走 size 短路。

**为什么 ExifTool 要持久进程**: ExifTool 是 Perl 脚本，单次冷启动 ~200-500ms。旧实现每文件 fork 一次，几千张照片光启动就要十几分钟，远超 MD5 与 IO 时间。新实现走 `-stay_open True -@ -` 协议：单常驻进程通过 stdin 接收文件路径，stdout 返回 JSON，启动开销摊销到 0。`Organizer.run` 在 size 短路决定后批量预热（`prewarm`，每批 100 文件），结果填到 `DateResolver._cache`；后续 `resolve()` 命中缓存零开销，未命中也走同一 daemon 单文件查询。daemon 崩溃时自动重启 (max 3 次)，超限后整体降级到 Pillow/mtime，不影响主流程。**关键陷阱**：stderr 必须 `DEVNULL`（exiftool 默认把 "X image files read" 写到 stderr，合并到 stdout 会污染 JSON 解析）；同时**不能加 `-q`**——`-q` 会把 `{ready}` sentinel 也压制，导致 readline 永久阻塞。

**为什么 pHash 要 BK-tree**: 旧实现每张新图与历史所有 phash 线性比对，复杂度 O(N²)。1 万张照片需要 ~5000 万次 hamming 计算，慢且占 CPU。BK-tree 利用三角不等式 `|d(x,p) − d(y,p)| ≤ d(x,y)` 剪枝，节点按"到父的距离"分桶，查询时只递归到 `[d − threshold, d + threshold]` 桶。实测 500 节点查询正确性与线性扫描完全等价（`test_pruning_does_not_miss_neighbors`），实际复杂度接近 O(log N) — 小阈值（≤4）尤其有效。

**为什么 IMG_E 前缀要归一**: iPhone 编辑过的照片 sidecar 是 `IMG_E1234.AAE`，主图却是 `IMG_1234.HEIC`，stem 不同。配对前对 stem 做 `IMG_E → IMG_` 归一化，否则 AAE 会被当成独立 primary 单独归档。

**为什么 Live Photo 要配对**: HEIC 主图和 MOV 实况视频的 Exif 时间常常差几秒，独立按时间归档会被分到不同月份目录 (跨月拍摄时尤其明显)，破坏 iPhone 重新导入时的实况照片关联。

**为什么 move 要分同卷/跨卷**: `shutil.move` 在跨卷时退化成 copy + unlink (非原子)，中途断电会留下"目标半成品 + 源完好"，下次扫描会再次"成功归档"源文件，造成永久残留。本工具同卷走 `os.replace` 单步原子；跨卷用 `copy → fsync → rename → unlink` 两阶段，任何中断点都可恢复 (.partial 残留可识别清理；rename 后崩溃则源残留会在下次扫描被识别为字节重复进 duplicates)。

**为什么要断点续跑**: 几千张照片处理一次要十几分钟，中途网络盘卡顿、Ctrl+C、电脑休眠都会丢进度。状态文件记录 size→hash 字典和源路径，下次启动直接跳过。

## 测试

```bash
# 在 conda lang 环境下
pytest tests/ -v
```

覆盖：`group_files` (含 IMG_E AAE 配对回归)、`size 短路` 决策、`_atomic_move` 同卷/跨卷/失败清理、`ExifToolDaemon` 协议/崩溃重启/close 幂等、`DateResolver` 缓存命中与 daemon 失败降级、`PHashBKTree` 插入/查询/剪枝正确性 + Organizer 视觉去重集成。共 47 用例。

## 文档

| 文档 | 受众 | 内容 |
|---|---|---|
| [README.md](README.md) | 所有人 | 上手与使用 |
| [docs/功能介绍.md](docs/功能介绍.md) | 用户 | 全功能清单与场景 |
| [docs/项目技术分析文档.md](docs/项目技术分析文档.md) | 维护者 | 架构、模块、算法 |
| [docs/项目交接文档.md](docs/项目交接文档.md) | 接手人 | 待办、踩坑、路线图 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献者 | 开发流程与规范 |
| [CHANGELOG.md](CHANGELOG.md) | 所有人 | 版本变更记录 |

## License

[MIT](LICENSE)
