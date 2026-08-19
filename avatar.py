"""Roster data for Avatar Legends: The Fighting Game.

Names are the in-game strings as they appear on the versus screen, since
they are what OCR reads and what findBestMatch scores against.
"""

characters = [
    "Aang",
    "Avatar State Aang",
    "Azula",
    "Katara",
    "Korra",
    "Kyoshi",
    "Nightmare Korra",
    "Ozai",
    "Sokka",
    "Toph",
    "Zaheer",
    "Zuko",
]

# Second line of the versus nameplate. Three per character; picking one
# alters the moveset. Not reported by default, but kept here so the
# assist line can be matched instead of discarded as OCR noise.
supports = {
    "Aang": ["Gyatso", "Appa", "Momo"],
    "Avatar State Aang": ["Katara", "Avatar Roku", "Guru Pathik"],
    "Azula": ["Lo and Li", "Joo Dee", "Ursa"],
    "Katara": ["Kanna", "Master Pakku", "Hakoda"],
    "Korra": ["Naga", "Raava", "Tonraq"],
    "Kyoshi": ["Rangi", "Kelsang", "Yun"],
    "Nightmare Korra": ["Vaatu", "Dark Spirit", "Dark Avatar Unalaq"],
    "Ozai": ["Firelord Sozin", "Admiral Zhao", "Imperial Firebender"],
    "Sokka": ["Suki", "Piandao", "Princess Yue"],
    "Toph": ["The Boulder", "Badgermole", "The Hippo"],
    "Zaheer": ["P'li", "Ming Hua", "Ghazan"],
    "Zuko": ["Mai", "June", "Ran and Shaw"],
}

all_supports = sorted({name for names in supports.values() for name in names})
