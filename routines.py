import configparser
import time
import avatar
import numpy as np
import core.core as core
from core.matching import findBestMatch

client_name = "smartcv-avatar"
config = configparser.ConfigParser()
config.read("config.ini")
previous_states = [None]

# Wall-clock source. VOD harness replaces this with video timestamp.
_now = time.time
ocr_enabled = True

payload = {
    "state": None,
    "round": 0,
    "players": [
        {
            "name": None,
            "character": None,
            "games": 0,
            "rounds": 0,
        },
        {
            "name": None,
            "character": None,
            "games": 0,
            "rounds": 0,
        },
    ],
}

# A game is first-to-2 rounds. A set is a run of games. Games inside a set
# usually run back to back behind a short fade with no character select, so
# there is no screen that announces a new game; it is inferred from a round
# start arriving after a game was already awarded.
_round_start_lock_until = 0.0
_game_awarded = False
_marker_streak = [None, 0]

# Broadcast overlays sit on top of the capture. Anything inside this box is
# off limits for probes; validate_vod asserts it. Default is the ICFC
# tournament scoreboard.
IGNORE_REGION = tuple(
    int(v) for v in config.get(
        "settings", "ignore_region", fallback="274,0,1647,60"
    ).split(",")
)

# Two slots flank the timer plate, symmetric about x=959: P1 left, P2 right.
# Upper slot is round 1, lower is round 2. They latch on for the rest of the
# game. P1 lights saturated orange, P2 saturated blue.
#
# Slot interiors are x 889-901 / 1017-1028, y 82-107 and y 112-137. The
# round 2 box deliberately stops at y=124: an orange meter glow climbs from
# below y=125 and false-triggers any taller box.
#
# Match the lit colour exactly rather than testing "is it warm". Warm is far
# too broad: Kyoshi's fan super parks a tan (188,163,121) over both P1 slots
# and a round-intro flash puts (219,130,27) there, and both clear any
# channel-difference threshold low enough to accept the real marker. Against
# the measured lit colour the fan misses on red and the flash on blue.
# Sampled over five stages the lit orange holds (214..237, 118..140, 79..91),
# so a 0.10 budget is roughly double the observed spread.
P1_ROUND_SLOTS = ((891, 88, 902, 104), (891, 114, 902, 124))
P2_ROUND_SLOTS = ((1018, 88, 1029, 104), (1018, 114, 1029, 124))
MARKER_P1_COLOR = (222, 126, 86)
MARKER_P2_COLOR = (136, 145, 238)
MARKER_DEV = 0.10
MARKER_STREAK = 3

# HUD-present gate, and the single most important guard in this file.
# Firebending and waterbending supers wash the whole screen, HUD included,
# in saturated orange or blue, which lights every marker slot at once. A
# brightness-only gate passes those frames and hands back scores like 2-0
# or 0-2 out of nowhere, so two opposing tests are needed:
#
#   the timer plate interior must be dark   - kills bright washouts
#   the gold dividers must be bright        - kills fades to black
#
# Measured over the sample VOD: with the HUD up the plate strip sums 42-59
# and the dividers 608-725; every occluded frame fails one side or the other
# (super 253, water 564, fade 0 with dividers at 3).
HUD_DIVIDERS = ((890, 108, 902, 112), (1018, 108, 1030, 112))
HUD_DIVIDER_MIN_SUM = 420
PLATE_INTERIOR = (910, 62, 1000, 72)
PLATE_DARK_MAX_SUM = 90

# Outer tips of the health bars. The bars are slanted parallelograms that
# deplete from the outer tip inward, so a lit tip means ~100% health and is
# therefore a round start. P1 is red, P2 is blue.
HP_P1 = (275, 68, 300, 80)
HP_P2 = (1635, 68, 1660, 80)
ROUND_START_LOCK = 12.0

