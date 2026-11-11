# mahjong-ai

**Mahjong turn coach:** in-the-moment help deciding what to discard or call, for new
players using Singapore rules. Built on [Jev](https://thejevai.com/) (TypeSafe AI's
decision model).

## What it does (first version)

1. **Enter your hand before your turn.** The coach tells you how close you are and which
   tiles to watch for on other players' turns.
2. **Enter the tile you drew, or the tile someone threw.** The advice updates straight away.
3. **Ask what to do.** You get the best discard, or whether to pong, chow, kong, win or pass,
   and it names **the tiles you still need for a hand that pays** (meets the minimum tai),
   with how many of each are still live.

If another player already holds or has thrown the tiles your hand needs, those tiles show
as gone and the coach warns you to switch shapes. Its ranking prefers hands whose tiles are
still live.

## How it decides

- **Rules engine** (`mahjong_coach/advisor.py`, `hand.py`, `scoring.py`): works out every legal
  move, how far each leaves you from ready, which tiles it needs, how many are live (4 minus
  what you can see) and what a win would score. It looks one draw ahead when you're one
  step from ready.
- **Jev** (`mahjong_coach/jev.py`): gets the table state plus the engine's numbers for each move
  and answers one *Choice* question: which move should the player make? Jev only chooses
  between moves the engine has already checked, so it can't suggest an illegal play. The
  app shows Jev's pick, its confidence, and whether the engine agrees.
- **No key, or Jev unreachable?** The engine's own ranking is used and the app says so.

## Run it

Needs Python 3.10+. There are no other dependencies.

```bash
git clone https://github.com/moobshake-git/mahjong-ai.git
cd mahjong-ai
python -m mahjong_coach serve
```

That opens http://localhost:8000. To use it from your phone on the same Wi-Fi, run
`python -m mahjong_coach serve --host 0.0.0.0` and open `http://<your-computer's-IP>:8000`.

### Turn Jev on

Get an API key from TypeSafe AI and set it in the environment before starting the server.
Never commit it; `.env` files are already git-ignored.

```bash
# macOS / Linux / Git Bash
export JEV_API_KEY="sk_..."
# Windows PowerShell
$env:JEV_API_KEY = "sk_..."
```

Optional: `JEV_MODEL` (default `typesafe/jev-1.14`) and `JEV_API_URL`
(default `https://thejevai.com/v1/systemone`). The key stays on the server and is never
sent to the browser.

### Using the page

- Tap tiles to fill **My hand** (13 tiles). When it's full, the input switches to **I drew**.
- Use **Thrown** for another player's discard. Tick *from player on my left* to allow a chow.
- Add discards and other players' melds under **Seen elsewhere** so live counts stay accurate.
- Add your flowers, seasons and animals under **Flowers / animals**.
- The buttons on each suggestion (Discard, Pong, Pass, ...) update the hand for you. **Undo**
  reverts any change.

### Command line

```bash
python -m mahjong_coach advise "123m456p789s11z13s" --drawn 4z
python -m mahjong_coach advise "34m456p789s55z666s" --thrown 5z
python -m mahjong_coach advise "1m" --meld pung:5z --meld pung:6z --meld chow:1p --meld chow:4p --thrown 1m
```

Tile notation: `1m-9m` Characters, `1p-9p` Dots, `1s-9s` Bamboo, `1z-7z` = East, South,
West, North, White, Green, Red (or the words `E S W N Wh G R`). Bonus tiles: `f1-f4`,
`s1-s4`, `cat rat rooster centipede`. Add `--no-jev` to use only the engine, or `--json`
for the raw result.

## Scoring (Singapore)

The defaults are a 1 tai minimum and a 5 tai cap. Payouts double per tai: on a discard win the
discarder pays 2^tai units and the others pay half that; on a self-drawn win everyone pays 2^tai.
The minimum and cap can be changed in the app.

| Pattern | Tai |
|---|---|
| Dragon pung (each) | 1 |
| Seat wind pung / prevailing wind pung | 1 each (2 if both) |
| Seat flower or seat season (each) | 1 |
| Complete set of 4 flowers or 4 seasons | 2 |
| Animal (each) / all four animals | 1 / 5 |
| Fully concealed, self-drawn | 1 |
| Ping hu (all chows, non-scoring pair, two-sided wait, no bonus tiles) | 4 |
| Lesser ping hu (same, but with bonus tiles) | 1 |
| All pungs | 2 |
| Half flush / Full flush | 2 / 4 |
| Small three dragons / Big three dragons | 3 / 5 |
| Small four winds | 4 |
| Mixed terminals | 5 |
| All honours, Hidden treasure (4 concealed pungs), Big four winds | limit |

House tables vary, so please check these values against how you play. They live in
`mahjong_coach/scoring.py`.

## Not in this version

- Advice after the hand ends (only the turn is covered).
- A strategy or rules library.
- Rare and situational hands: Thirteen Wonders, Nine Gates, winning on a kong or flower
  replacement, robbing the kong, last tile, Heavenly/Earthly hands, Seven Pairs.
- Declaring a concealed kong on your own turn, or the extra kong on a pung you already called.

## Open questions (from the brief)

- Is today's ~15 minutes per round or per turn? The target is 10-15 seconds per turn.
- Do new players actually ask for help mid-turn? The paired test hasn't been run.
- Which house table the group plays. The Singapore defaults above are a starting point.

## Develop

```bash
python -m unittest discover -s tests -t .
```

## Working together

1. Clone: `git clone https://github.com/moobshake-git/mahjong-ai.git`
2. Make a branch for each change: `git checkout -b my-change`
3. Push it and open a pull request so the other person can review before merging into `main`.
