![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.10+-blue)

# DragonShorts

DragonShorts is a Windows utility that scans installed games across major launchers and helps pick something to play.

## Features

- Scans:
  - Steam
  - Battle.net
  - Epic Games
  - Ubisoft
  - Xbox
- Platform-specific detection where available
- Filesystem fallback scanning
- Executable resolution for non-Steam titles
- UI progress:
  - Per-platform status
  - Overall progress bar
- Cross-scanner deduplication
- Random game picker
- Launch support:
  - Steam via `steam://rungameid/...`
  - Non-Steam via resolved `.exe`

## Platform Notes

### Steam
- Uses Steam scanner module when available
- Falls back to filesystem scanning when needed

### Battle.net
- Uses `product.db` discovery
- Falls back to registry install discovery
- Resolves executable candidates from install folders

### Epic Games
- Reads manifests from:
  - `C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests`
  - `C:\ProgramData\Epic\UnrealEngineLauncher\Data\Manifests`
- Falls back to filesystem scanning when needed

### Ubisoft / Xbox
- Uses filesystem/path-based scanning

## Requirements

- Windows 10/11
- Python 3.10+ (source run only)

## Run From Source

```powershell
pip install -r requirements.txt
python DragonShorts.py
```

## Build (PyInstaller, onefile)

```powershell
cd "C:\Users\arcad\OneDrive\Documents\code\DragonShortsGH"
Remove-Item .\build,.\dist -Recurse -Force -ErrorAction SilentlyContinue
pyinstaller --noconfirm --clean --onefile --noconsole --name DragonShorts `
  --paths "DragonShortsGH" `
  --hidden-import "scanners" `
  --hidden-import "scanners.steam" `
  --hidden-import "scanners.epic" `
  --hidden-import "scanners.battlenet" `
  --add-data "scanners;scanners" `
  DragonShorts.py
```

## Logging

- Debug scan logging is written to:
  - `%TEMP%\DragonShorts_scan.log`
- For release builds, disable logging in code:
  - `LOG_ENABLED = False`