# Versus screen: PLAYER 1 maroon band, PLAYER 2 navy band, cream VS brush.
# Only up for 3-4s, which is still 6-8 polls at the default refresh rate.
VS_PROBES = (
    ((529, 97), (95, 50, 41)),
    ((1368, 114), (64, 73, 118)),
    ((934, 863), (231, 226, 214)),
)
VS_DEV = 0.12

# Character select: two points on the fixed roster grid art, one on the grid
# surround, one on the cream band under it. All four hold while the cursors
# move and on the control-config variant of the screen.
CSS_PROBES = (
    ((790, 492), (216, 89, 60)),
    ((817, 447), (253, 215, 156)),
    ((1140, 426), (112, 141, 183)),
    ((967, 603), (235, 223, 205)),
)
CSS_DEV = 0.12

# Set results screen: red and blue brush bars behind the REMATCH entries.
# Both required; either one alone shows up on other menus.
RES_RED = (80, 768, 330, 795)
RES_BLUE = (1570, 768, 1830, 795)
RES_RED_COLOR = (144, 68, 39)
RES_BLUE_COLOR = (45, 56, 165)
RES_DEV = 0.10

# Versus nameplates. Character name is the top line; the line below is the
# support, which does not affect reported data.
P1_NAME_RECT = (150, 792, 440, 62)
P2_NAME_RECT = (1330, 792, 440, 62)

# The nameplate font is stylised and sits over animated art, so a single
# read is not trustworthy: ZUKO has come back as ALKLO, ALKO, LUKO and
# ALUKO on consecutive frames of the same versus screen. Measured over the
# sample VOD, every correct read scored 0.783 or better and every wrong or
# stray read scored 0.695 or worse, so the floor sits between them. Reads
# keep coming for the length of the versus screen and the best score wins,
# which is what turns ALKLO -> Azula (0.64, wrong) into LUKO -> Zuko (0.83).
CHAR_MIN_SCORE = 0.75
CHAR_LOCK_SCORE = 0.95
_char_scores = [0.0, 0.0]


def _debug():
    return config.getboolean("settings", "debug_mode", fallback=False)


def _set_state(payload, state):
    payload["state"] = state
    if previous_states[-1] != state:
        previous_states.append(state)


def _as_rgb(img):
    arr = np.asarray(img)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        return arr[:, :, :3]
    return arr


def _px(img, x, y, scale_x, scale_y):
    arr = _as_rgb(img)
    sx = min(max(int(x * scale_x), 0), arr.shape[1] - 1)
    sy = min(max(int(y * scale_y), 0), arr.shape[0] - 1)
    return tuple(int(v) for v in arr[sy, sx])


def _region_mean(img, box, scale_x, scale_y):
    x0, y0, x1, y1 = box
    arr = _as_rgb(img)
    xa, xb = int(x0 * scale_x), int(x1 * scale_x)
    ya, yb = int(y0 * scale_y), int(y1 * scale_y)
    xa, xb = sorted((max(xa, 0), min(xb, arr.shape[1])))
    ya, yb = sorted((max(ya, 0), min(yb, arr.shape[0])))
    if xb <= xa or yb <= ya:
        return (0.0, 0.0, 0.0)
    return tuple(float(v) for v in arr[ya:yb, xa:xb].reshape(-1, 3).mean(0))


def _probes_match(img, probes, deviation, scale_x, scale_y):
    return all(
        core.is_within_deviation(
            _px(img, x, y, scale_x, scale_y), color, deviation
        )
        for (x, y), color in probes
    )


def _hud_visible(img, scale_x, scale_y):
    if sum(_region_mean(img, PLATE_INTERIOR, scale_x, scale_y)) > PLATE_DARK_MAX_SUM:
        return False
    return all(
        sum(_region_mean(img, box, scale_x, scale_y)) > HUD_DIVIDER_MIN_SUM
        for box in HUD_DIVIDERS
    )


