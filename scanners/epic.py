import json
import os


BLACKLIST = [
    "crash",
    "report",
    "bug",
    "updater",
    "launcher",
    "helper",
    "telemetry",
    "anti",
    "cheat",
    "bssndrpt",
    "unitycrash",
    "unrealcrash",
    "setup",
    "install",
    "dlc",
]


def _iter_drives():
    for letter in range(ord("A"), ord("Z") + 1):
        drive = f"{chr(letter)}:\\"
        if os.path.exists(drive):
            yield drive


def _find_exe(folder):
    candidates = []

    try:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                lower = name.lower()
                if not lower.endswith(".exe"):
                    continue
                if any(bad in lower for bad in BLACKLIST):
                    continue
                candidates.append(os.path.join(root, name))
    except (OSError, PermissionError):
        return None

    if not candidates:
        return None

    preferred_keywords = ["shipping", "win64", "win32", "game", "client"]

    for keyword in preferred_keywords:
        for candidate in candidates:
            if keyword in os.path.basename(candidate).lower():
                return candidate

    try:
        candidates.sort(key=lambda path: os.path.getsize(path), reverse=True)
    except OSError:
        pass

    return candidates[0]


def _iter_manifest_dirs():
    candidates = [
        r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests",
        r"C:\ProgramData\Epic\UnrealEngineLauncher\Data\Manifests",
    ]

    seen = set()
    for path in candidates:
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            continue
        seen.add(norm)

        if os.path.isdir(path):
            yield path


def _scan_from_manifests():
    results = []
    seen_paths = set()

    for manifest_dir in _iter_manifest_dirs():
        try:
            entries = os.listdir(manifest_dir)
        except (OSError, PermissionError):
            continue

        for entry in entries:
            if not entry.lower().endswith((".item", ".manifest", ".json")):
                continue

            manifest_path = os.path.join(manifest_dir, entry)

            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, PermissionError, ValueError, json.JSONDecodeError):
                continue

            install_path = (
                data.get("InstallLocation")
                or data.get("InstallDir")
                or data.get("InstallLocationPath")
            )
            display_name = (
                data.get("DisplayName")
                or data.get("AppName")
                or data.get("MainGameAppName")
                or os.path.splitext(entry)[0]
            )
            app_name = (
                data.get("AppName")
                or data.get("CatalogItemId")
                or display_name
            )

            if not install_path or not os.path.isdir(install_path):
                continue

            norm = os.path.normcase(os.path.normpath(install_path))
            if norm in seen_paths:
                continue
            seen_paths.add(norm)

            exe = _find_exe(install_path)
            if not exe:
                continue

            results.append({
                "appid": f"epic games:{app_name}",
                "name": str(display_name).strip(),
                "path": install_path,
                "library": install_path,
                "exe": exe,
                "favorite": False,
            })

    return results


def _iter_library_roots():
    relatives = [
        r"Program Files\Epic Games",
        r"Program Files (x86)\Epic Games",
        r"Epic Games",
        r"EpicGames",
        r"Games\Epic Games",
    ]

    seen = set()
    for drive in _iter_drives():
        for rel in relatives:
            root = os.path.join(drive, rel)
            norm = os.path.normcase(os.path.normpath(root))
            if norm in seen:
                continue
            seen.add(norm)

            if os.path.isdir(root):
                yield root


def _scan_from_filesystem():
    results = []
    seen_paths = set()

    for root in _iter_library_roots():
        try:
            entries = os.listdir(root)
        except (OSError, PermissionError):
            continue

        for entry in entries:
            full_path = os.path.join(root, entry)
            if not os.path.isdir(full_path):
                continue

            norm = os.path.normcase(os.path.normpath(full_path))
            if norm in seen_paths:
                continue
            seen_paths.add(norm)

            exe = _find_exe(full_path)
            if not exe:
                continue

            results.append({
                "appid": f"epic games:{entry}",
                "name": entry,
                "path": full_path,
                "library": full_path,
                "exe": exe,
                "favorite": False,
            })

    return results


def scanForGames():
    games = _scan_from_manifests()
    if games:
        return games
    return _scan_from_filesystem()


class epicScanner:
    def __init__(self, epicGames=None, libraries=None):
        self.epicGames = epicGames or []
        self.libraries = libraries or []

    def epicRoot(self):
        for root in _iter_library_roots():
            return root
        return None

    def scanner(self, libraries=None):
        if libraries is not None:
            self.libraries = libraries
        self.epicGames = self.scan()
        return self.epicGames

    def scan(self):
        return scanForGames()


if __name__ == "__main__":
    for game in scanForGames():
        print(f"{game['name']} -> {game['exe']}")