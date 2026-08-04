"""Every arena in the game, what it owns, and which minigame runs it.

The archive names an arena by its folder -- `models/arena/<arena>/` -- and a
minigame by its overlay, `overlays/modes/<mode>.bin`. The two line up by prefix
for the six multi-arena modes and by name for the single-arena ones, which is
enough to say what each minigame's level list is and what each level carries:
its own geometry, a crystal-challenge variant, props, music and effects.

Reading it beside `tools/arena_setup.py` says which of that is per level in the
binaries: only the crate mode configures ground per arena (FORMAT.md §8.4).

    tools/arena_list.py game/SCUS_945.70
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crashbash.archive import BashArchive  # noqa: E402

# Folder prefix -> the overlay that runs those arenas. The single-arena
# minigames each own one medieval or boss arena rather than a prefix.
MODE_OF_PREFIX = {
    "balls": "ball",
    "crate": "crate",
    "dash": "dash",
    "pogo": "pogo",
    "polar": "polar",
    "tank": "tank",
}
MODE_OF_ARENA = {
    "medieval_ring": "ring",
    "medieval_keg": "kegs",
    "medieval_mallet": "mallet",
    "medieval_dragon": "dino",
    "medieval_chicken": "dino",
    "boss_papu": "papu",
    "boss_oxide": "oxide",
    "boss_komodo": "tank",
    "boss_bear": "polar",
}


def mode_of(arena: str) -> str:
    if arena in MODE_OF_ARENA:
        return MODE_OF_ARENA[arena]
    return MODE_OF_PREFIX.get(arena.split("_")[0], "?")


def inventory(archive: BashArchive):
    arenas: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    audio: dict[str, list[str]] = defaultdict(list)
    for entry in archive:
        parts = entry.name.split("/")
        if entry.name.startswith("models/arena/") and len(parts) == 4:
            arenas[parts[2]][parts[3].rsplit(".", 1)[0]].append(entry)
        elif entry.name.startswith(("sfx/arena/", "music/")):
            audio[parts[-1].rsplit(".", 1)[0]].append(entry.name)
    return arenas, audio


def main() -> None:
    archive = BashArchive(sys.argv[1] if len(sys.argv) > 1 else "game/SCUS_945.70")
    arenas, audio = inventory(archive)

    by_mode: dict[str, list[str]] = defaultdict(list)
    for arena in arenas:
        by_mode[mode_of(arena)].append(arena)

    playable = 0
    for mode in sorted(by_mode):
        names = sorted(by_mode[mode])
        real = [n for n in names if "arena" in arenas[n]]
        playable += len(real)
        print(f"=== {mode}  ({len(real)} arena{'s' if len(real) != 1 else ''}"
              f"{f', {len(names) - len(real)} support folder(s)' if len(names) > len(real) else ''})")
        for arena in names:
            parts = arenas[arena]
            if "arena" not in parts:
                print(f"    {arena:<22} (props only: {', '.join(sorted(parts))})")
                continue
            size = sum(e.size for group in parts.values() for e in group)
            extras = [k for k in sorted(parts) if k != "arena"]
            sound = audio.get(arena, [])
            print(f"    {arena:<22} {size:>9,} B   "
                  f"{'+' + ', '.join(extras) if extras else 'arena only'}")
            if sound:
                print(f"    {'':<22} {'':>9}     audio: {', '.join(sorted(sound))}")
        print()
    print(f"{playable} arenas over {len(by_mode)} minigames, "
          f"{sum(len(v) for v in arenas.values())} model pairs in all")


if __name__ == "__main__":
    main()