def _read_markers(img, scale_x, scale_y):
    p1 = sum(
        core.is_within_deviation(
            _region_mean(img, box, scale_x, scale_y), MARKER_P1_COLOR, MARKER_DEV
        )
        for box in P1_ROUND_SLOTS
    )
    p2 = sum(
        core.is_within_deviation(
            _region_mean(img, box, scale_x, scale_y), MARKER_P2_COLOR, MARKER_DEV
        )
        for box in P2_ROUND_SLOTS
    )
    return int(p1), int(p2)


def _stable_markers(img, scale_x, scale_y):
    """Markers only count once the same reading repeats MARKER_STREAK times.

    Belt and braces on top of the HUD gate. A lit slot stays lit for the
    rest of the game, tens of seconds, so demanding 1.5s of agreement costs
    nothing real while discarding any flash the gate happens to let through.
    """
    reading = _read_markers(img, scale_x, scale_y)
    if _marker_streak[0] == reading:
        _marker_streak[1] += 1
    else:
        _marker_streak[0] = reading
        _marker_streak[1] = 1
    if _debug():
        print("Round markers:", reading, "streak", _marker_streak[1])
    return reading if _marker_streak[1] >= MARKER_STREAK else None


def _health_full(img, scale_x, scale_y):
    p1 = _region_mean(img, HP_P1, scale_x, scale_y)
    p2 = _region_mean(img, HP_P2, scale_x, scale_y)
    if _debug():
        print("Health tip means:", p1, p2)
    p1_full = p1[0] > 90 and (p1[0] - p1[1]) > 50
    p2_full = p2[2] > 120 and (p2[2] - p2[0]) > 40
    return p1_full and p2_full


def _reset_game(payload):
    """New game inside the same set. Characters and games carry over."""
    global _game_awarded
    payload["round"] = 0
    for player in payload["players"]:
        player["rounds"] = 0
    _game_awarded = False


def _reset_set(payload):
    global _game_awarded
    payload["round"] = 0
    for player in payload["players"]:
        player["games"] = 0
        player["rounds"] = 0
        player["character"] = None
    _char_scores[0], _char_scores[1] = 0.0, 0.0
    _game_awarded = False


def detect_character_select_screen(payload, img, scale_x, scale_y):
    if not _probes_match(img, CSS_PROBES, CSS_DEV, scale_x, scale_y):
        return
    if payload["state"] == "character_select":
        return
    core.print_with_time("- Character select screen detected")
    _reset_set(payload)
    for player in payload["players"]:
        player["name"] = None
    _set_state(payload, "character_select")


def detect_versus_screen(payload, img, scale_x, scale_y):
    if not _probes_match(img, VS_PROBES, VS_DEV, scale_x, scale_y):
        return
    if payload["state"] != "loading":
        core.print_with_time("- Versus screen detected (new set)")
        _reset_set(payload)
        _set_state(payload, "loading")
    detect_characters(payload, img, scale_x, scale_y)


def detect_characters(payload, img, scale_x, scale_y):
    """One OCR pass per set, on the versus nameplates.

    Everything else in this file is pixel work; this is the only text read,
    and it stops as soon as both names land.
    """
    if not ocr_enabled:
        return
    if min(_char_scores) >= CHAR_LOCK_SCORE:
        return
    if not _probes_match(img, VS_PROBES, VS_DEV, scale_x, scale_y):
        return
    for i, rect in enumerate((P1_NAME_RECT, P2_NAME_RECT)):
        if _char_scores[i] >= CHAR_LOCK_SCORE:
            continue
        x, y, w, h = (
            int(rect[0] * scale_x),
            int(rect[1] * scale_y),
            int(rect[2] * scale_x),
            int(rect[3] * scale_y),
        )
        result = core.read_text(img, (x, y, w, h), contrast=2)
        if not result:
            continue
        match, score = findBestMatch(" ".join(result), avatar.characters)
        if _debug():
            print(f"Nameplate {i + 1} OCR:", result, "->", match, round(score, 3))
        if not match or score < CHAR_MIN_SCORE or score <= _char_scores[i]:
            continue
        _char_scores[i] = score
        if payload["players"][i]["character"] == match:
            continue
        payload["players"][i]["character"] = match
        core.print_with_time(
            f"{payload['players'][i]['name'] or f'Player {i + 1}'} as:", match
        )


