import os
import random
import subprocess
import sqlite3 as sqlite
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import *
from tkinter import messagebox
from tkinter import ttk

from xplatform import XPlatform


class GamePicker:
    def __init__(self):
        self.driveList = self.DriveScanner()
        self.masterGameList = []
        self.platformDefaultWindowsPaths = {
            'steam': {
                "Steam": r'Program Files (x86)/Steam/'
            },

            'battle.net': {
                "Blizzard Games": r''
            },

            'epic games': {
                "Epic Games": r'Epic Games/'
            },

            'ubisoft': {
                "Ubisoft Games": r'Ubisoft/Ubisoft Game Launcher/games/'
            },

            'xbox': {
                "Xbox Games": r'XboxGames/'
            }
        }

    def DriveScanner(self):
        drives = [
            f'{chr(letter)}:/'
            for letter in range(ord('a'), ord('z') + 1)
            if os.path.exists(f'{chr(letter)}:/')
        ]
        return drives

    def _runScanner(self, platform):
        if platform == 'steam':
            from scanners.steam import scanForGames
            root, libraries, games = scanForGames()
            return games
        else:
            return XPlatform(
            platform=platform,
            driveList=self.driveList,
            defaultPaths=self.platformDefaultWindowsPaths[platform]
        ).gameFinder()

    def findExe(self, folder):
        candidates = []

        for root, dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".exe"):
                    candidates.append(os.path.join(root, f))

        if not candidates:
            return None

        # Hard blacklist — never launch these
        blacklist = [
            "crash", "report", "bug", "updater", "launcher",
            "helper", "telemetry", "anti", "cheat", "bssndrpt",
            "unitycrash", "unrealcrash", "setup", "install"
        ]

        filtered = [
            c for c in candidates
            if not any(b in os.path.basename(c).lower() for b in blacklist)
        ]

        if not filtered:
            filtered = candidates

        # Strong preference for real game binaries
        preferred_keywords = [
            "shipping", "win64", "win32", "game", "client"
        ]

        for kw in preferred_keywords:
            for c in filtered:
                if kw in os.path.basename(c).lower():
                    return c

        # Fallback: largest EXE is usually the real game
        filtered.sort(key=lambda p: os.path.getsize(p), reverse=True)
        return filtered[0]

    def runAllScanners(self, platform):
        platformList = ['steam', 'battle.net', 'epic games', 'ubisoft', 'xbox']
        results = []

        with ThreadPoolExecutor(max_workers=len(platform)) as executor:
            futureMap = {
                executor.submit(self._runScanner,p): p
                for p in platformList
            }
            for future in as_completed(futureMap):
                platform = futureMap[future]
                try:
                    result = future.result()

                    if not result:
                        continue

                    if platform != 'steam':



                        SYSTEM_BLACKLIST = [
                            "$getcurrent",
                            "$windows.~bt",
                            "$windows.~ws",
                            "$sysreset",
                            "windows",
                            "program files",
                            "program files (x86)",
                            "programdata",
                            "recovery",
                            "system volume information",
                            "users",
                            "onedrive",
                            "appdata",
                            "amd",
                            "nvidia",
                            "intel",
                            "driver",
                            "setup",
                            "installer",
                            "riot client",
                            "riotclient",
                            "vanguard",
                            "vgc",
                            "riot games\\riot client"
                        ]

                        def is_system_folder(path):
                            lower = path.lower()
                            return any(b in lower for b in SYSTEM_BLACKLIST)

                        GAME_KEYWORDS = ["game", "win64", "win32", "shipping", "binaries", "content"]

                        def looks_like_game_folder(path):
                            for root, dirs, files in os.walk(path):
                                # must contain an exe
                                if not any(f.lower().endswith(".exe") for f in files):
                                    continue

                                # must contain at least one game-like folder or file
                                if any(k in root.lower() for k in GAME_KEYWORDS):
                                    return True

                            return False

                        # FILTER FIRST
                        filtered = [
                            g for g in result
                            if not is_system_folder(g["path"]) and looks_like_game_folder(g["path"])
                        ]

                        # THEN build entries
                        result = [
                            {
                                "appid": f"{platform}:{g['name']}",
                                "name": g["name"],
                                "installdir": os.path.basename(g["path"]),
                                "library": g["path"],
                                "last_played": 0,
                                "exe": None if platform == "xbox" else self.findExe(g['path']),
                                "icon": None,
                                "favorite": False
                            }
                            for g in filtered
                        ]
                        for g in self.masterGameList:
                            print(g["name"])
                    elif platform == "xbox":
                        XBOX_JUNK = [
                            "stub", "tracker", "pack", "dlc", "bundle", "addon",
                            "content", "po", "gp", "gamepass", "early access"
                        ]
                        name_lower = g["name"].lower()
                        if any(j in name_lower for j in XBOX_JUNK):
                            continue
                    else:
                        for g in result:
                           g['favorite'] = False

                    results.extend(result)
                except Exception as sc:
                    print(f'{platform} scanner failed: {sc}')
            return results

    def toggleFavorite(self, appid):
        for game in self.masterGameList:
            if game["appid"] == appid:
                pass

    def randomGame(self):
        # Only pick games that actually have an executable
        validGames = [g for g in self.masterGameList if g.get("exe")]

        if not validGames:
            messagebox.showerror("Error", "No games with valid executables found.")
            return None

        # Pick a random valid game
        game = random.choice(validGames)

        exe = game["exe"]

        # Sanity check
        if not exe or not os.path.isfile(exe):
            messagebox.showerror("Error", f"{game['name']} has no valid executable.")
            return None

        return game

    def launch_game(self, game):
        try:
            appid = str(game["appid"])

            # Steam games
            if appid.isdigit():
                subprocess.Popen([f"steam://rungameid/{appid}"])
                return

            # Non‑Steam games
            exe = game.get("exe")

            if not exe or not os.path.isfile(exe):
                messagebox.showerror("Error", f"{game['name']} has no valid executable.")
                return

            subprocess.Popen([exe], cwd=os.path.dirname(exe))

        except Exception as e:
            print(f"Error launching the game: {e}")

    def UI(self):
        ui = Tk()
        ui.title('DragonShorts')

        frame = ttk.Frame(ui, padding=10)
        frame.grid()

        # ttk.Label(frame, text="DragonShorts").grid(column=0, row=0, columnspan=2)

        self.results = ttk.Label(frame, text='')
        self.results.grid(column=0, row=2, columnspan=2)

        # def initDB():
        #     cursor = sqlite.Cursor()




        def scan_games():
            self.results.config(text='Scanning...')
            ui.update_idletasks()

            platforms = ['steam', 'battle.net', 'epic games','ubisoft','xbox']
            self.masterGameList = self.runAllScanners(platforms)

            # root, libraries, games = scanForGames()
            # grabber = SteamIconGrabber(root, libraries, games)

            self.results.config(text=f'Found {len(self.masterGameList)} games')

        def random_launch():
            game = self.randomGame()

            if game:
                self.results.config(text=f"Selected: {game['name']}")
                self.launch_game(game)
            else:
                self.results.config(text='No Games Found!')

        def faveTracker(game):
            if GamePicker.toggleFavorite(self):
                game['favorites'] = True
                return game



        if faveTracker:
            ttk.Label(frame, text="Favorite Games")

            favorites = ttk.Frame(frame, padding=10)

            ttk.Menubutton(favorites, name='favorites', padding=10)
        else:
            return None

        ttk.Button(frame, text='Scan Games', command=scan_games).grid(column = 0, row = 1)

        ttk.Button(frame, text="Pick a random game", command=random_launch).grid(column=0, row=3)

        ttk.Button(frame, text="Quit", command=ui.destroy).grid(column=1, row=3)

        ui.mainloop()

if __name__ == "__main__":
    picker = GamePicker()
    picker.UI()