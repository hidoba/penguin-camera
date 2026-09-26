"""Button pictograms for the camera screen (update 26).

24x24 rounded squares (white outline, black fill) with a 16x16 pictogram inside,
next to the four left buttons: curve (OK), switch camera, menu, dithering.
Stored as 4-bit maps (low nibble first, row-major); nibble -> palette index via
TABLE, 15 = leave the pixel alone. Palette (stock resource 62, RGB565 + alpha):
250 black, 242/241/246/240/2 grays 49/81/117/162/194, 251 white.
The curve pictogram is plotted live from the selected curve's 256-entry LUT.
"""
import math

TABLE = (250, 242, 241, 246, 240, 2, 251) + (0,)*8 + (0,)     # nibble 15 = skip
SKIP, BLACK, WHITE = 15, 0, 6
SIZE, INNER, MARGIN = 24, 16, 4


def frame():
    """24x24 rounded square: corners transparent, 1-px white outline, black fill."""
    inset = [3, 1, 1] + [0]*18 + [1, 1, 3]
    inside = lambda x, y: 0 <= y < SIZE and inset[y] <= x < SIZE-inset[y]
    out = []
    for y in range(SIZE):
        for x in range(SIZE):
            if not inside(x, y): out.append(SKIP)
            elif all(inside(x+dx, y+dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))): out.append(BLACK)
            else: out.append(WHITE)
    return out


CAMERA_ART = (
    "................",
    "......#####.....",
    "....##.....##...",
    "...#.........#..",
    "...#.........#..",
    "...#.........#..",
    "...........#####",
    "............###.",
    "...#.........#..",
    "..###...........",
    ".#####..........",
    "...#.........#..",
    "...#.........#..",
    "....##.....##...",
    "......#####.....",
    "................")


def switch_camera():
    """Two circular arrows (iPhone-style camera flip): top arc ends in a downward
    arrowhead on the right, bottom arc in an upward arrowhead on the left."""
    assert all(len(r) == INNER for r in CAMERA_ART) and len(CAMERA_ART) == INNER
    return [WHITE if c == '#' else BLACK for row in CAMERA_ART for c in row]


def menu():
    px = [[BLACK]*INNER for _ in range(INNER)]
    for y0 in (3, 7, 11):
        for y in (y0, y0+1):
            for x in range(2, 14): px[y][x] = WHITE
    return [v for row in px for v in row]


def gradient(dithered):
    """Black -> white ramp: smooth (7 palette grays) or 4x4 Bayer dithered."""
    bayer = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)
    px = [[BLACK]*INNER for _ in range(INNER)]
    for y in range(2, 14):
        for x in range(1, 15):
            t = (x-1)/13
            if dithered: px[y][x] = WHITE if t*16 > bayer[(y % 4)*4+x % 4]+0.5 else BLACK
            else: px[y][x] = round(t*6)
    return [v for row in px for v in row]


def _art(rows, mapping):
    return [mapping[c] for row in rows for c in row]


# Update 28 ------------------------------------------------------------------
UPDOWN_ART = (                     # 12x16, bottom bar after the dither name (white on bar)
    ".....##.....",
    "....####....",
    "...######...",
    "..########..",
    ".##########.",
    "............",
    "............",
    "............",
    "............",
    "............",
    "............",
    ".##########.",
    "..########..",
    "...######...",
    "....####....",
    ".....##.....")
TRI_UP_ART = (                     # 16x9, right edge over the picture: white, black outline
    "......oooo......",
    ".....o####o.....",
    "....o######o....",
    "...o########o...",
    "..o##########o..",
    ".o############o.",
    "o##############o",
    "o##############o",
    "oooooooooooooooo")
FRAME_ART = (                      # 16x16 inside the rounded square: a framed landscape
    "................",
    ".##############.",
    ".#............#.",
    ".#........##..#.",
    ".#.......####.#.",
    ".#........##..#.",
    ".#............#.",
    ".#.....#......#.",
    ".#....###.....#.",
    ".#...#####..#.#.",
    ".#..###########.",
    ".#.############.",
    ".##############.",
    ".#............#.",
    ".##############.",
    "................")
UPDOWN_W, UPDOWN_H, TRI_W, TRI_H = 12, 16, 16, 9
PAINTING_W = PAINTING_H = 28
WAVY_FRAME_ART = (                 # 28x28 (update 29b): wavy picture frame, open centre
    "..###..................###..",
    ".######..####..####..######.",
    "#######.############.#######",
    "############################",
    "############################",
    ".##########################.",
    ".##########################.",
    "...######################...",
    "..######............######..",
    ".#######............#######.",
    ".#######............#######.",
    ".#######............#######.",
    ".#######............#######.",
    "..######............######..",
    "..######............######..",
    ".#######............#######.",
    ".#######............#######.",
    ".#######............#######.",
    ".#######............#######.",
    "..######............######..",
    "...######################...",
    ".##########################.",
    ".##########################.",
    "############################",
    "############################",
    "#######.############.#######",
    ".######..####..####..######.",
    "..###..................###..",
)


def painting():
    """Update 29b: wavy picture frame (user reference): white body, 1-px black outline,
    open centre and outside left untouched (the photo shows through)."""
    N = PAINTING_W
    inside = lambda x, y: 0 <= x < N and 0 <= y < N and WAVY_FRAME_ART[y][x] == '#'
    px = []
    for y in range(N):
        for x in range(N):
            if not inside(x, y): px.append(SKIP)
            elif all(inside(x+dx, y+dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))): px.append(WHITE)
            else: px.append(BLACK)
    return px


def updown():
    return _art(UPDOWN_ART, {'.': SKIP, '#': WHITE})


def tri(up):
    rows = TRI_UP_ART if up else TRI_UP_ART[::-1]
    return _art(rows, {'.': SKIP, '#': WHITE, 'o': BLACK})


def pack(pixels):
    return bytes(pixels[i] | pixels[i+1] << 4 for i in range(0, len(pixels), 2))


ICONS = {'frame': frame(), 'camera': switch_camera(), 'menu': menu(),
         'dither_on': gradient(True), 'dither_off': gradient(False)}


ICONS2 = {'updown': updown(), 'tri_up': tri(True), 'tri_down': tri(False), 'painting': painting()}


def blob2():
    """Second icon block (update 28): packed icons; same TABLE (in the first block)."""
    data = b''; offs = {}
    for name, px in ICONS2.items():
        assert len(px) % 2 == 0
        offs[name] = len(data); data += pack(px)
    return data, offs


def blob():
    """TABLE (16) + packed icons; returns (bytes, {name: offset})."""
    data = bytes(TABLE); offs = {}
    for name, px in ICONS.items():
        offs[name] = len(data); data += pack(px)
    return data, offs


def curve_points(lut):
    """16 columns: y = 15 - (lut[i*17] >> 4), each column filled to the previous y."""
    ys = [15-(lut[i*17] >> 4) for i in range(INNER)]
    return [(i, min(ys[i], ys[i-1] if i else ys[i]), max(ys[i], ys[i-1] if i else ys[i])) for i in range(INNER)]


def ascii(px, w):
    ch = {SKIP: ' ', 0: '.', 1: '1', 2: '2', 3: '3', 4: '4', 5: '5', 6: '#'}
    return '\n'.join(''.join(ch[v] for v in px[i:i+w]) for i in range(0, len(px), w))