def detect_round_start(payload, img, scale_x, scale_y):
    global _round_start_lock_until
    if _now() < _round_start_lock_until:
        return
    if not _hud_visible(img, scale_x, scale_y):
        return
    if not _health_full(img, scale_x, scale_y):
        return

    # Do not read the markers here. The divider is drawn a beat before the
    # slot fills animate in, so a round 2 start reads 0-0 for about a second
    # and would look like a fresh game. A new game can only follow a game
    # that ended, so use the award flag; the rounds >= 2 arm is a fallback
    # for a game end that was somehow missed.
    players = payload["players"]
    if _game_awarded or players[0]["rounds"] >= 2 or players[1]["rounds"] >= 2:
        if players[0]["rounds"] or players[1]["rounds"]:
            core.print_with_time("- New game in set")
        _reset_game(payload)

    _round_start_lock_until = _now() + ROUND_START_LOCK
    _marker_streak[0], _marker_streak[1] = None, 0
    payload["round"] = players[0]["rounds"] + players[1]["rounds"] + 1
    core.print_with_time(f"Round {payload['round']} starting")
    _set_state(payload, "in_game")
    detect_characters(payload, img, scale_x, scale_y)


def detect_rounds(payload, img, scale_x, scale_y):
    """Round wins and game end, straight off the marker slots.

    The second marker lights the instant the finishing blow lands and stays
    lit for 5s or more before the HUD tears down, so this doubles as the K.O.
    detector; there is no need to chase the FINISH glyph.
    """
    global _game_awarded
    if payload["state"] != "in_game":
        return
    if not _hud_visible(img, scale_x, scale_y):
        return
    reading = _stable_markers(img, scale_x, scale_y)
    if reading is None:
        return
    p1, p2 = reading

    players = payload["players"]
    for i, count in enumerate((p1, p2)):
        if count > players[i]["rounds"]:
            players[i]["rounds"] = count
            core.print_with_time(
                "K.O. -",
                players[i]["character"] or f"Player {i + 1}",
                f"wins round ({players[0]['rounds']}-{players[1]['rounds']})",
            )

    if _game_awarded:
        return
    if players[0]["rounds"] < 2 and players[1]["rounds"] < 2:
        return
    winner = 0 if players[0]["rounds"] >= 2 else 1
    players[winner]["games"] += 1
    _game_awarded = True
    core.print_with_time(
        f"{players[winner]['character'] or f'Player {winner + 1}'} wins the game "
        f"({players[0]['rounds']}-{players[1]['rounds']})  "
        f"set {players[0]['games']}-{players[1]['games']}"
    )
    payload["round"] = 0
    _set_state(payload, "game_end")


def detect_results(payload, img, scale_x, scale_y):
    """Set results screen. Games are already counted from the markers, so
    this only marks the set as finished."""
    red = _region_mean(img, RES_RED, scale_x, scale_y)
    blue = _region_mean(img, RES_BLUE, scale_x, scale_y)
    if _debug():
        print("Results brush means:", red, blue)
    if not (
        core.is_within_deviation(red, RES_RED_COLOR, RES_DEV)
        and core.is_within_deviation(blue, RES_BLUE_COLOR, RES_DEV)
    ):
        return
    if payload["state"] == "game_end":
        return
    core.print_with_time("- Results screen detected (set over)")
    payload["round"] = 0
    _set_state(payload, "game_end")


states_to_functions = {
    None: [
        detect_character_select_screen,
        detect_versus_screen,
        detect_round_start,
    ],
    "character_select": [detect_versus_screen, detect_round_start],
    "loading": [detect_characters, detect_round_start],
    "in_game": [
        detect_rounds,
        detect_round_start,
        detect_character_select_screen,
        detect_results,
    ],
    "game_end": [
        detect_round_start,
        detect_character_select_screen,
        detect_versus_screen,
        detect_results,
    ],
}
