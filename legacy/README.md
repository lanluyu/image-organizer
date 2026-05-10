# Legacy 目录

此目录下的脚本均为**早期实现**，已被根目录的 `organizer.py` 取代，仅作历史参考保留。

## 各文件说明

| 文件 | 说明 |
|---|---|
| `1_获取文件夹中所有文件的后缀格式.py` ~ `6_*.py` | 最早按流程拆成 6 个独立小脚本的版本，硬编码路径 |
| `image.py` | 中期整合版，封装为 `ImageTool` 类，仍硬编码路径、用 `print` |
| `media_organizer_class.py` | 过渡重构版本 |
| `media_organizer_optimized.py` | 上一版最完整实现，已被新版 `organizer.py` 全面替代 |
| `获取*HEIC*.py` / `获取照片信息*.py` / `其他工具.py` | 一次性元数据探查工具，调试用 |
| `环境安装.py` | 早期依赖安装脚本，已被 `environment.yml` 取代 |

## 已知问题（新版已修复）

- 全量 MD5 无 size 短路 → 新版先比 size，唯一直接放行
- AAE 单独按内部时间归档 → 新版作为 sidecar 跟随主文件
- Live Photo（HEIC+MOV）可能被拆到不同月份 → 新版按 stem 配对一起移动
- 单点失败中断全流程 → 新版每个文件独立 try，末尾汇总 FAIL/ERROR
- 无断点续跑 → 新版写 `.organizer_state.json`，下次跳过已处理

新功能请使用根目录的 `organizer.py`。
