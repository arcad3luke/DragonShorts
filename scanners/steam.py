import os
import vdf


def steamRoot():
    possible = [
        r'C:\Program Files (x86)\Steam',
        r'C:\Program Files'
    ]

    for path in possible:
        if os.path.exists(path):
            return path
    return None

def dirFinder(steam_root):
    libraryFile = os.path.join(steam_root, 'steamapps', 'libraryfolders.vdf')
    if not os.path.exists(libraryFile):
        return []
    with open(libraryFile, encoding='utf-8') as f:
        data = vdf.load(f)
    libraries = []
    folders = data.get('libraryfolders',{})
    for key, entry in folders.items():
        if isinstance(entry, dict):
            path = entry.get('path')
        else:
            path = entry

        if path:
            libraries.append(os.path.join(path, 'steamapps'))

    return libraries

def gameScanner(libraries):
    games = []
    for steamapps in libraries:

        for dirpath, dirnames, filenames in os.walk(steamapps):
            for filename in filenames:
                if filename.startswith("appmanifest_") and filename.endswith(".acf"):
                    full_path = os.path.join(dirpath, filename)

                    try:
                        with open(full_path, "r", encoding="utf-8") as m:
                            manifest = vdf.load(m).get("AppState", {})
                    except Exception as e:
                        print(f"[ERROR] Failed to read manifest {full_path}: {e}")
                        continue

                    # Build game entry
                    game = {
                        "appid": manifest.get("appid"),
                        "name": manifest.get("name", str(manifest.get("appid"))),
                        "installdir": manifest.get("installdir"),
                        "library": steamapps,
                        "last_played": int(manifest.get("LastPlayed", "0"))
                    }

                    games.append(game)

    return games

def scanForGames():
    root = steamRoot()
    libraries = dirFinder(root)
    games = gameScanner(libraries)
    return root, libraries, games