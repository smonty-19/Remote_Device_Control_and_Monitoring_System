# Start the controller. The operator CLI runs inside this process.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m controller.server.server @args
