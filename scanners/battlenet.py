import os
import sqlite3

blacklist = [
    "crash", "report", "bug", "updater", "launcher",
    "helper", "telemetry", "anti", "cheat", "bssndrpt",
    "unitycrash", "unrealcrash", "setup", "install", "dlc", "DLC"
]


class BattleNetScanner:
    def __init__(self):
        self.games = []

    # ---------------------------------------------------------
    # 1. Locate Battle.net Agent root
    # ---------------------------------------------------------
    def find_agent_root(self):
        possible = [
            r"C:\ProgramData\Battle.net\Agent",
            r"C:\Program Files (x86)\Battle.net",
            r"C:\Program Files\Battle.net"
        ]

        for path in possible:
            if os.path.exists(path):
                return path

        return None

    # ---------------------------------------------------------
    # 2. Load product.db and filter out DLC by product_type
    # ---------------------------------------------------------
    def load_product_db(self, agent_root):
        db_path = os.path.join(agent_root, "product.db")

        if not os.path.isfile(db_path):
            print("Battle.net product.db not found.")
            return []

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Check schema
            cursor.execute("PRAGMA table_info(product_install)")
            columns = [col[1] for col in cursor.fetchall()]

            # If product_type exists, use it
            if "product_type" in columns:
                cursor.execute("SELECT uid, install_path, product_type FROM product_install")
                rows = cursor.fetchall()

                # Keep ONLY real games
                rows = [
                    r for r in rows
                    if r[2].lower() in ("game", "product")
                ]

            else:
                # Fallback for older schemas
                cursor.execute("SELECT uid, install_path FROM product_install")
                rows = cursor.fetchall()

            conn.close()
            return rows

        except Exception as e:
            print(f"Error reading product.db: {e}")
            return []

    # ---------------------------------------------------------
    # 3. Find the main executable inside a game folder
    # ---------------------------------------------------------
    def find_main_exe(self, folder):
        if not os.path.isdir(folder):
            return None

        for root, dirs, files in os.walk(folder):
            for f in files:
                if not f.lower().endswith(".exe"):
                    continue

                # blacklist filter
                if any(bad in f.lower() for bad in blacklist):
                    continue

                # CoD DLC helper EXEs often slip through — require game-like names
                if not any(keyword in f.lower() for keyword in ["cod", "warzone", "overwatch", "diablo", "wow"]):
                    continue

                return os.path.join(root, f)

        return None

    # ---------------------------------------------------------
    # 4. Full scan
    # ---------------------------------------------------------
    def scan(self):
        agent_root = self.find_agent_root()
        if not agent_root:
            print("Battle.net Agent folder not found.")
            return []

        installs = self.load_product_db(agent_root)

        for entry in installs:
            # product_type present
            if len(entry) == 3:
                uid, path, ptype = entry
            else:
                uid, path = entry
                ptype = "game"

            if not os.path.isdir(path):
                continue

            # Folder-based DLC filtering
            bad_path_terms = ["dlc", "data", "patch", "test", "ptr"]
            if any(term in path.lower() for term in bad_path_terms):
                continue

            exe = self.find_main_exe(path)
            if not exe:
                continue

            game = {
                "name": os.path.basename(path),
                "appid": "N/A",
                "exe": exe
            }

            self.games.append(game)

        return self.games