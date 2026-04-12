import os
import sys
import queue
import random
import sqlite3 as sqlite
import subprocess
import threading
import contextlib
import io
import time as t
import tempfile
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from tkinter import *
from tkinter import messagebox
from tkinter import ttk

# ── frozen path fix (must be before scanner imports) ─────────────────────────
if getattr(sys, "frozen", False):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))

if _base not in sys.path:
    sys.path.insert(0, _base)

# ── static scanner imports ────────────────────────────────────────────────────
try:
    from scanners.steam import scanForGames as steam_scan_for_games
except Exception:
    steam_scan_for_games = None

try:
    from scanners.epic import scanForGames as epic_scan_for_games
except Exception:
    epic_scan_for_games = None

try:
    from scanners.battlenet import BattleNetScanner
except Exception:
    BattleNetScanner = None


# ── constants ────────────────────────────────────────────────────────────────

PLATFORM_ORDER = ["steam", "battle.net", "epic games", "ubisoft", "xbox"]

# Force strict one-platform-at-a-time behavior
SCAN_MAX_WORKERS = 1
SCAN_UI_QUEUE_MAX = 1000
SCAN_HARD_CAP_SECONDS = 180
LOG_ENABLED = False
LOG_FILE = os.path.join(tempfile.gettempdir(), "DragonShorts_scan.log")

PLATFORM_LABELS = {
    "steam":      "Steam",
    "battle.net": "Battle.net",
    "epic games": "Epic Games",
    "ubisoft":    "Ubisoft",
    "xbox":       "Xbox",
}

SCANNER_TIMEOUT_BY_PLATFORM = {
    # idle timeout seconds (not wall-clock runtime)
    "steam":      60,
    "battle.net": 120,
    "epic games": 90,
    "ubisoft":    90,
    "xbox":       120,
}


PLATFORM_ROOT_MAP = {
    "steam": [
        r"Program Files (x86)\Steam\steamapps\common",
        r"Program Files\Steam\steamapps\common",
        r"SteamLibrary\steamapps\common",
        r"Games\SteamLibrary\steamapps\common",
    ],
    "epic games": [
        r"Program Files\Epic Games",
        r"Program Files (x86)\Epic Games",
        r"Epic Games",
        r"EpicGames",
        r"Games\Epic Games",
    ],
    "ubisoft": [
        r"Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games",
        r"Program Files\Ubisoft\Ubisoft Game Launcher\games",
        r"Ubisoft\Ubisoft Game Launcher\games",
    ],
    "xbox": [
        r"XboxGames",
        r"Program Files\ModifiableWindowsApps",
    ],
}

EXE_BLACKLIST = {
    "crash", "report", "bug", "updater", "helper",
    "telemetry", "anti", "cheat", "bssndrpt",
    "unitycrash", "unrealcrash", "setup", "install",
}

# paths that are never game folders
SYSTEM_BLACKLIST = {
    "$getcurrent", "$windows.~bt", "$windows.~ws", "$sysreset",
    "windows", "programdata", "recovery", "system volume information",
}

XBOX_JUNK = {
    "stub", "tracker", "pack", "bundle", "addon",
    "content", "gamepass",
}

PREFERRED_EXE_KEYWORDS = ("shipping", "win64", "win32", "game", "client")

WALK_SKIP_DIRS = {
    "__pycache__", ".git", ".vs", "redistributables",
    "redist", "_commonredist", "support", "cache", "logs",
    "crashreports", "telemetry",
}

# scanners that already resolved their exe — skip re-validation
TRUSTED_PLATFORMS = {"steam", "battle.net", "epic games"}


# ── progress helpers ──────────────────────────────────────────────────────────

class ScanProgressReporter:
    def __init__(self, platform, callback=None):
        self.platform = platform
        self.callback = callback

    def _emit(self, kind, message, current=None, total=None):
        if self.callback:
            self.callback(kind, self.platform, message, current, total)

    def start_phase(self, label):
        self._emit("detail", label)

    def update_spinner(self, label, detail="", interval=0.1):
        self._emit("detail", f"{label} | {detail}" if detail else label)

    def update_bar(self, label, current, total):
        # was incorrectly emitted as "detail"
        self._emit("platform", label, current, total)

    def finish_phase(self, label, detail="done"):
        self._emit("detail", f"{label} | {detail}")


