# Start one LED and one temperature simulator, each in its own window so the
# per-device output stays readable (and screenshottable).
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'

Start-Process -FilePath 'python' `
    -ArgumentList '-m', 'simulations.simulated_led_device' `
    -WorkingDirectory $root

Start-Process -FilePath 'python' `
    -ArgumentList '-m', 'simulations.simulated_temp_device' `
    -WorkingDirectory $root

Write-Host 'Started led1 and temp1 in separate windows. Close those windows to stop them.'
