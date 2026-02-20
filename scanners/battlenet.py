import os
import sqlite3
import DragonShorts


class battleNetScanner:
    def __init__(self):
        super().__init__()
        self.bnetGames = []
        self.libraries = []

    def bnetRoot(self):
        possible = [
            r'Program Files (x86)\Battle.net',
            r'Program Files\Battle.net',
            r'Battle.net'
        ]

        for path in possible:
            if os.path.exists(path):
                return path
        return None

    def dirFinder(self):
        bnetDir = DragonShorts.GamePicker.DriveScanner(self)
        for d in bnetDir:
            try:
                if 'battle.net' not in d or 'bnet' not in d:
                    continue
                else:
                    self.bnetGames.append(d)
                    return self.bnetGames
            except FileNotFoundError as fnf:
                return f'Error while attempting to locate directory: {fnf}'

    def gameScanner(self, libraries):
        for g in libraries:
            self.bnetGames = [
                f'{g}/{game}'
                for game in libraries
                if os.path.exists(g)
            ]

    def dbConnect(self):
        conn = sqlite3.connect(f'{self.bnetRoot}product.db')
        bnetCursor = conn.cursor()
        bnetFetch = bnetCursor.fetchall()
        for entry in bnetFetch:
            self.bnetGames.append(entry)
            print(f'Entry: {self.bnetGames.index(entry)} added to collection!')
        return self.bnetGames

    def scanForGames(self):
        root = self.bnetRoot()
        libraries = self.dirFinder()
        games = self.gameScanner(libraries)
        return root, libraries, games

if __name__ == '__main__':
    pass