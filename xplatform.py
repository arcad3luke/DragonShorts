import os

class XPlatform:
    def __init__(self, platform, driveList, defaultPaths):
        self.driveList = driveList
        self.platform = platform
        self.defaultPaths = defaultPaths

    def gameFinder(self):
        found = []

        IGNORE_NAMES = {
            "engine", "engines", "redist", "redistributables",
            "common", "support", "tools", "launcher", "launchers",
            "temp", "tmp", "sdk", "sdks"
        }

        for drive in self.driveList:
            for game_name, relative_path in self.defaultPaths.items():

                paths = relative_path if isinstance(relative_path, list) else [relative_path]

                for rel in paths:
                    full_path = os.path.join(drive, rel)

                    if not os.path.exists(full_path):
                        continue

                    try:
                        entries = os.listdir(full_path)
                    except FileNotFoundError:
                        continue

                    for entry in entries:
                        entry_path = os.path.join(full_path, entry)

                        # Must be a folder
                        if not os.path.isdir(entry_path):
                            continue

                        # Ignore known junk
                        if entry.lower() in IGNORE_NAMES:
                            continue

                        # Check for .exe in root OR one subfolder
                        has_exe = False

                        # Check root
                        try:
                            root_files = os.listdir(entry_path)
                            if any(f.lower().endswith(".exe") for f in root_files):
                                has_exe = True
                            else:
                                # Check one level down
                                for sub in root_files:
                                    sub_path = os.path.join(entry_path, sub)
                                    if os.path.isdir(sub_path):
                                        try:
                                            sub_files = os.listdir(sub_path)
                                            if any(f.lower().endswith(".exe") for f in sub_files):
                                                has_exe = True
                                                break
                                        except FileNotFoundError as fnf:
                                            print(f'Error: {fnf}')
                        except:
                            continue

                        if not has_exe:
                            continue

                        # Passed filters — count it as a game
                        found.append({
                            "name": entry,
                            "path": entry_path
                        })

        return found