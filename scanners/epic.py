import os


def epicRoot():
    possible = [
        r'Program Files (x86)\Epic Games',
        r'Program Files\Epic Games',
        r'Epic Games',
        r'EpicGames'
    ]
    for path in possible:
        if os.path.exists(path):
            return path
    return None


class epicScanner:
    def __init__(self):
        super().__init__()
        self.epicGames = []
        self.libraries = []

    def scanner(self):
        for e in self.libraries:
            self.epicGames = [
                f'{e}/{game}'
                for game in self.libraries
                if os.path.exists(e)
            ]

        return self.epicGames

if __name__ == '__main__':
    epicScanner()