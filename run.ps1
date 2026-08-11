$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "未找到 .venv。请先按 README.md 创建虚拟环境并安装依赖。"
}

& ".venv\Scripts\python.exe" -m app.main

