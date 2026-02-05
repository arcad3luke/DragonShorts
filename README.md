![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.10+-blue)

# DragonShorts

A small Windows utility that scans your Steam libraries, finds installed games, pulls metadata, grabs icons, and builds clean desktop shortcuts. It also sorts games into folders (Co-op, Multiplayer, Singleplayer, Unsorted) and creates a Favorites folder based on your most recently played titles.

This started as a personal quality-of-life tool and grew into something genuinely useful, so I’m sharing it here.

## What it does
- Detects all Steam library folders  
- Reads each game’s appmanifest files  
- Finds the correct executable  
- Resolves icons (renames Steam’s hashed icons or downloads header images)  
- Creates `.lnk` shortcuts with proper working directories and icons  
- Sorts shortcuts into category folders  
- Cleans up duplicates  
- Builds a Favorites folder (top 10 most recently played games)  
- Shows a simple splash screen with progress updates  

## How to use it

### Option 1: Download the executable
Grab the latest release from the Releases page and run it. No installation required.

### Option 2: Run from source
Requires Python 3.10+.

```bash
pip install -r requirements.txt
python steam_icon_grabber.py
```

## Building the executable
Run this inside your virtual environment:

```bash
pyinstaller --onefile --noconsole ^
    --icon=launcher.ico ^
    --add-data "launcher.ico;." ^
    --hidden-import=vdf --hidden-import=vdf.vdf ^
    steam_icon_grabber.py
```

The finished executable will be in `dist/`.

## Requirements
- Windows 10 or 11  
- Steam installed  
- Python 3.10+ (only if running from source)

Python modules:
- vdf  
- requests  
- pywin32  

## Project structure
```
steam_icon_grabber.py
launcher.ico
README.md
requirements.txt
LICENSE
```

## Notes
- Only supports Windows because it relies on `.lnk` shortcuts.  
- Some games don’t expose category metadata, so they fall into “Unsorted.”  
- If Steam changes their manifest format, this may need updates.  

## Contributing
If you want to improve sorting rules, add new categories, or clean up the code, feel free to open a PR or issue.

## License

MIT License.
