# Image Organizer

[![Tests](https://github.com/lanluyu/image-organizer/actions/workflows/test.yml/badge.svg)](https://github.com/lanluyu/image-organizer/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

iPhone 照片/视频整理工具: 按拍摄时间归档到 `年/月/`，自动去重，保留 Live Photo 配对。

## 特性

- **按 Exif 时间归档** — ExifTool 持久进程 (`-stay_open`) 批量预热 → Pillow 兜底 → 文件 mtime 最后兜底；几千张照片省去几千次冷启动，提速 ~10x
- **字节去重 (size 短路)** — 先按文件大小分桶，size 唯一的文件直接放行，只对同 size 文件算 MD5；短路放行的文件记入欠账，同 size 再次出现时回补哈希，跨次运行也不漏检
- **视觉去重** (可选 `--phash`) — 同一张图不同压缩/分辨率也能识别；BK-tree 索引让查询接近 O(log N)，万张照片可用；解码走线程池并发，含 HEIC (需 pillow-heif)
- **归档时间来源如实标注** — 没有 EXIF 拍摄时间的文件降级到文件 mtime，并在汇总里明确计数告警，不会把"拷贝时间"当成"拍摄时间"悄悄归错年份
- **Live Photo + AAE 配对** — `IMG_xxxx.HEIC` / `IMG_xxxx.MOV` / 编辑变体 `IMG_Exxxx.AAE` 自动归到同组
- **跨卷原子移动** — 同盘 `os.replace`；跨盘 copy → fsync → rename → unlink 两阶段，断电不留半成品
- **断点续跑** — 状态写入 `target/.organizer_state.json`，中断后下次自动跳过已处理
- **批量容错** — 单点失败不中断主流程，末尾打印完整 FAIL/ERROR 清单
- **并发哈希** — `--workers` 控制线程数，IO 密集场景 2-4x 加速
- **Dry-run 预演** — `--dry-run` 只打印不移动

## 快速开始

**交互式（推荐首次使用）**：双击 `启动处理照片.bat`。会打开一个 PowerShell 7 窗口，引导填写三个目录与选项，**先跑一次预演**，确认无误后才真正移动文件。上次的配置会记住，下次一路回车即可。

**固定配置一键跑**：编辑 `run.bat` 顶部的三个路径变量后双击，不交互。

**命令行**：

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

本项目运行在既有的 conda 环境 `douyin`（与抖音视频分析项目共用）。该环境已存在，补依赖用 `update` 而非 `create`：

```bash
conda env update -n douyin -f environment.yml   # 切勿加 --prune
conda activate douyin
```

或手动:

```bash
pip install Pillow
pip install imagehash     # 仅 --phash 时需要
pip install pillow-heif   # HEIC 解码；缺它则 --phash 对 HEIC 无效 (启动时会告警)
```

**ExifTool**: 仓库已自带 Windows 版（`./exiftool.exe` + `./exiftool_files/`，根据 Artistic/GPL 协议再分发），开箱即用，无需另行下载。

如需升级或换平台版本，从 https://exiftool.org/ 下载后覆盖到项目根即可。`detect_default_exiftool()` 也兼容旧约定 `./exiftool/exiftool.exe`。
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
├── organizer.py          # 兼容入口: python organizer.py ... / from organizer import X
├── imageorg/             # 实现
│   ├── imaging.py        # Pillow / imagehash / pillow-heif 软依赖入口
│   ├── constants.py      # 扩展名、时间字段优先级、运行常量
│   ├── models.py         # Config / Stats / FailureRecord
│   ├── exiftool.py       # ExifToolDaemon (-stay_open 协议)
│   ├── dates.py          # DateResolver (ExifTool → Pillow → mtime)
│   ├── grouping.py       # Live Photo / AAE 配对分组
│   ├── dedup.py          # md5 / phash / PHashBKTree
│   ├── state.py          # 断点续跑状态 v3
│   ├── core.py           # Organizer 主调度
│   └── cli.py            # 命令行
├── tests/                # 74 用例
├── exiftool.exe          # 仓库自带 (含 exiftool_files/)
└── legacy/               # 旧版本，仅供参考，不再维护
```

## 设计要点

**为什么 size 短路重要**: 全量 MD5 每个文件都要读完整内容。流程是 `stat → 按 size 分桶 → 唯一 size 直接放行 → 只对同 size 桶 (含历史 size) 算 MD5`。照片几乎不可能字节数完全一样而内容不同，所以唯一 size 的文件可以零 IO 放行。

**为什么短路要记欠账 (`size_pending`)**: 短路放行意味着这个文件的 MD5 **从未算过**。若状态里只记 `{size: []}`，下次运行遇到字节完全相同的副本时，补算出的哈希与空桶比对必然不匹配 —— 分批导入照片时，第二批与第一批的重复会全部漏检。所以短路放行的文件要把归档后的路径记入 `size_pending`，同 size 文件再次出现时先回补它们的哈希再比对。参照物被用户手工删除时跳过并告警，不影响本次归档。

**为什么 mtime 不能冒充拍摄时间**: exiftool 对任何文件都会返回 `File:FileModifyDate`（就是 mtime）。若把它列入拍摄时间候选，`_parse_exif_record` 永远有返回值，Pillow 分支成为死代码，而从 iPhone 拷出、EXIF 被剥掉的照片会按**拷贝时间**归到错误年份，日志却显示 `source=exiftool`。因此该字段刻意不在 `DATE_KEYS` 内：没有真实拍摄时间就一路降级到 mtime，由 `source` 标签如实反映，并在汇总里计数告警。

**为什么 pHash 要并发**: 图像解码远比 MD5 昂贵（整张图解码 + DCT），串行会让 `--phash` 成为整轮瓶颈。实测 28 张 HEIC / 85.6 MB：串行 6.01s → 8 线程 2.49s（2.41x），结果逐一致。算不出 pHash 的文件记空串而非 None，避免主循环再徒劳解码一次。

**为什么 ExifTool 要持久进程**: ExifTool 是 Perl 脚本，单次冷启动 ~200-500ms。旧实现每文件 fork 一次，几千张照片光启动就要十几分钟，远超 MD5 与 IO 时间。新实现走 `-stay_open True -@ -` 协议：单常驻进程通过 stdin 接收文件路径，stdout 返回 JSON，启动开销摊销到 0。`Organizer.run` 在 size 短路决定后批量预热（`prewarm`，每批 100 文件），结果填到 `DateResolver._cache`；后续 `resolve()` 命中缓存零开销，未命中也走同一 daemon 单文件查询。daemon 崩溃时自动重启 (max 3 次)，超限后整体降级到 Pillow/mtime，不影响主流程。**关键陷阱**：stderr 必须 `DEVNULL`（exiftool 默认把 "X image files read" 写到 stderr，合并到 stdout 会污染 JSON 解析）；同时**不能加 `-q`**——`-q` 会把 `{ready}` sentinel 也压制，导致 readline 永久阻塞。

**为什么 pHash 要 BK-tree**: 旧实现每张新图与历史所有 phash 线性比对，复杂度 O(N²)。1 万张照片需要 ~5000 万次 hamming 计算，慢且占 CPU。BK-tree 利用三角不等式 `|d(x,p) − d(y,p)| ≤ d(x,y)` 剪枝，节点按"到父的距离"分桶，查询时只递归到 `[d − threshold, d + threshold]` 桶。实测 500 节点查询正确性与线性扫描完全等价（`test_pruning_does_not_miss_neighbors`），实际复杂度接近 O(log N) — 小阈值（≤4）尤其有效。

**为什么 IMG_E 前缀要归一**: iPhone 编辑过的照片 sidecar 是 `IMG_E1234.AAE`，主图却是 `IMG_1234.HEIC`，stem 不同。配对前对 stem 做 `IMG_E → IMG_` 归一化，否则 AAE 会被当成独立 primary 单独归档。

**为什么 Live Photo 要配对**: HEIC 主图和 MOV 实况视频的 Exif 时间常常差几秒，独立按时间归档会被分到不同月份目录 (跨月拍摄时尤其明显)，破坏 iPhone 重新导入时的实况照片关联。

**为什么 move 要分同卷/跨卷**: `shutil.move` 在跨卷时退化成 copy + unlink (非原子)，中途断电会留下"目标半成品 + 源完好"，下次扫描会再次"成功归档"源文件，造成永久残留。本工具同卷走 `os.replace` 单步原子；跨卷用 `copy → fsync → rename → unlink` 两阶段，任何中断点都可恢复 (.partial 残留可识别清理；rename 后崩溃则源残留会在下次扫描被识别为字节重复进 duplicates)。

**为什么要断点续跑**: 几千张照片处理一次要十几分钟，中途网络盘卡顿、Ctrl+C、电脑休眠都会丢进度。状态文件记录 size→hash 字典和源路径，下次启动直接跳过。中断保护覆盖**全部阶段**（扫描/MD5/pHash/预热/主循环）：MD5 并发段往往是整轮最耗时的部分，而 `KeyboardInterrupt` 不是 `Exception` 子类，只在主循环里接会让它穿透 `main()` 的兜底 —— 状态不存盘、退出码也不是 130。后台预热线程通过中止标志在批次间退出，线程池用 `cancel_futures` 取消排队任务，Ctrl+C 不会被几千个已排队任务拖住。

## 测试

```bash
# 在 conda douyin 环境下
pytest tests/ -v
```

覆盖：`group_files` (含 IMG_E AAE 配对回归)、`size 短路` 决策与跨次运行去重、`_atomic_move` 同卷/跨卷/非跨卷错误/失败清理、`ExifToolDaemon` 协议/崩溃重启/close 幂等、`DateResolver` 缓存命中与 daemon 失败降级、拍摄时间来源标注、dry-run 无副作用、Ctrl+C 覆盖前置阶段、HEIC 解码、`PHashBKTree` 插入/查询/剪枝正确性 + Organizer 视觉去重集成、状态文件 v1/v2/v3 迁移。共 74 用例（未装 pillow-heif 时 2 项 HEIC 解码用例自动跳过）。

## 文档

| 文档 | 受众 | 内容 |
|---|---|---|
| [README.md](README.md) | 所有人 | 上手与使用 |
| [docs/功能介绍.html](docs/功能介绍.html) | 用户 | 全功能清单与场景 |
| [docs/项目技术分析文档.html](docs/项目技术分析文档.html) | 维护者 | 架构、模块、算法 |
| [docs/项目交接文档.html](docs/项目交接文档.html) | 接手人 | 待办、踩坑、路线图 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献者 | 开发流程与规范 |
| [CHANGELOG.md](CHANGELOG.md) | 所有人 | 版本变更记录 |

## License

[MIT](LICENSE)
