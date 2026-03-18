import os
from DragonShorts import GamePicker




class epicScanner:
    def __init__(self, epicGames, libraries):
        super().__init__()
        self.epicGames = epicGames
        self.libraries = libraries
        self.driveRoots = GamePicker.DriveScanner = []

    def epicRoot(self):

        possible = [
            r'Program Files (x86)\Epic Games',
            r'Program Files\Epic Games',
            r'Epic Games',
            r'EpicGames'
        ]
        rootPaths = [
            f'{self.driveRoots}/{possible}'
            for drive in self.driveRoots
            if os.path.exists(drive)
        ]
        for path in rootPaths:
            if os.path.exists(path):
                return path
            else:
                return None

    def scanner(self, libraries):
        self.libraries = libraries
        for e in libraries:
            self.epicGames = [
                f'{e}/{game}'
                for game in self.libraries
                if os.path.exists(e)
            ]

        return self.epicGames, self.libraries

if __name__ == '__main__':
    epicScanner()