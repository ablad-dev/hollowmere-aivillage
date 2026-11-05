# Art assets (optional)

The village renders fine with no art at all — villagers fall back to coloured
tokens and buildings are drawn as vector shapes. Drop these files in to upgrade
it, both CC0 (public domain):

| File to create here | Source pack |
|---|---|
| `characters.png` | [Kenney — Tiny Town](https://kenney.nl/assets/tiny-town) → `Tilemap/tilemap_packed.png`, or [Kenney — Roguelike Characters](https://kenney.nl/assets/roguelike-characters) |
| `grass.png` | Any seamless 24×24 grass tile, e.g. from [Kenney — Tiny Town](https://kenney.nl/assets/tiny-town) |

**`characters.png` must be a 16×16 spritesheet.** Villagers use frames 0–5
(`sprite: "villager_N"` in `backend/app/seed.py` maps straight to frame `N`).
Each sprite is tinted with the villager's colour, so a plain white/grey
silhouette sheet looks best.

If a file is missing, Phaser logs a load error and the scene quietly uses the
vector fallback. No code change needed.

Other good CC0 options:

- [LPC Universal Sprite Sheet](https://opengameart.org/content/universal-lpc-sprite-sheet) — proper 4-direction walk cycles (CC-BY-SA / GPL, check per-author terms)
- [Kenney — Tiny Dungeon](https://kenney.nl/assets/tiny-dungeon) — props and furniture
