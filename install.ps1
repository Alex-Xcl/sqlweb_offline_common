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
$py = $null
foreach ($c in @("python", "python3", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) {
    Write-Host ""
    Write-Host "[FAIL] 找不到 python"
    Write-Host "       请安装 Python 3.8+ 并在安装时勾选 Add Python to PATH"
    exit 1
}

$ver = & $py -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
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
& $py -c @"
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-12s %-16s %s' % (d['name'], d['label'], d['info']))
"@

# ---- 4. 离线装驱动 ----
if ($InstallDeps) {
    Write-Host ""
    Write-Host "离线安装 wheels 内的驱动 (PyMySQL, pg8000, dm-pure)..."
    if (-not (Test-Path "wheels")) { Write-Host "  [FAIL] 没有 wheels 目录"; exit 1 }
    if (-not (Test-Path ".venv")) { & $py -m venv .venv }
    $vpy = Join-Path $Dir ".venv\Scripts\python.exe"
    & $vpy -m pip install --no-index --find-links=wheels PyMySQL pg8000 dm-pure
    if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] 离线安装失败"; exit 1 }
    Write-Host "  [ok] 已装入 .venv"
    Write-Host ""
    Write-Host "安装后驱动可用性:"
    & $vpy -c @"
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-12s %-16s %s' % (d['name'], d['label'], d['info']))
"@
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
