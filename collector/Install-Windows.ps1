$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$guiPath = Join-Path $PSScriptRoot 'VionCollectorGUI.exe'
if (-not (Test-Path -LiteralPath $guiPath)) { throw 'Распакуйте весь Windows-архив: рядом должен быть VionCollectorGUI.exe.' }
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'VionCollector.exe'))) { throw 'Не найден VionCollector.exe. Распакуйте весь архив.' }
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopPath 'Vion Collector.lnk'
if (-not (Test-Path -LiteralPath $shortcutPath)) {
    $shellLink = New-Object -ComObject WScript.Shell
    $shortcut = $shellLink.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $guiPath
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Save()
}
Start-Process -FilePath $guiPath -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
