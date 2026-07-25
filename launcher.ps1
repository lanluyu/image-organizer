#requires -Version 7.0
<#
    iPhone 照片整理 — 交互式启动器

    由 启动处理照片.bat 拉起。负责：说明用途 → 收集目录与选项 → 先预演 → 确认后执行。
    所有实际工作仍由 organizer.py 完成，本脚本不碰任何照片文件。
#>

$ErrorActionPreference = 'Stop'
# 控制台可能停留在 OEM 代码页 (中文 Windows 为 936)，两端都切到 UTF-8，
# 否则中文提示会花屏、粘贴含中文的路径也会被截断。
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
try { [Console]::InputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python     = 'D:\soft\Miniconda\envs\douyin\python.exe'
$Organizer  = Join-Path $Root 'organizer.py'
$ConfigFile = Join-Path $Root '.launcher-config.json'
$LogDir     = Join-Path $Root 'logs'

# 与 imageorg/constants.py 保持一致，仅用于运行前预估文件数量
$MediaExt = @('.jpg', '.jpeg', '.png', '.gif', '.heic', '.dng', '.tiff', '.tif',
              '.jfif', '.ico', '.bmp', '.webp', '.mp4', '.mov', '.avi', '.mkv',
              '.flv', '.wmv', '.webm', '.m4v', '.3gp', '.aae')

# ============================================================
# 输出helpers
# ============================================================

function Write-Rule([string]$Title) {
    Write-Host ''
    Write-Host ('─' * 66) -ForegroundColor DarkGray
    if ($Title) { Write-Host "  $Title" -ForegroundColor Cyan }
    Write-Host ('─' * 66) -ForegroundColor DarkGray
}

function Write-Kv([string]$Key, [string]$Value, [string]$Color = 'White') {
    Write-Host ('  {0,-12}' -f $Key) -NoNewline -ForegroundColor DarkGray
    Write-Host $Value -ForegroundColor $Color
}

function Write-Tip([string]$Text) { Write-Host "  $Text" -ForegroundColor DarkGray }

function Stop-WithPause([string]$Message, [int]$Code = 2) {
    Write-Host ''
    Write-Host "  [错误] $Message" -ForegroundColor Red
    Write-Host ''
    Read-Host '  按回车关闭'
    exit $Code
}

# 不读输入的中止路径：输入流已结束时再 Read-Host 会立刻返回空值并陷入死循环
function Stop-Abort([string]$Message) {
    Write-Host ''
    Write-Host "  [中止] $Message" -ForegroundColor Red
    Write-Host ''
    exit 2
}

# 每个交互循环的重试上限。正常人不会连错 10 次；触发通常意味着
# 输入流已到 EOF（被重定向 / 管道喂完了），此时必须退出而不是空转。
$script:MaxTries = 10

# ============================================================
# 输入helpers
# ============================================================

# 支持拖拽进来的带引号路径、环境变量、结尾反斜杠
function Resolve-InputPath([string]$Raw) {
    if ([string]::IsNullOrWhiteSpace($Raw)) { return $null }
    $p = $Raw.Trim().Trim('"').Trim("'").Trim()
    $p = [Environment]::ExpandEnvironmentVariables($p)
    if ($p.Length -gt 3) { $p = $p.TrimEnd('\') }
    if ($p -match '^[A-Za-z]:$') { $p += '\' }   # "D:" → "D:\"
    return $p
}

function Read-Dir([string]$Prompt, [string]$Default, [switch]$MustExist) {
    $tries = 0
    while ($true) {
        if (++$tries -gt $script:MaxTries) { Stop-Abort '连续多次未得到有效目录，输入流可能已结束。' }
        $hint = if ($Default) { " [$Default]" } else { '' }
        Write-Host ''
        Write-Host "  $Prompt$hint" -ForegroundColor White
        Write-Host '  > ' -NoNewline -ForegroundColor Cyan
        $path = Resolve-InputPath (Read-Host)
        if (-not $path) { $path = $Default }
        if (-not $path) { Write-Host '  路径不能为空。' -ForegroundColor Yellow; continue }

        if ($MustExist -and -not (Test-Path -LiteralPath $path -PathType Container)) {
            Write-Host "  目录不存在：$path" -ForegroundColor Yellow
            continue
        }
        return $path
    }
}

function Read-YesNo([string]$Prompt, [bool]$Default) {
    $hint = if ($Default) { 'Y/n' } else { 'y/N' }
    $tries = 0
    while ($true) {
        if (++$tries -gt $script:MaxTries) { Stop-Abort '连续多次未得到有效答复，输入流可能已结束。' }
        Write-Host ''
        Write-Host "  $Prompt [$hint]" -ForegroundColor White
        Write-Host '  > ' -NoNewline -ForegroundColor Cyan
        $a = (Read-Host).Trim().ToLower()
        if (-not $a) { return $Default }
        if ($a -in 'y', 'yes', '是') { return $true }
        if ($a -in 'n', 'no', '否') { return $false }
        Write-Host '  请输入 y 或 n。' -ForegroundColor Yellow
    }
}

function Read-Int([string]$Prompt, [int]$Default, [int]$Min, [int]$Max) {
    $tries = 0
    while ($true) {
        if (++$tries -gt $script:MaxTries) { Stop-Abort '连续多次未得到有效数值，输入流可能已结束。' }
        Write-Host ''
        Write-Host "  $Prompt [$Default]" -ForegroundColor White
        Write-Host '  > ' -NoNewline -ForegroundColor Cyan
        $a = (Read-Host).Trim()
        if (-not $a) { return $Default }
        $n = 0
        if ([int]::TryParse($a, [ref]$n) -and $n -ge $Min -and $n -le $Max) { return $n }
        Write-Host "  请输入 $Min - $Max 之间的整数。" -ForegroundColor Yellow
    }
}

# ============================================================
# 环境自检
# ============================================================

# 输出被重定向时没有真实控制台，Clear-Host 会抛「句柄无效」——清屏失败不该中断流程
try { Clear-Host } catch { }
Write-Host ''
Write-Host '   iPhone 照片整理工具' -ForegroundColor Cyan
Write-Host '   Image Organizer 1.1.0' -ForegroundColor DarkGray

if (-not (Test-Path -LiteralPath $Python)) {
    Stop-WithPause "找不到 Python 解释器：$Python`n         预期是 conda 环境 douyin。若路径有变，请修改 launcher.ps1 顶部的 `$Python。"
}
if (-not (Test-Path -LiteralPath $Organizer)) {
    Stop-WithPause "找不到 organizer.py：$Organizer"
}

# 依赖自检：缺 imagehash 则视觉去重不可用，缺 pillow-heif 则对 HEIC 无效
$hasImageHash = $false
$hasHeif = $false
try {
    $probe = & $Python -X utf8 -c 'import importlib.util as u; print(int(u.find_spec("imagehash") is not None), int(u.find_spec("pillow_heif") is not None))' 2>$null
    $parts = ("$probe".Trim() -split '\s+')
    if ($parts.Count -ge 2) {
        $hasImageHash = $parts[0] -eq '1'
        $hasHeif = $parts[1] -eq '1'
    }
} catch { }

# ============================================================
# 运行说明
# ============================================================

Write-Rule '这个工具做什么'
Write-Host ''
Write-Tip '把一堆平铺、混乱的 iPhone 导出照片，按拍摄时间整理成 年/月 目录，'
Write-Tip '并把重复的挑出来单独存放。Live Photo 的 HEIC+MOV、编辑参数 AAE'
Write-Tip '会被识别为一组，始终归到同一个目录。'
Write-Host ''
Write-Host '  ⚠ 文件是「移动」不是「复制」——源目录里的照片会被搬走。' -ForegroundColor Yellow
Write-Tip '  所以下面会先做一次预演，你确认无误后才真正执行。'

Write-Rule '需要你提供三个目录'
Write-Host ''
Write-Kv '源目录' '待整理的照片在哪（会被清空）' 'Gray'
Write-Kv '目标目录' '整理后按 年/月 归档到这里' 'Gray'
Write-Kv '重复目录' '识别出的重复文件放这里，不会直接删除' 'Gray'
Write-Host ''
Write-Tip '路径可以直接从资源管理器拖进来，或粘贴。'

# ============================================================
# 读取上次配置
# ============================================================

$cfg = $null
if (Test-Path -LiteralPath $ConfigFile) {
    try { $cfg = Get-Content -LiteralPath $ConfigFile -Raw -Encoding utf8 | ConvertFrom-Json } catch { $cfg = $null }
}
if ($cfg) {
    Write-Rule '找到上次的配置'
    Write-Host ''
    Write-Kv '源目录' $cfg.Source
    Write-Kv '目标目录' $cfg.Target
    Write-Kv '重复目录' $cfg.Duplicates
    Write-Kv '选项' ("视觉去重={0}  并发={1}  日志={2}" -f $(if ($cfg.Phash) { '开' } else { '关' }), $cfg.Workers, $cfg.LogLevel)
    if (-not (Read-YesNo '沿用这套配置？（选 n 重新填写）' $true)) { $cfg = $null }
}

# ============================================================
# 收集配置
# ============================================================

if (-not $cfg) {
    Write-Rule '① 目录设置'

    $source = Read-Dir '源目录（待整理的照片所在）' $null -MustExist

    $defTarget = Join-Path (Split-Path $source -Parent) 'Organized_Photos'
    $target = Read-Dir '目标目录（按 年/月 归档到这里，不存在会自动创建）' $defTarget

    $defDup = Join-Path (Split-Path $target -Parent) 'Duplicates'
    $duplicates = Read-Dir '重复目录（重复文件挪到这里）' $defDup

    Write-Rule '② 选项'
    Write-Host ''
    Write-Tip '直接回车即采用方括号里的推荐值。'

    if ($hasImageHash) {
        Write-Host ''
        Write-Tip '视觉去重：能识别「同一张图的不同压缩版本」，比如微信发过一轮'
        Write-Tip '再存下来的那种。字节去重抓不到（文件大小不同），需要它。'
        if (-not $hasHeif) {
            Write-Host '  注意：当前环境缺 pillow-heif，视觉去重对 HEIC 无效。' -ForegroundColor Yellow
        }
        $phash = Read-YesNo '启用视觉去重？（会慢一些）' $true
        $threshold = 4
        if ($phash) {
            Write-Host ''
            Write-Tip '相似度阈值：越小越严格。0=几乎一模一样，4=推荐，8=较宽松。'
            $threshold = Read-Int '相似度阈值' 4 0 16
        }
    }
    else {
        Write-Host ''
        Write-Host '  当前环境未装 imagehash，视觉去重不可用（字节去重仍然生效）。' -ForegroundColor Yellow
        $phash = $false
        $threshold = 4
    }

    Write-Host ''
    $cores = [Environment]::ProcessorCount
    $defWorkers = [Math]::Min(8, [Math]::Max(2, $cores - 2))
    Write-Tip "并发数：本机 $cores 个逻辑核。机械硬盘或网络盘建议调到 2，避免 IO 抢占。"
    $workers = Read-Int '并发数' $defWorkers 1 32

    Write-Host ''
    Write-Tip '日志级别：INFO 够用；出问题排查时用 DEBUG（输出会非常多）。'
    $logLevel = if (Read-YesNo '用 DEBUG 级别？' $false) { 'DEBUG' } else { 'INFO' }

    $cfg = [pscustomobject]@{
        Source     = $source
        Target     = $target
        Duplicates = $duplicates
        Phash      = $phash
        Threshold  = $threshold
        Workers    = $workers
        LogLevel   = $logLevel
    }
}

# ============================================================
# 合法性检查
# ============================================================

function Test-Nested([string]$Inner, [string]$Outer) {
    $i = [IO.Path]::GetFullPath($Inner).TrimEnd('\') + '\'
    $o = [IO.Path]::GetFullPath($Outer).TrimEnd('\') + '\'
    return $i.StartsWith($o, [StringComparison]::OrdinalIgnoreCase)
}

$problems = @()
if ([IO.Path]::GetFullPath($cfg.Source).TrimEnd('\') -ieq [IO.Path]::GetFullPath($cfg.Target).TrimEnd('\')) {
    $problems += '源目录和目标目录是同一个。'
}
if ([IO.Path]::GetFullPath($cfg.Target).TrimEnd('\') -ieq [IO.Path]::GetFullPath($cfg.Duplicates).TrimEnd('\')) {
    $problems += '目标目录和重复目录是同一个。'
}
if (Test-Nested $cfg.Source $cfg.Target) {
    $problems += '源目录在目标目录里面，整理逻辑会互相干扰。'
}
if ($problems) {
    Write-Host ''
    foreach ($p in $problems) { Write-Host "  [错误] $p" -ForegroundColor Red }
    Write-Host ''
    Write-Host '  请重新运行本启动器并填写不冲突的路径。' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按回车关闭'
    exit 2
}

# ============================================================
# 运行前预估
# ============================================================

Write-Rule '③ 确认'
Write-Host ''
Write-Host '  正在统计源目录…' -ForegroundColor DarkGray -NoNewline

$files = @(Get-ChildItem -LiteralPath $cfg.Source -Recurse -File -ErrorAction SilentlyContinue |
           Where-Object { $MediaExt -contains $_.Extension.ToLower() })
$totalMB = if ($files.Count) { ($files | Measure-Object Length -Sum).Sum / 1MB } else { 0 }
Write-Host "`r                          `r" -NoNewline

if ($files.Count -eq 0) {
    Write-Host ''
    Write-Host '  源目录里没有找到可处理的照片或视频。' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按回车关闭'
    exit 0
}

$stateFile = Join-Path $cfg.Target '.organizer_state.json'
$resumeNote = '无（首次整理）'
if (Test-Path -LiteralPath $stateFile) {
    try {
        $st = Get-Content -LiteralPath $stateFile -Raw -Encoding utf8 | ConvertFrom-Json
        $resumeNote = "有，上次保存于 $($st.saved_at)"
    } catch { $resumeNote = '有（文件已损坏，将重新开始）' }
}

Write-Host ''
Write-Kv '源目录' $cfg.Source
Write-Kv '目标目录' $cfg.Target
Write-Kv '重复目录' $cfg.Duplicates
Write-Host ''
Write-Kv '待处理' ("{0} 个文件，合计 {1:N1} MB" -f $files.Count, $totalMB) 'Green'
Write-Kv '断点状态' $resumeNote
Write-Kv '视觉去重' $(if ($cfg.Phash) { "开（阈值 $($cfg.Threshold)）" } else { '关' })
Write-Kv '并发数' $cfg.Workers
Write-Kv '日志级别' $cfg.LogLevel

# 组装参数
$argsBase = @(
    '-X', 'utf8', $Organizer,
    '--source', $cfg.Source,
    '--target', $cfg.Target,
    '--duplicates', $cfg.Duplicates,
    '--workers', $cfg.Workers,
    '--log-level', $cfg.LogLevel
)
if ($cfg.Phash) { $argsBase += @('--phash', '--phash-threshold', $cfg.Threshold) }

Write-Host ''
Write-Tip '等效命令：'
Write-Host ("  {0} {1}" -f (Split-Path $Python -Leaf), ($argsBase -join ' ')) -ForegroundColor DarkGray

# ============================================================
# 执行
# ============================================================

function Invoke-Organizer([string[]]$Arguments, [string]$LogPath) {
    $prev = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
    $ErrorActionPreference = 'Continue'
    try {
        & $Python @Arguments 2>&1 | ForEach-Object {
            $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { [string]$_ }
            Write-Host $line
            if ($LogPath) { Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8 }
        }
        return $LASTEXITCODE
    }
    finally {
        $PSNativeCommandUseErrorActionPreference = $prev
        $ErrorActionPreference = 'Stop'
    }
}

Write-Rule '④ 预演（不会移动任何文件）'
Write-Host ''
$null = Invoke-Organizer ($argsBase + '--dry-run') $null

Write-Host ''
Write-Host '  以上是预演结果，此刻磁盘上什么都没变。' -ForegroundColor Green
if (-not (Read-YesNo '确认按上面的分配真正执行？' $false)) {
    Write-Host ''
    Write-Host '  已取消，没有任何文件被移动。' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按回车关闭'
    exit 0
}

# 保存配置供下次复用
try {
    $cfg | ConvertTo-Json | Set-Content -LiteralPath $ConfigFile -Encoding utf8
} catch {
    Write-Host '  （配置保存失败，不影响本次运行）' -ForegroundColor DarkGray
}

$logPath = $null
if (Read-YesNo '把本次运行日志存到 logs/ 目录？' $true) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $logPath = Join-Path $LogDir ('organizer-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date))
}

Write-Rule '⑤ 执行中'
Write-Host ''
if ($logPath) { Write-Tip "日志：$logPath" }
Write-Tip '长跑期间每 30 秒会打印一次心跳；按 Ctrl+C 可随时中断，进度会存盘。'
Write-Host ''

$sw = [Diagnostics.Stopwatch]::StartNew()
$code = Invoke-Organizer $argsBase $logPath
$sw.Stop()

# ============================================================
# 结果
# ============================================================

Write-Rule '完成'
Write-Host ''
Write-Kv '耗时' ('{0:hh\:mm\:ss}' -f $sw.Elapsed)

switch ($code) {
    0   { Write-Host '  [成功] 全部归档完成。' -ForegroundColor Green }
    1   { Write-Host '  [警告] 有个别文件业务失败，请看上方「失败清单」。' -ForegroundColor Yellow }
    2   { Write-Host '  [错误] 出现脚本异常，请看上方「失败清单」排查。' -ForegroundColor Red }
    130 { Write-Host '  [中断] 你取消了运行。进度已存盘，重新运行本启动器即可续跑。' -ForegroundColor Yellow }
    default { Write-Host "  [未知] 退出码 $code" -ForegroundColor Red }
}

Write-Host ''
Write-Tip "归档结果： $($cfg.Target)"
Write-Tip "重复文件： $($cfg.Duplicates)   ← 确认无误后可自行删除"
if ($logPath) { Write-Tip "本次日志： $logPath" }
Write-Host ''
Read-Host '  按回车关闭'
exit $code