class ScannerOutputBridge(io.TextIOBase):
    """Redirect scanner stdout/stderr into the progress queue."""

    def __init__(self, platform, callback=None):
        self.platform = platform
        self.callback = callback
        self._buf = ""

    def writable(self): return True

    def write(self, text):
        if not text:
            return 0
        self._buf += text.replace("\r", "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("[bnet] "):
                line = line[7:].strip()
            if self.callback:
                self.callback("detail", self.platform, line, None, None)
        return len(text)

    def flush(self):
        line = self._buf.strip()
        if line and self.callback:
            if line.startswith("[bnet] "):
                line = line[7:].strip()
            self.callback("detail", self.platform, line, None, None)
        self._buf = ""


# ── main class ───────────────────────────────────────────────────────────────

class GamePicker:
    def __init__(self):
        self.driveList         = self._scanDrives()
        self.masterGameList    = []
        self.scanQueue         = queue.Queue(maxsize=SCAN_UI_QUEUE_MAX)
        self.scanActive        = False
        self.scanStartedAt     = None
        self.platformStatusVars = {}
        self.platformOrder     = list(PLATFORM_ORDER)
        self._platformFoundCounts = {p: 0 for p in PLATFORM_ORDER}
        self._lastDetailEmit   = {}
        self._lastDetailMsg    = {}
        self._scanDoneEvent    = threading.Event()
        self._scanResultBuffer = []
        self._activePlatform   = None
        self._log(f"App start | frozen={getattr(sys, 'frozen', False)} | exe={sys.executable}")

    def _log(self, msg):
        if not LOG_ENABLED:
            return
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    # ── drives ────────────────────────────────────────────────────────────────

    def _scanDrives(self):
        return [
            f"{chr(c)}:\\"
            for c in range(ord("A"), ord("Z") + 1)
            if os.path.exists(f"{chr(c)}:\\")
        ]

    # ── progress ──────────────────────────────────────────────────────────────

    def _progressCallback(self, kind, platform, message, current=None, total=None):
        # Ignore late scanner chatter after scan is over
        if not self.scanActive and kind != "done":
            return

        if kind == "detail":
            now = t.time()
            if (now - self._lastDetailEmit.get(platform, 0.0)) < 0.35 \
                    and message == self._lastDetailMsg.get(platform, ""):
                return
            self._lastDetailEmit[platform] = now
            self._lastDetailMsg[platform]  = message

            if self.scanQueue.qsize() > 400:
                return

        try:
            self.scanQueue.put_nowait((kind, platform, message, current, total))
        except queue.Full:
            # keep non-detail updates if possible
            if kind != "detail":
                try:
                    self.scanQueue.put((kind, platform, message, current, total), timeout=0.2)
                except queue.Full:
                    pass

    def _formatEta(self, completed, total):
        if not self.scanStartedAt or completed <= 0 or total <= 0:
            return "estimating..."
        elapsed = t.time() - self.scanStartedAt
        eta = max(0, int((elapsed / completed) * (total - completed)))
        return f"{eta}s" if eta < 60 else f"{eta // 60}m {eta % 60:02d}s"

    # ── filesystem helpers ────────────────────────────────────────────────────

    def _safeScandir(self, path):
        try:
            with os.scandir(path) as it:
                return list(it)
        except (OSError, PermissionError):
            return []

    def _platformLabel(self, platform):
        return PLATFORM_LABELS.get(platform, platform.title())

    def _isSystemFolder(self, path):
        """
        Only treat well-known OS folders as system if they are near drive root.
        Do NOT reject valid game paths under Program Files.
        """
        try:
            norm = os.path.normpath(path).lower()
            parts = [p for p in norm.split(os.sep) if p and not p.endswith(":")]
            if not parts:
                return False

            # root folder like C:\Windows or C:\ProgramData\...
            first = parts[0]
            if first in SYSTEM_BLACKLIST and len(parts) <= 2:
                return True

            return False
        except Exception:
            return False

    def _iterPlatformRoots(self, platform):
        seen = set()
        for drive in self.driveList:
            for rel in PLATFORM_ROOT_MAP.get(platform, ()):
                root = os.path.join(drive, rel)
                norm = os.path.normcase(os.path.normpath(root))
                if norm not in seen and os.path.isdir(root):
                    seen.add(norm)
                    yield root

    def findExe(self, folder, max_depth=6):
        best_filtered = best_filtered_score = None
        best_any      = best_any_score      = None

        try:
            for root, dirs, files in os.walk(folder, topdown=True):
                # prune skip dirs
                dirs[:] = [d for d in dirs if d.lower() not in WALK_SKIP_DIRS]

                rel   = os.path.relpath(root, folder)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > max_depth:
                    dirs.clear()
                    continue

                for filename in files:
                    lower = filename.lower()
                    if not lower.endswith(".exe"):
                        continue

                    full = os.path.join(root, filename)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0

                    kw_rank = next(
                        (len(PREFERRED_EXE_KEYWORDS) - i
                         for i, kw in enumerate(PREFERRED_EXE_KEYWORDS) if kw in lower),
                        0
                    )
                    score = (kw_rank, -depth, size)

                    if best_any_score is None or score > best_any_score:
                        best_any, best_any_score = full, score

                    if any(tok in lower for tok in EXE_BLACKLIST):
                        continue

                    if best_filtered_score is None or score > best_filtered_score:
                        best_filtered, best_filtered_score = full, score

        except (OSError, PermissionError):
            return None

        return best_filtered or best_any

    # ── normalisation ─────────────────────────────────────────────────────────

    def _normalizeScannerResults(self, platform, result):
        if not result:
            return []

        normalized = []

        for g in result:
            name = str(g.get("name", "")).strip()
            exe  = g.get("exe")
            path = g.get("path") or g.get("library")

            if not path and exe:
                path = os.path.dirname(exe)

            if not name or not path:
                continue

            if self._isSystemFolder(path):
                continue

            if platform == "xbox":
                if any(tok in name.lower() for tok in XBOX_JUNK):
                    continue
            elif platform in TRUSTED_PLATFORMS:
                # trust scanner result — do NOT reject based on missing exe
                pass
            else:
                if not exe and not self.findExe(path, max_depth=2):
                    continue

            resolved_exe = None if platform == "xbox" else (exe or self.findExe(path))

            normalized.append({
                "appid":      str(g.get("appid") or f"{platform}:{name}"),
                "name":       name,
                "installdir": os.path.basename(path.rstrip("\\/")),
                "library":    path,
                "path":       path,
                "last_played": g.get("last_played", 0),
                "exe":        resolved_exe,
                "icon":       g.get("icon"),
                "favorite":   bool(g.get("favorite", False)),
            })

        return normalized

    # ── scanners ──────────────────────────────────────────────────────────────

    def _scanPlatformFilesystem(self, platform, reporter):
        roots = list(self._iterPlatformRoots(platform))
        label = self._platformLabel(platform)

        if not roots:
            reporter.finish_phase(f"Scanning {label}", "no roots found")
            return []

        candidates  = []
        seen_paths  = set()

        for root in roots:
            for entry in self._safeScandir(root):
                try:
                    if not entry.is_dir():
                        continue
                except (OSError, PermissionError):
                    continue
                norm = os.path.normcase(os.path.normpath(entry.path))
                if norm not in seen_paths:
                    seen_paths.add(norm)
                    candidates.append(entry.path)

        if not candidates:
            reporter.finish_phase(f"Scanning {label}", "no installs found")
            return []

        results = []
        total   = len(candidates)

        for idx, folder in enumerate(candidates, 1):
            reporter.update_bar(f"Scanning {label}", idx, total)
            exe = None if platform == "xbox" else self.findExe(folder)
            if platform != "xbox" and not exe:
                continue
            results.append({
                "appid":    f"{platform}:{os.path.basename(folder)}",
                "name":     os.path.basename(folder),
                "path":     folder,
                "library":  folder,
                "exe":      exe,
                "favorite": False,
            })

        reporter.finish_phase(f"Scanning {label}", f"{len(results)} game(s)")
        return results

    def _runScanner(self, platform, progress_callback=None):
        reporter = ScanProgressReporter(platform, progress_callback)
        self._log(f"{platform} scanner: start")

        if platform == "steam":
            reporter.start_phase("Scanning Steam")
            if steam_scan_for_games:
                try:
                    _root, _libs, games = steam_scan_for_games()
                    reporter.finish_phase("Scanning Steam", f"{len(games)} game(s)")
                    self._log(f"steam scanner: success ({len(games)} games)")
                    return games
                except Exception:
                    pass
            return self._scanPlatformFilesystem(platform, reporter)

        if platform == "battle.net":
            if BattleNetScanner is None:
                reporter.finish_phase("Scanning Battle.net", "unavailable")
                self._log("battle.net scanner: unavailable (import failed)")
                return []
            reporter.start_phase("Scanning Battle.net")
            try:
                scanner = BattleNetScanner(debug=False, progress=reporter)
                games = scanner.scan()
                reporter.finish_phase("Scanning Battle.net", f"{len(games)} game(s)")
                self._log(f"battle.net scanner: success ({len(games)} games)")
                return games
            except Exception:
                self._log("battle.net scanner: exception\n" + traceback.format_exc())
                return []

        if platform == "epic games":
            reporter.start_phase("Scanning Epic Games")
            if epic_scan_for_games:
                try:
                    games = epic_scan_for_games()
                    reporter.finish_phase("Scanning Epic Games", f"{len(games) if games else 0} game(s)")
                    return games or []
                except Exception:
                    pass
            return self._scanPlatformFilesystem(platform, reporter)

        if platform in {"ubisoft", "xbox"}:
            reporter.start_phase(f"Scanning {self._platformLabel(platform)}")
            return self._scanPlatformFilesystem(platform, reporter)

        reporter.finish_phase(f"Scanning {self._platformLabel(platform)}", "unsupported")
        return []

    def _runScannerWithTimeout(self, platform, progress_callback=None):
        timeout_s = SCANNER_TIMEOUT_BY_PLATFORM.get(platform, 240)
        result = []
        err = None
        done = threading.Event()

        def _target():
            nonlocal result, err
            try:
                result = self._runScanner(platform, progress_callback)
            except Exception as e:
                err = e
            finally:
                done.set()

        th = threading.Thread(target=_target, daemon=True)
        th.start()
        th.join(timeout=timeout_s)

        if not done.is_set():
            self._log(f"{platform}: HARD TIMEOUT after {timeout_s}s")
            if progress_callback:
                progress_callback("detail", platform, f"hard timeout after {timeout_s}s", None, None)
            return []

        if err:
            raise err
        return result

    # ── run all ───────────────────────────────────────────────────────────────

    def runAllScanners(self, platforms, progress_callback=None):
        platform_list = list(platforms)
        results = []
        completed = 0
        total = len(platform_list)
        seen_games = set()

        self._log(f"runAllScanners: start | sequential=True | platforms={platform_list}")

        for platform in platform_list:
            platform_count = 0
            try:
                items = self._normalizeScannerResults(
                    platform,
                    self._runScannerWithTimeout(platform, progress_callback)
                )
                platform_count = len(items)
                self._log(f"{platform}: normalized {platform_count}")

                if progress_callback:
                    progress_callback("count", platform, platform_count, None, None)

                for game in items:
                    appid = str(game.get("appid") or "")
                    path  = os.path.normcase(game.get("path") or game.get("library") or "")
                    exe   = os.path.normcase(game.get("exe") or "")

                    # scope appid to platform so cross-platform name collisions don't dedup
                    key = f"{platform}:{appid}" if appid else path or exe
                    if not key:
                        continue
                    if key in seen_games:
                        continue
                    seen_games.add(key)
                    results.append(game)

            except Exception as exc:
                self._log(f"{platform} scanner failed: {exc}\n{traceback.format_exc()}")
                if progress_callback:
                    progress_callback("count", platform, 0, None, None)

            finally:
                completed += 1
                if progress_callback:
                    progress_callback(
                        "overall",
                        platform,
                        f"{platform} complete | {platform_count} game(s)",
                        completed,
                        total
                    )

        self._log(f"runAllScanners: end | total_results={len(results)}")
        return results

    # ── game actions ──────────────────────────────────────────────────────────

    def randomGame(self):
        valid = [g for g in self.masterGameList
                 if g.get("exe") and os.path.isfile(g["exe"])]
        if not valid:
            messagebox.showerror("Error", "No games with valid executables found.")
            return None
        return random.choice(valid)

    def launch_game(self, game):
        try:
            appid = str(game["appid"])
            if appid.isdigit():
                subprocess.Popen([f"steam://rungameid/{appid}"])
                return
            exe = game.get("exe")
            if not exe or not os.path.isfile(exe):
                messagebox.showerror("Error", f"{game['name']} has no valid executable.")
                return
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        except Exception as exc:
            print(f"Error launching the game: {exc}")

    # ── UI ────────────────────────────────────────────────────────────────────

    def UI(self):
        ui = Tk()
        ui.title("DragonShorts")

        frame = ttk.Frame(ui, padding=50)
        frame.grid()

        self.results = ttk.Label(frame, text="")
        self.results.grid(column=0, row=2, columnspan=2)

        self.scanStatus = StringVar(value="Idle")
        ttk.Label(frame, textvariable=self.scanStatus).grid(
            column=0, row=3, columnspan=2, pady=(5, 0))

        self.scanProgress = ttk.Progressbar(
            frame, mode="determinate", length=320, maximum=len(PLATFORM_ORDER))
        self.scanProgress.grid(column=0, row=4, columnspan=2, pady=(5, 10))

        platformStatusFrame = ttk.LabelFrame(frame, text="Platform Status", padding=10)
        platformStatusFrame.grid(column=0, row=5, columnspan=2, sticky="ew", pady=(0, 10))

        self.platformStatusVars = {}
        for row_idx, platform in enumerate(self.platformOrder):
            label = self._platformLabel(platform)
            var   = StringVar(value=f"{label}: idle")
            self.platformStatusVars[platform] = var
            ttk.Label(platformStatusFrame, textvariable=var, anchor="w").grid(
                column=0, row=row_idx, sticky="w", pady=1)

        def finalize_scan(games):
            self.masterGameList = games or []
            self.scanActive = False
            self.scanProgress["value"] = self.scanProgress["maximum"]
            self.scanStatus.set("Scan complete")
            self.results.config(text=f"Found {len(self.masterGameList)} games")
            self._activePlatform = None
            self._log(f"UI finalize_scan: {len(self.masterGameList)} games")
            scanButton.config(state="normal")

        def pump_scan_updates():
            try:
                processed = 0
                while processed < 120:
                    kind, platform, message, current, total = self.scanQueue.get_nowait()
                    processed += 1

                    if kind == "done":
                        finalize_scan(message)
                        return

                    if kind == "count":
                        if platform:
                            count = int(message or 0)
                            self._platformFoundCounts[platform] = count
                            if platform in self.platformStatusVars:
                                self.platformStatusVars[platform].set(
                                    f"{self._platformLabel(platform)}: {count} game(s)"
                                )

                    elif kind == "detail":
                        if platform:
                            self._activePlatform = platform
                            self.scanStatus.set(f"{self._platformLabel(platform)}: {message}")
                            if platform in self.platformStatusVars:
                                self.platformStatusVars[platform].set(
                                    f"{self._platformLabel(platform)}: {message}"
                                )

                    elif kind == "platform":
                        if platform:
                            self._activePlatform = platform
                        if platform in self.platformStatusVars and current is not None and total:
                            self.platformStatusVars[platform].set(
                                f"{self._platformLabel(platform)}: {message} ({current}/{total})"
                            )

                    elif kind == "overall":
                        if total:
                            self.scanProgress.configure(maximum=total)
                        if current is not None:
                            self.scanProgress["value"] = current
                        eta = self._formatEta(current or 0, total or 0)
                        active_label = self._platformLabel(self._activePlatform) if self._activePlatform else "Working"
                        found_so_far = sum(self._platformFoundCounts.values())
                        self.results.config(
                            text=f"Scanning {active_label}... {current}/{total} platforms | {found_so_far} found | ETA {eta}"
                        )
                        if platform in self.platformStatusVars:
                            count = self._platformFoundCounts.get(platform, 0)
                            self.platformStatusVars[platform].set(
                                f"{self._platformLabel(platform)}: complete ({count} game(s))"
                            )

            except queue.Empty:
                pass
            except Exception as exc:
                self._log(f"pump_scan_updates error: {exc}")
                self.scanStatus.set(f"UI error: {exc}")

            # ── check done OUTSIDE the queue loop ────────────────────────────
            # This fires even if queue was empty when _scanDoneEvent gets set
            if self.scanActive and self._scanDoneEvent.is_set():
                # drain any remaining queue items first
                try:
                    while True:
                        kind, platform, message, current, total = self.scanQueue.get_nowait()
                        if kind == "done":
                            finalize_scan(message)
                            return
                        if kind == "count" and platform:
                            count = int(message or 0)
                            self._platformFoundCounts[platform] = count
                except queue.Empty:
                    pass
                finalize_scan(self._scanResultBuffer)
                return

            if self.scanActive:
                ui.after(100, pump_scan_updates)

        def scan_worker(platforms):
            games = []
            try:
                self._log("scan_worker: begin")
                games = self.runAllScanners(
                    platforms,
                    progress_callback=self._progressCallback
                )
            except Exception:
                self._log("scan_worker: exception\n" + traceback.format_exc())
            finally:
                self._scanResultBuffer = games or []
                self._scanDoneEvent.set()  # always set completion signal

                # best-effort done event for normal path
                done_item = ("done", None, self._scanResultBuffer, None, None)
                for _ in range(20):
                    try:
                        self.scanQueue.put(done_item, timeout=0.1)
                        break
                    except queue.Full:
                        try:
                            self.scanQueue.get_nowait()
                        except queue.Empty:
                            pass

                self._log("scan_worker: done queued")

        def scan_games():
            if self.scanActive:
                return

            self.scanActive = True
            self.scanStartedAt = t.time()
            self._scanDoneEvent.clear()
            self._scanResultBuffer = []
            self._activePlatform = None
            self._platformFoundCounts = {p: 0 for p in self.platformOrder}

            # clear stale queue entries
            try:
                while True:
                    self.scanQueue.get_nowait()
            except queue.Empty:
                pass

            self.scanProgress["value"] = 0
            self.results.config(text="Scanning...")
            self.scanStatus.set("Starting scan...")
            scanButton.config(state="disabled")
            ui.update_idletasks()

            for platform in self.platformOrder:
                if platform in self.platformStatusVars:
                    self.platformStatusVars[platform].set(
                        f"{self._platformLabel(platform)}: queued"
                    )

            threading.Thread(
                target=scan_worker,
                args=(list(self.platformOrder),),
                daemon=True
            ).start()
            ui.after(100, pump_scan_updates)

        def random_launch():
            if not self.masterGameList:
                self.results.config(text="No Games Found!")
                return
            game = self.randomGame()
            if not game:
                return
            self.results.config(text=f"Selected: {game['name']}")
            if messagebox.askyesno("Confirm Launch",
                                   f"Launch {game['name']}?"):
                self.launch_game(game)

        scanButton = ttk.Button(frame, text="Scan Games", command=scan_games)
        scanButton.grid(column=0, row=1)

        ttk.Button(frame, text="Pick a random game",
                   command=random_launch).grid(column=0, row=6)
        ttk.Button(frame, text="Quit",
                   command=ui.destroy).grid(column=1, row=6)

        ui.mainloop()



if __name__ == "__main__":
    GamePicker().UI()