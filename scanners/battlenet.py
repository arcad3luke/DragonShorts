import os
import sqlite3
import ctypes
import winreg
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

blacklist = [
    "crash", "report", "bug", "updater",
    "helper", "telemetry", "anti", "cheat", "bssndrpt",
    "unitycrash", "unrealcrash", "setup", "install", "dlc"
]

_TERM_WIDTH = 78
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _spinner_frame(start_time):
    """Return a spinner character based on elapsed time."""
    elapsed = time.monotonic() - start_time
    idx = int(elapsed / 0.1) % len(_SPINNER_FRAMES)
    return _SPINNER_FRAMES[idx]


def _fmt_elapsed(start_time):
    """Return elapsed time as a short string."""
    s = time.monotonic() - start_time
    if s < 60:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def _progress_spinner(msg, start_time):
    """Spinner line for unknown-length phases."""
    # Avoid flooding when stdout is redirected (PyInstaller/UI bridge).
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return
    spin = _spinner_frame(start_time)
    elapsed = _fmt_elapsed(start_time)
    line = f"{spin} {msg} [{elapsed}]"
    sys.stdout.write(f"\r[bnet] {line:<{_TERM_WIDTH}}")
    sys.stdout.flush()


def _progress_done(msg, start_time=None):
    """Finalise a line, optionally appending total elapsed time."""
    suffix = f" ({_fmt_elapsed(start_time)})" if start_time is not None else ""
    if not getattr(sys, "stdout", None):
        return
    try:
        sys.stdout.write(f"\r[bnet] {msg + suffix:<{_TERM_WIDTH}}\n")
        sys.stdout.flush()
    except Exception:
        return


