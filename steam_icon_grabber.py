## Steam Icon Grabber
#  This program will search all drives for steam libraries,
#  extract the shortcut for the application, creating one if none exist,
#  create folders on the desktop,
#  based on single/multiplayer by looking up the game on steam,
#  and automatically sort the games into their respective folder

import os
import sys
import re
import vdf
import requests
import json
import shutil
import tkinter as tk
from win32com.client import Dispatch
from tkinter import ttk

def resource_path(relative):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)
    return relative

class Splash:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Updating Game Shortcuts")
        self.root.geometry("420x140")
        self.root.resizable(False, False)
        self.root.iconbitmap(resource_path("launcher.ico"))
        # Optional: remove window border for a true splash look
        # self.root.overrideredirect(True)

        self.label = tk.Label(self.root, text="Starting...", font=("Segoe UI", 12))
        self.label.pack(pady=10)

        self.progress = ttk.Progressbar(self.root, length=380, mode="determinate")
        self.progress.pack(pady=10)

        self.root.update()

    def update(self, text, value):
        self.label.config(text=text)
        self.progress["value"] = value
        self.root.update()

    def close(self):
        self.root.destroy()

class SteamIconGrabber:
    def __init__(self):
        self.libraries = []
        self.games = []

        # Expand user profile properly
        self.outputDir = os.path.expandvars(r'%USERPROFILE%\OneDrive\Desktop\\')

        # Path to libraryfolders.vdf (Steam's canonical location)
        self.gameDirFile = r'C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf'

        # Derive Steam root dynamically from libraryfolders.vdf
        if os.path.exists(self.gameDirFile):
            with open(self.gameDirFile) as f:
                libFile = vdf.load(f)
                first = libFile['libraryfolders']['0']['path']
                self.steamRoot = first
        else:
            # Fallback if Steam is installed somewhere unusual
            self.steamRoot = r'C:\Program Files (x86)\Steam\\'

        # Build icon directory cleanly
        self.steamIconRoot = os.path.join(self.steamRoot, 'steam', 'games')

        # Ensure output directory exists
        os.makedirs(self.outputDir, exist_ok=True)

    def dirFinder(self):
        with open(self.gameDirFile) as f:
            libFile = vdf.load(f)
            for key in libFile['libraryfolders']:
                path = libFile['libraryfolders'][key]['path']
                self.libraries.append(path)
        print("Libraries found:", self.libraries)

    def gameScanner(self):
        for library in self.libraries:
            steamapps = os.path.join(library, "steamapps")

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
                            "library": library,
                            # ⭐ Add LastPlayed here
                            "last_played": int(manifest.get("LastPlayed", "0"))
                        }

                        self.games.append(game)

    def launchFetcher(self):
        icon_dir = os.path.join(self.steamRoot, 'steam', 'games')

        for game in self.games:
            appid = game['appid']
            installdir = game['installdir']
            library = game['library']

            game_path = os.path.join(library, 'steamapps', 'common', installdir)
            game.setdefault('exe', None)

            # Find EXE
            if os.path.exists(game_path):
                for root, dirs, files in os.walk(game_path):
                    for file in files:
                        if file.endswith('.exe'):
                            game['exe'] = os.path.join(root, file)
                            break
                    if game['exe'] is not None:
                        break
            else:
                print("Game path missing:", game_path)

            # Find icon (best-effort)
            icon_path = None
            if os.path.exists(icon_dir):
                for file in os.listdir(icon_dir):
                    if file.endswith('.ico') and installdir.lower() in file.lower():
                        icon_path = os.path.join(icon_dir, file)
                        break

            game['icon'] = icon_path

    def iconResolver(self):
        icon_dir = os.path.join(self.steamRoot, "steam", "games")
        base_dir = os.path.abspath(icon_dir)

        for game in self.games:
            appid = str(game["appid"])
            name = game.get("name", appid)
            safe_name = self.sanitize_filename(name)

            # Fetch metadata
            url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
            try:
                r = requests.get(url, timeout=5)
                data = r.json()
            except Exception as e:
                print(f"[ERROR] Metadata fetch failed for {appid}: {e}")
                continue

            # Validate metadata
            entry = data.get(appid, {})
            if not entry.get("success"):
                print(f"[WARN] No metadata for {appid}")
                continue

            appdata = entry.get("data", {})
            header_url = appdata.get("header_image")
            if not header_url:
                print(f"[WARN] No header image for {appid}")
                continue

            # Extract hash from header image filename
            filename = header_url.split("/")[-1]
            hash_part = filename.split(".")[0]
            hash_filename = f"{hash_part}.ico"
            local_hashed_icon = os.path.join(icon_dir, hash_filename)

            # If hashed icon exists locally, rename it
            if os.path.exists(local_hashed_icon):
                readable_name = f"{appid}_{safe_name}.ico"
                readable_path = os.path.join(icon_dir, readable_name)
                target = os.path.abspath(readable_path)

                if not target.startswith(base_dir):
                    print(f"[SECURITY] Blocked unsafe rename for {appid}")
                    continue

                try:
                    os.rename(local_hashed_icon, readable_path)
                    game["icon"] = readable_path
                except Exception as e:
                    print(f"[ERROR] Rename failed for {appid}: {e}")
                    game["icon"] = local_hashed_icon

                continue  # Done with this game

            # No local icon → download header image
            try:
                img = requests.get(header_url, timeout=5)
                readable_name = f"{appid}_{safe_name}.jpg"
                readable_path = os.path.join(icon_dir, readable_name)
                target = os.path.abspath(readable_path)

                if not target.startswith(base_dir):
                    print(f"[SECURITY] Blocked unsafe write for {appid}")
                    continue

                with open(readable_path, "wb") as f:
                    f.write(img.content)

                game["icon"] = readable_path

            except Exception as e:
                print(f"[ERROR] Download failed for {appid}: {e}")
                game["icon"] = None
    def sanitize_filename(self, name):
        # Remove forbidden characters
        cleaned = re.sub(r'[\\/:*?"<>|]', '_', name)

        # Strip whitespace and trailing periods
        cleaned = cleaned.strip().rstrip('.')

        # Avoid Windows reserved device names
        reserved = {
            'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{i}' for i in range(1, 10)),
            *(f'LPT{i}' for i in range(1, 10)),
        }

        if cleaned.upper() in reserved:
            cleaned = f'_{cleaned}_'

        return cleaned

    def gameShortcut(self, game):
        event_errors = []
        if not game['exe']:
            event_errors.append(
                f'Missing Executable: {game["exe"]}\n'
                f'With icon: {game["icon"]}\n'
                f'For game: {game["name"]}'
            )
            return None

        safe_name = f'{game["appid"]}_{self.sanitize_filename(game["name"])}'
        shortcut_name = f'{safe_name}.lnk'
        shortcut_path = os.path.join(self.outputDir, shortcut_name)


        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortcut(shortcut_path)

        shortcut.TargetPath = game['exe']
        shortcut.WorkingDirectory = os.path.dirname(game['exe'])
        if game['icon']:
            shortcut.IconLocation = game['icon']
        if game.get('args'):
            shortcut.Arguments = game['args']

        shortcut.save()

        return shortcut_path

    def findExistingShortcut(self, appid):
        for root, dirs, files in os.walk(self.outputDir):
            for file in files:
                if file.startswith(str(appid)) and file.endswith(".lnk"):
                    return os.path.join(root, file)
        return None

    def gameSort(self, data, appid, shortcut):
        entry = data.get(str(appid), {})
        if not entry.get("success"):
            return

        appdata = entry.get("data", {})
        categories = appdata.get("categories", [])
        descriptions = [c.get("description", "").lower() for c in categories]

        # Priority: Co-op → Multiplayer → Singleplayer → Unsorted
        if any("co-op" in c or "coop" in c or "cooperative" in c for c in descriptions):
            folder = "Co-op"
        elif any("multi" in c or "pvp" in c or "online" in c or "lan" in c for c in descriptions):
            folder = "Multiplayer"
        elif any("single" in c for c in descriptions):
            folder = "Singleplayer"
        else:
            folder = "Unsorted"

        dest_dir = os.path.join(self.outputDir, folder)
        os.makedirs(dest_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, os.path.basename(shortcut))
        try:
            shutil.move(shortcut, dest_path)
        except:
            pass

    def cleanupShortcuts(self):
        seen = {}
        removed = []

        for root, dirs, files in os.walk(self.outputDir):
            for file in files:
                if not file.endswith(".lnk"):
                    continue

                parts = file.split("_", 1)
                if not parts[0].isdigit():
                    continue

                appid = parts[0]
                path = os.path.join(root, file)
                mtime = os.path.getmtime(path)

                if appid not in seen:
                    seen[appid] = (path, mtime)
                    continue

                old_path, old_mtime = seen[appid]
                if mtime > old_mtime:
                    removed.append(old_path)
                    seen[appid] = (path, mtime)
                else:
                    removed.append(path)

        for path in removed:
            try:
                os.remove(path)
            except:
                pass

    def buildFavorites(self, limit=5):
        favorites_dir = os.path.join(self.outputDir, "Favorites")
        os.makedirs(favorites_dir, exist_ok=True)

        # Sort by recency
        recent = sorted(
            self.games,
            key=lambda g: g.get("last_played", 0),
            reverse=True
        )

        top = recent[:limit]

        # Clear old favorites
        for file in os.listdir(favorites_dir):
            if file.endswith(".lnk"):
                try:
                    os.remove(os.path.join(favorites_dir, file))
                except:
                    pass

        # Copy shortcuts
        for game in top:
            appid = str(game["appid"])
            shortcut = self.findExistingShortcut(appid)
            if not shortcut:
                continue

            dest = os.path.join(favorites_dir, os.path.basename(shortcut))
            shutil.copy(shortcut, dest)

            ts = game.get("last_played", 0)
            if ts > 0:
                os.utime(dest, (ts, ts))

    def run(self):
        splash = Splash()

        try:
            splash.update("Finding Steam libraries...", 5)
            self.dirFinder()

            splash.update("Scanning for games...", 15)
            self.gameScanner()

            splash.update("Fetching launch data...", 30)
            self.launchFetcher()

            splash.update("Resolving icons...", 45)
            self.iconResolver()

            splash.update("Creating shortcuts and sorting...", 65)
            for game in self.games:
                appid = str(game["appid"])

                existing = self.findExistingShortcut(appid)
                if existing:
                    try:
                        os.remove(existing)
                    except:
                        pass

                shortcut = self.gameShortcut(game)
                if not shortcut:
                    continue

                url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
                try:
                    r = requests.get(url, timeout=5)
                    data = r.json()
                except:
                    continue

                try:
                    self.gameSort(data, appid, shortcut)
                except:
                    pass

            splash.update("Cleaning up duplicates...", 85)
            self.cleanupShortcuts()

            splash.update("Updating favorites...", 95)
            self.buildFavorites()

            splash.update("Done!", 100)

        except Exception as e:
            with open("error_log.txt", "a", encoding="utf-8") as log:
                log.write(f"ERROR: {e}\n")

        finally:
            splash.close()

if __name__ == '__main__':
    SteamIconGrabber().run()