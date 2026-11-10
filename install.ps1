# sqlweb_offline_common 安装脚本（Windows PowerShell）
# 用法: .\install.ps1 [-InstallDeps] [-Port 8899] [-BindHost 0.0.0.0] [-NoStart] [-Check]

param(
    [switch]$InstallDeps,
    [switch]$NoStart,
    [switch]$Check,
    [int]$Port = 0,
    [string]$BindHost = ""
)

$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Dir

Write-Host "================================================"
Write-Host " sqlweb_offline_common 安装 (Windows)"
Write-Host "================================================"
Write-Host "目录: $Dir"

# ---- 1. 找 Python ----
# 依次尝试 PATH 命令、py 启动器、常见安装目录；每个候选都实际执行探测。
# 注意：Windows 的 python 可能只是"应用执行别名"（占位），Path/Source 为空或位于
# WindowsApps，直接调用会弹应用商店，所以必须试跑通过才采用。
$py = $null
$ver = ""
$cands = @()
# 已经用 -InstallDeps 建过 .venv 时，优先用它，避免"装好了却检测不到"
$venvPy = Join-Path $Dir ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPy) { $cands += $venvPy }
if ($env:SQLWEB_PYTHON) { $cands += $env:SQLWEB_PYTHON }
foreach ($c in @("python3", "python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
        $p = $cmd.Path
        if (-not $p) { $p = $cmd.Source }
        if (-not $p) { $p = $cmd.Definition }
        if ($p) { $cands += $p }
    }
}
$cands += @(
    "D:\Python313\python.exe", "D:\Python312\python.exe", "D:\Python311\python.exe",
    "D:\Python310\python.exe", "D:\Python39\python.exe", "D:\Python38\python.exe",
    "C:\Python313\python.exe", "C:\Python312\python.exe", "C:\Python311\python.exe",
    "C:\Python310\python.exe", "C:\Python39\python.exe", "C:\Python38\python.exe"
)
foreach ($v in @("313","312","311","310","39","38")) {
    $cands += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe")
}

foreach ($cand in $cands) {
    if ([string]::IsNullOrWhiteSpace($cand)) { continue }
    if ($cand -like "*WindowsApps*") { continue }
    if (-not (Test-Path -LiteralPath $cand)) { continue }
    try {
        $probe = & $cand -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$probe" -match '^\d+\.\d+\.\d+') {
            $py = $cand
            $ver = "$probe".Trim()
            break
        }
    } catch {}
}

if (-not $py) {
    Write-Host ""
    Write-Host "[FAIL] 找不到可用的 python"
    Write-Host "       请安装 Python 3.8+ 并在安装时勾选 Add Python to PATH"
    Write-Host "       也可显式指定：`$env:SQLWEB_PYTHON = 'D:\Python310\python.exe'"
    exit 1
}

$ok = & $py -c "import sys; print(1 if sys.version_info[:2] >= (3,8) else 0)"
Write-Host "Python: $ver  ($py)"
if ($ok -ne "1") {
    Write-Host ""
    Write-Host "[FAIL] 需要 Python 3.8 及以上，当前 $ver"
    exit 1
}
Write-Host "  [ok] Python 版本满足要求 (>=3.8)"

# ---- 2. 文件完整性 ----
foreach ($f in @("sqlweb.py","drivers.py","auth.py","captcha.py","redis_panel.py","index.html","login.html")) {
    if (-not (Test-Path $f)) { Write-Host "  [FAIL] 缺少文件 $f"; exit 1 }
}
Write-Host "  [ok] 程序文件齐全"

# ---- 3. 驱动探测 ----
Write-Host ""
Write-Host "驱动可用性:"
& $py -c @'
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-12s %-16s %s' % (d['name'], d['label'], d['info']))
'@

# ---- 4. 离线装驱动 ----
if ($InstallDeps) {
    Write-Host ""
    Write-Host "离线安装 wheels 内的驱动 (PyMySQL, pg8000, dm-pure)..."
    if (-not (Test-Path "wheels")) { Write-Host "  [FAIL] 没有 wheels 目录"; exit 1 }
    # --system-site-packages：让 .venv 继承系统 Python 里已经装好的驱动（psycopg2/pymssql/
    # oracledb 等）。否则建出来的是个空环境，start.bat 切过去之后这些驱动会全部"变缺失"。
    if (-not (Test-Path ".venv")) { & $py -m venv --system-site-packages .venv }
    $vpy = Join-Path $Dir ".venv\Scripts\python.exe"
    & $vpy -m pip install --no-index --find-links=wheels PyMySQL pg8000 dm-pure
    if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] 离线安装失败"; exit 1 }
    Write-Host "  [ok] 已装入 .venv"
    Write-Host ""
    Write-Host "安装后驱动可用性:"
    & $vpy -c @'
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-12s %-16s %s' % (d['name'], d['label'], d['info']))
'@
}

# ---- 5. 配置 ----
Write-Host ""
if (-not (Test-Path "config.json")) {
    Copy-Item config.example.json config.json
    Write-Host "  [ok] 已由 config.example.json 生成 config.json"
} else {
    Write-Host "  [ok] 已有 config.json，保持不变"
}

if ($Port -gt 0 -or $BindHost -ne "") {
    & $py -c @"
import json, sys, os
port, host = '$Port', '$BindHost'
with open('config.json', encoding='utf-8') as f:
    cfg = json.load(f)
if port and int(port) > 0:
    cfg['port'] = int(port)
if host:
    cfg['host'] = host
tmp = 'config.json.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.replace(tmp, 'config.json')
print('  [ok] 已更新监听为 %s:%s' % (cfg.get('host'), cfg.get('port')))
"@
}

if ($Check) {
    Write-Host ""
    Write-Host "环境检查完成（未启动服务）"
    exit 0
}

# ---- 6. 运行时 Python ----
$vpy = Join-Path $Dir ".venv\Scripts\python.exe"
$runtime = $py
if (Test-Path $vpy) { $runtime = $vpy }

if ($NoStart) {
    Write-Host ""
    Write-Host "准备完成。启动: .\start.bat"
    exit 0
}

Write-Host ""
Write-Host "================================================"
Write-Host "启动服务（Ctrl+C 退出）"
Write-Host "================================================"
& $runtime sqlweb.py