class BattleNetScanner:
    def __init__(self, debug=False, progress=None):
        self.games = []
        self.debug = debug
        self.progress = progress
        self._last_progress_time = 0.0
        self.max_exe_workers = 4  # keep small; higher can hurt on HDDs
        self.resolve_phase_timeout_s = 90   # hard cap for exe-resolution phase
        self.find_exe_max_depth = 4         # prevent deep recursive stalls

    def _resolve_install_entry(self, entry):
            if len(entry) == 3:
                _uid, path, _ptype = entry
            else:
                _uid, path = entry

            if not path or not os.path.isdir(path):
                return None

            base_name = os.path.basename(path).strip().lower()
            if base_name in {"battle.net", "battle net"}:
                return None

            lower_path = path.lower()
            if any(term in lower_path for term in ["dlc", "patch", "test", "ptr"]):
                return None

            exe = self.find_main_exe(path)
            if not exe:
                return None

            return {
                "name": os.path.basename(path),
                "appid": f"battle.net:{os.path.basename(path)}",
                "path": path,
                "library": path,
                "exe": exe,
                "favorite": False,
            }

    def _throttled(self, fn, interval=0.1):
        """Call fn() at most once per interval seconds."""
        now = time.monotonic()
        if now - self._last_progress_time >= interval:
            fn()
            self._last_progress_time = now

    def _emit_detail(self, message):
        if self.progress:
            try:
                self.progress.update_spinner("Scanning Battle.net", message)
                return
            except Exception:
                pass
        _progress_done(message)

    def _emit_bar(self, current, total, message):
        if self.progress:
            try:
                self.progress.update_bar("Scanning Battle.net", current, total)
                return
            except Exception:
                pass
        _progress_spinner(message, time.monotonic())

    # ---------------------------------------------------------
    # 0. File format detection
    # ---------------------------------------------------------
    def _detect_file_format(self, path):
        if not os.path.isfile(path):
            return "missing"
        try:
            with open(path, "rb") as f:
                head = f.read(64)
        except OSError:
            return "unreadable"

        if not head:
            return "empty"
        if head.startswith(b"SQLite format 3\x00"):
            return "sqlite3"
        if head.startswith(b"PK\x03\x04"):
            return "zip/container"
        if head.startswith(b"\x1f\x8b"):
            return "gzip"
        if head.startswith(b"\x28\xb5\x2f\xfd"):
            return "zstd"
        if head.startswith(b"\x04\x22\x4d\x18"):
            return "lz4-frame"

        stripped = head.lstrip()
        if stripped.startswith((b"{", b"[", b"<")):
            return "text/structured (json/xml-like)"

        printable = sum(32 <= b <= 126 or b in (9, 10, 13) for b in head)
        if printable / max(len(head), 1) > 0.90:
            return "text/plain-like"

        return f"binary/proprietary (magic={head[:8].hex()})"

    # ---------------------------------------------------------
    # 1. Drive + DB helpers
    # ---------------------------------------------------------
    def _is_sqlite_file(self, path):
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "rb") as f:
                header = f.read(16)
            return header.startswith(b"SQLite format 3\x00")
        except OSError:
            return False

    def _iter_candidate_drives(self):
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                drive = f"{chr(65 + i)}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive))
                if drive_type in (2, 3, 4):
                    yield drive

    def _safe_walk(self, root):
        def _onerror(_err):
            return
        try:
            yield from os.walk(root, topdown=True, onerror=_onerror, followlinks=False)
        except (OSError, PermissionError):
            return

    def _probe_product_db_files(self, max_hits=200):
        hits = 0
        for drive in self._iter_candidate_drives():
            for current_root, dirs, files in self._safe_walk(drive):
                try:
                    dirs[:] = [
                        d for d in dirs
                        if d.lower() not in {"windows", "system volume information", "$recycle.bin"}
                    ]
                    for f in files:
                        fl = f.lower()
                        if not (fl.startswith("product") and fl.endswith(".db")):
                            continue
                        p = os.path.join(current_root, f)
                        is_sqlite = self._is_sqlite_file(p)
                        has_shape = self._db_has_game_install_shape(p) if is_sqlite else False
                        fmt = self._detect_file_format(p)
                        print(f"[bnet-probe] {p} | fmt={fmt} | sqlite={is_sqlite} | shape={has_shape}")
                        hits += 1
                        if hits >= max_hits:
                            print(f"[bnet-probe] hit limit reached ({max_hits})")
                            return
                except (OSError, PermissionError):
                    continue

    def _db_has_game_install_shape(self, path):
        if not self._is_sqlite_file(path):
            return False
        try:
            with sqlite3.connect(path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                if not tables:
                    return False

                for t in tables:
                    tl = t.lower()
                    if not any(k in tl for k in ("product", "install", "game")):
                        continue
                    cur.execute(f"PRAGMA table_info([{t}])")
                    cols = {row[1].lower() for row in cur.fetchall()}
                    if "install_path" in cols:
                        return True
                    if ("path" in cols or "location" in cols) and (
                        "uid" in cols or "product" in cols or "id" in cols
                    ):
                        return True

                if "product_install" in [t.lower() for t in tables]:
                    return True

        except Exception:
            return False

        return False

    def _score_db_path(self, db_path):
        p = db_path.lower()
        score = 0
        if "battle.net" in p or "battlenet" in p:
            score += 6
        if "blizzard" in p:
            score += 4
        if "\\agent\\" in p:
            score += 5
        if "\\programdata\\" in p:
            score += 3
        if os.path.basename(p) == "product.db":
            score += 2
        return score

    def _looks_like_product_db_name(self, filename):
        n = filename.lower()
        return n.startswith("product") and n.endswith(".db")

    # ---------------------------------------------------------
    # 2. Find product.db
    # ---------------------------------------------------------
    def find_product_db_path(self):
        rel_candidates = [
            r"ProgramData\Battle.net\Agent\product.db",
            r"ProgramData\Battle.net\Agent\data\product.db",
            r"Program Files (x86)\Battle.net\Agent\product.db",
            r"Program Files\Battle.net\Agent\product.db",
            r"Battle.net\Agent\product.db",
            r"Blizzard\Agent\product.db",
        ]

        t0 = time.monotonic()

        # fast explicit checks
        for drive in self._iter_candidate_drives():
            for rel in rel_candidates:
                db_path = os.path.join(drive, rel)
                self._throttled(lambda: _progress_spinner("Checking known DB locations...", t0))
                if self._db_has_game_install_shape(db_path):
                    _progress_done(f"Found DB: {db_path}", t0)
                    return db_path

        # broad scan
        candidates = []
        scanned = 0
        for drive in self._iter_candidate_drives():
            for current_root, dirs, files in self._safe_walk(drive):
                try:
                    dirs[:] = [
                        d for d in dirs
                        if d.lower() not in {"windows", "system volume information", "$recycle.bin"}
                    ]
                    scanned += 1
                    self._throttled(
                        lambda: _progress_spinner(f"Scanning drives... {scanned} folders", t0)
                    )

                    for f in files:
                        if not self._looks_like_product_db_name(f):
                            continue
                        db_path = os.path.join(current_root, f)
                        if self._db_has_game_install_shape(db_path):
                            candidates.append((self._score_db_path(db_path), db_path))
                except (OSError, PermissionError):
                    continue

        if not candidates:
            _progress_done("No valid product*.db found — using fallbacks.", t0)
            if self.debug:
                self._probe_product_db_files()
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        chosen = candidates[0][1]
        _progress_done(f"Selected DB: {chosen}", t0)

        if self.debug:
            for s, p in candidates[:10]:
                print(f"  score={s}  {p}")

        return chosen

    # ---------------------------------------------------------
    # 3. Load product.db
    # ---------------------------------------------------------
    def load_product_db(self, db_path):
        if not db_path or not os.path.isfile(db_path):
            print("Battle.net product.db not found.")
            return []

        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cursor.fetchall()]
                lower_map = {t.lower(): t for t in tables}

                table = None
                for preferred in ("product_install", "productinstall", "installs", "products"):
                    if preferred in lower_map:
                        table = lower_map[preferred]
                        break

                if not table:
                    for t in tables:
                        cursor.execute(f"PRAGMA table_info([{t}])")
                        cols = [c[1].lower() for c in cursor.fetchall()]
                        if ("uid" in cols or "id" in cols) and (
                            "install_path" in cols or "path" in cols or "location" in cols
                        ):
                            table = t
                            break

                if not table:
                    if self.debug:
                        print(f"[bnet] no usable table in DB: {db_path}")
                    return []

                cursor.execute(f"PRAGMA table_info([{table}])")
                cols = [c[1].lower() for c in cursor.fetchall()]

                uid_col   = "uid" if "uid" in cols else ("id" if "id" in cols else None)
                path_col  = (
                    "install_path" if "install_path" in cols
                    else ("path" if "path" in cols
                    else ("location" if "location" in cols else None))
                )
                ptype_col = "product_type" if "product_type" in cols else None

                if not uid_col or not path_col:
                    return []

                if ptype_col:
                    cursor.execute(
                        f"SELECT [{uid_col}], [{path_col}], [{ptype_col}] FROM [{table}]"
                    )
                    raw_rows = cursor.fetchall()
                    rows = []
                    for uid, install_path, product_type in raw_rows:
                        pt = (product_type or "").lower()
                        if not pt or pt in ("game", "product"):
                            rows.append((uid, install_path, pt))
                    return rows

                cursor.execute(f"SELECT [{uid_col}], [{path_col}] FROM [{table}]")
                return cursor.fetchall()

        except sqlite3.DatabaseError as e:
            print(f"Error reading product.db (invalid SQLite): {db_path} | {e}")
            return []
        except Exception as e:
            print(f"Error reading product.db: {db_path} | {e}")
            return []

    def _normalize_name(self, name):
        return "".join(ch for ch in name.lower() if ch.isalnum())

    # ---------------------------------------------------------
    # 4. Find main exe
    # ---------------------------------------------------------
    def find_main_exe(self, folder):
        if not os.path.isdir(folder):
            return None

        folder_name_norm = self._normalize_name(os.path.basename(folder))
        candidates = []

        for root, dirs, files in self._safe_walk(folder):
            # prune depth
            rel = os.path.relpath(root, folder)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth >= self.find_exe_max_depth:
                dirs[:] = []

            # prune noisy dirs
            dirs[:] = [d for d in dirs if d.lower() not in {
                "logs", "cache", "crash", "crashreports", "support",
                "_commonredist", "redistributables", "installer"
            }]

            for f in files:
                lower = f.lower()
                if not lower.endswith(".exe"):
                    continue
                if any(bad in lower for bad in blacklist):
                    continue

                full_path = os.path.join(root, f)
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = 0

                exe_name_norm = self._normalize_name(os.path.splitext(f)[0])
                name_match = (
                    folder_name_norm in exe_name_norm
                    or exe_name_norm in folder_name_norm
                )
                score = (1 if depth == 0 else 0, 1 if name_match else 0, size, -depth)
                candidates.append((score, full_path))

        if not candidates:
            return None
        candidates.sort(reverse=True, key=lambda x: x[0])
        return candidates[0][1]

    # ---------------------------------------------------------
    # 5. Registry fallback
    # ---------------------------------------------------------
    def _load_installs_from_registry(self):
        t0 = time.monotonic()
        rows = []
        seen = set()

        uninstall_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]

        name_tokens = (
            "blizzard", "battle.net", "warcraft", "overwatch",
            "diablo", "starcraft", "hearthstone", "heroes of the storm"
        )

        for hive, root_path in uninstall_roots:
            try:
                with winreg.OpenKey(hive, root_path) as root:
                    subkey_count = winreg.QueryInfoKey(root)[0]
                    for i in range(subkey_count):
                        self._throttled(
                            lambda: _progress_spinner(f"Scanning registry... {len(rows)} found", t0)
                        )
                        try:
                            sub = winreg.EnumKey(root, i)
                            with winreg.OpenKey(root, sub) as app:
                                display_name = ""
                                install_location = ""
                                display_icon = ""

                                try:
                                    display_name = str(winreg.QueryValueEx(app, "DisplayName")[0] or "")
                                except OSError:
                                    pass
                                try:
                                    install_location = str(winreg.QueryValueEx(app, "InstallLocation")[0] or "")
                                except OSError:
                                    pass
                                try:
                                    display_icon = str(winreg.QueryValueEx(app, "DisplayIcon")[0] or "")
                                except OSError:
                                    pass

                                dn = display_name.lower()
                                if not any(t in dn for t in name_tokens):
                                    continue

                                path = install_location.strip().strip('"')
                                if (not path or not os.path.isdir(path)) and display_icon:
                                    icon_path = display_icon.split(",")[0].strip().strip('"')
                                    if os.path.isfile(icon_path):
                                        path = os.path.dirname(icon_path)

                                if not path or not os.path.isdir(path):
                                    continue

                                key = path.lower()
                                if key in seen:
                                    continue
                                seen.add(key)
                                rows.append((sub, path, "game"))
                        except OSError:
                            continue
            except OSError:
                continue

        _progress_done(f"Registry scan done — {len(rows)} install(s) found.", t0)
        return rows

    # ---------------------------------------------------------
    # 6. Full scan
    # ---------------------------------------------------------
    def scan(self):
        self.games = []
        self._last_progress_time = 0.0
        self._emit_detail("Starting Battle.net scan...")

        db_path = self.find_product_db_path()
        installs = self.load_product_db(db_path) if db_path else []
        if not installs:
            installs = self._load_installs_from_registry()
        if not installs:
            self._emit_detail("No Battle.net installs found (DB or registry).")
            return []

        workers = min(self.max_exe_workers, max(1, len(installs)))
        seen_exes = set()
        total = len(installs)
        done = 0

        resolver = getattr(self, "_resolve_install_entry", None)
        if resolver is None:
            self._emit_detail("Battle.net resolver missing; using sequential fallback.")
            for entry in installs:
                game = self._resolve_install_entry(entry) if hasattr(self, "_resolve_install_entry") else None
                if game:
                    self.games.append(game)
            return self.games

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(resolver, e) for e in installs}
            phase_deadline = time.monotonic() + self.resolve_phase_timeout_s

            while pending:
                if time.monotonic() >= phase_deadline:
                    self._emit_detail("Battle.net exe resolution timed out; returning partial results.")
                    break

                finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                if not finished:
                    continue

                for fut in finished:
                    done += 1
                    self._throttled(lambda d=done, n=total: (
                        self.progress.update_bar("Scanning Battle.net", d, n) if self.progress else None
                    ), interval=0.2)

                    try:
                        game = fut.result()
                    except Exception:
                        game = None

                    if not game:
                        continue
                    exe = game["exe"]
                    if exe in seen_exes:
                        continue
                    seen_exes.add(exe)
                    self.games.append(game)

            for fut in pending:
                fut.cancel()

        self._emit_detail(f"Battle.net scan complete — {len(self.games)} game(s) found.")
        return self.games


if __name__ == "__main__":
    scanner = BattleNetScanner(debug=False)
    games = scanner.scan()
    print()
    for game in games:
        print(f"  {game['name']} -> {game['exe']}")