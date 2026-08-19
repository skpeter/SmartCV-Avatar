# SmartCV-Avatar

SmartCV-Avatar is a tool that reports live match data for **Avatar Legends: The Fighting Game** without installing mods on your game, and without needing a powerful PC to do it.

It works by sampling a handful of pixels and small pixel regions per frame to recognise what the game is showing. Round wins, game wins and every screen transition are detected this way; the only text ever read is the two character names on the versus screen, once per set. See the **How does it work?** section for details.

## Requirements
- [OBS (optional if streaming)](https://obsproject.com/download)
- Your copy of Avatar Legends must be in **English**.
- The game must be rendered at 16:9. Detection is calibrated at 1920x1080 and scaled from there.

## Step 1: Installation
- Follow either one of the two steps below:
### Step 1.1: Installing the CPU version
- Download the compiled release.zip [here](https://github.com/skpeter/SmartCV-Avatar/releases).
- You can skip to step 2 from here.
### Step 1.2: Installing the GPU version
- Download **source.zip** from the [latest release](https://github.com/skpeter/SmartCV-Avatar/releases/latest/download/source.zip). Do not use GitHub's auto-generated "Source code" zip — it is missing the `core` files.
- Install Python if you haven't done so already [here](https://www.python.org/downloads/). **Recommended version is 3.12**.
- Open a command prompt terminal in the installed directory and run `pip install -r core/requirements.txt`
- Then install PyTorch. Go to PyTorch's "Start Locally" section [here](https://pytorch.org/get-started/locally/), pick the **Stable** build, your OS, **Pip**, **Python**, and the **Compute Platform** your GPU supports. PyTorch weighs around 3GB, so take your time.

## Step 2: Setup
- Follow either one of the two steps below:
### Step 2.1: Game Setup
If the game runs on the same device as SmartCV you do not need to do anything else. Open the game alongside SmartCV and the window is detected automatically.
### Step 2.2: OBS Setup
SmartCV reads the game from an OBS video source over OBS WebSocket. **Enable and configure OBS WebSocket first.** Open `config.ini`, set `source_title` to the name of your OBS source, and fill in the rest of the `[obs]` section. `width` and `height` are the resolution OBS sends; you can lower it to save CPU as long as it stays 16:9, though detection gets less reliable the lower you go.

### Step 2.3: Replaying a video file
Set `capture_mode = video`, point `[video] path` at a recording, and launch with `python vod.py` instead of the normal script. This runs the real detection loop and websocket server against the file, which is the easy way to test a client integration without playing a match:

```bash
python vod.py path/to/vod.mp4
python vod.py path/to/vod.mp4 --start 400 --speed 4
```

`--speed` only changes wall-clock pacing; the video still advances one `refresh_rate` per poll, so the detectors see the same cadence they would live.

### Broadcast overlays
If your stream layout draws a scoreboard over the game, declare it so the calibration check can guarantee no probe sits underneath it:

```ini
ignore_region = 274,0,1647,60
```

The default is the ICFC tournament scoreboard (`x 274-1647, y 0-60`). No probe currently falls inside that box — the closest is the health bar tip at `y 68`. If your overlay is taller than 60px at 1080p, run `python dev/validate_vod.py --probes` and it will tell you which probes collide.

## Step 3: Usage
- To run the GPU/source version, open a launch script: `smartcv.bat` (Windows), `smartcv.sh` (Linux), `smartcv.command` (macOS). On a git clone these live under `core/`. CPU/release: `smartcv.exe`.

**From here just follow the on-screen instructions. Detection starts from the character select screen.**
**If using OBS, keep it open and do not disable the game capture source.**

## Where do I use this?
SmartCV opens a websocket server (port 6565 by default) to send data to.
As of this writing only [S.M.A.R.T.](https://skpeter.github.io/smart-user-guide) integrates with it. To integrate SmartCV into your own app, look at `example-json.json` for the data shape.

## How does it work?

### Match structure
A **game** is first to 2 rounds. A **set** is a run of games. The versus screen only appears once per set — games inside a set run back to back with a short fade and no character select — so the versus screen is what resets the set score.

### Round wins
Two slots flank the match timer plate, symmetric about the screen centre. The left pair belongs to player 1, the right pair to player 2; the upper slot is round 1 and the lower is round 2. A slot lights up the instant the finishing blow lands and stays lit for the rest of the game.

Player 1's slots light saturated **orange**, player 2's saturated **blue**, and each region average is matched against that exact colour. This is also the K.O. detector: the second slot lights several seconds before the HUD tears down, which is far more margin than the `FINISH` glyph gives, and it names the winner for free.

Matching the colour exactly matters more than it sounds. A looser "is this warm?" test is what a first pass naturally reaches for, and it awards phantom rounds constantly, because a firebending super, a waterbending super, a round-intro flash and Kyoshi's tan war fan all park bright colour over the marker slots. Against the measured lit orange the fan misses on red and the intro flash misses on blue.

### The HUD gate
Marker reads are only trusted when the HUD is verifiably on screen, tested two ways at once:

- the timer plate interior must be **dark** — rejects full-screen effects washing over the HUD
- the gold dividers beside the plate must be **bright** — rejects fades to black

Either test alone passes frames where the HUD is gone. Together they cleanly separate every occluded frame in the sample footage. When a frame is rejected the reading is simply skipped; since a lit slot stays lit for the rest of the game, waiting for a clean frame costs nothing. On top of the gate, a reading has to repeat three polls running before it counts.

### Round starts
The health bars are slanted parallelograms that drain from the outer tip inward, so the outer tip is only coloured at full health. Both tips lit plus a visible HUD means a round just started. A short lock prevents the same round start firing twice while nobody has taken damage yet.

The round markers are deliberately **not** read at a round start: the divider is drawn a beat before the slot fills animate in, so a round 2 start briefly reads 0-0. Whether a round start begins a new game is decided from whether the previous game was already awarded.

### Screens
Character select, versus and the set results screen are each identified by a small set of fixed probe points chosen to be stable across characters, cursor positions and stage backgrounds.

### Characters
The only OCR in the project. When the versus screen appears, the two nameplates are read once and fuzzy-matched against the roster in `avatar.py`. Each character also has one of three supports, shown on the line below the name; supports change the moveset but are not reported.

## Development

There are two ways to run footage through this, and they answer different questions.

`vod.py` is the product: real detection loop, real websocket server, frames from a file. Use it to check what a client receives.

`dev/validate_vod.py` is the calibration and regression tool. It drives the detectors directly, with no server or sleeping, and prints every state change:

```bash
python dev/validate_vod.py path/to/vod.mp4 --start 400 --end 900
python dev/validate_vod.py path/to/vod.mp4 --probes 411.5   # dump probe values for one frame
python dev/validate_vod.py path/to/vod.mp4 --ocr            # include nameplate OCR
```

`--probes` also asserts that no probe point sits inside `ignore_region`.

## Known Issues
- Only the main character is reported; supports are parsed but discarded.
- Player names are not on screen in local play, so they arrive from the confirmation dialog or an external source rather than from the capture.
- Detection assumes the game fills the captured frame, which is what you get capturing the game window or a dedicated OBS game source. A broadcast layout that insets or shifts the gameplay to make room for side panels moves the HUD off the calibrated coordinates. This fails safe: the HUD gate stops matching and nothing is reported, rather than reporting something wrong.
- Returning to character select is treated as the start of a new set, which resets the game count. Players sometimes revisit character select between games of the same tournament set to change supports, and SmartCV cannot tell that apart from a genuinely new set. Bracket state belongs to the consuming app.

## Check out also:
- [SmartCV-SF6 for Street Fighter 6](https://github.com/skpeter/SmartCV-SF6)
- [SmartCV-Tokon for MARVEL Tokon: Fighting Souls](https://github.com/skpeter/SmartCV-Tokon)
- [SmartCV-SSBU for Super Smash Bros. Ultimate](https://github.com/skpeter/SmartCV-SSBU)
- [SmartCV-GGST for Guilty Gear -STRIVE-](https://github.com/skpeter/SmartCV-GGST)
- [SmartCV-RoA2 for Rivals of Aether II](https://github.com/skpeter/SmartCV-RoA2)

## Contact

[I am mostly available on my team's Discord Server if you'd like to talk about SmartCV or have any additional questions.](https://discord.gg/zecMKvF8b5)
