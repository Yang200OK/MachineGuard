# 管理当前项目安装的 PostgreSQL 后台进程，不操作其它实例。
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Operation
)

$machineguardRoot = Split-Path -Parent $PSScriptRoot
$machineguardBinary = Join-Path $machineguardRoot 'local\pgsql\bin\pg_ctl.exe'
$machineguardData = Join-Path $machineguardRoot 'local\postgres-data'
$machineguardLog = Join-Path $machineguardRoot 'local\postgres.log'
if (!(Test-Path -LiteralPath $machineguardBinary) -or !(Test-Path -LiteralPath $machineguardData)) {
    throw 'PostgreSQL 尚未安装，请先运行 scripts/install_postgres.py。'
}
if ($Operation -eq 'start') {
    & $machineguardBinary status -D $machineguardData | Out-Null
    $machineguardStatus = $LASTEXITCODE
    if ($machineguardStatus -eq 0) {
        Write-Output '当前项目 PostgreSQL 已运行。'
        exit 0
    }
    if ($machineguardStatus -ne 3) { throw '数据库状态检查失败，拒绝继续启动。' }
    & $machineguardBinary start -D $machineguardData -l $machineguardLog -w
} elseif ($Operation -eq 'stop') {
    & $machineguardBinary stop -D $machineguardData -m fast -w
} else {
    & $machineguardBinary status -D $machineguardData
}
exit $LASTEXITCODE
