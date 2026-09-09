#!/usr/bin/env python3
"""Emit the two README banners, the favicon both front ends use, and the web
GUI's one remaining raster.

    python3 assets/build_banners.py

An SVG loaded as an image (which is what the README does, through GitHub's camo
proxy) runs in secure static mode and cannot fetch external resources, so
<image href="silica-mark.svg"/> would render nothing. The mark has to live
inside each banner as a copy, which is why the banners are generated and not
hand-edited: keeping two copies of it in sync by hand is how they drift apart.

The banner is the lockup and nothing else: no plate, no ground, no strapline.
It is the small-size cut of the mark set beside the wordmark at the same
height, on transparent, so it sits on whatever page embeds it instead of
carrying its own rectangle everywhere it travels.

The favicon is one file copied to the two places that serve one, rather than
three hand-kept copies of the same drawing. It is the mark's small-size cut,
not the mark: the full one carries enough nested rings to turn to mush at 16px.

The chat empty state still comes from the mascot PNG, because Sili survives
there and not as the hero. That needs Pillow, which is not a silica dependency,
because this script is run by hand on the one machine that edits the art.
"""

import re
from pathlib import Path

HERE = Path(__file__).parent
MARK = "silica-mark.svg"
MASCOT_PNG = "sili_mascot.png"
STATIC = HERE.parent / "silica" / "ui" / "web" / "static"
# (file, width) - the chat empty state, and nothing else now
RASTERS = (("sili.webp", 360),)
# one drawing per destination that needs it, copied rather than kept by hand:
# the small cut for both tabs, the full mark for the site's social card
FAVICON = "silica-mark-favicon.svg"
WEB = HERE.parent / "web"
ART_COPIES = ((FAVICON, STATIC / "favicon.svg"), (FAVICON, WEB / "favicon.svg"),
              (MARK, WEB / "silica-mark.svg"))

# The lockup, measured rather than eyeballed. The mark's hexagon fills 78.5% of
# its own 512 box, so a 96 box draws it 75 tall against a 63 cap height: the
# logo reads level with the wordmark instead of towering over it.
MARK_SIZE = 96
GAP = 26
PAD_X, PAD_Y = 28, 26

# Lato Light at 100 units, so the wordmark is 71.5 tall from baseline to cap and
# its ink runs x 4.5 to 368.1 (measured off the path below, not off a font).
WORD_SCALE = 0.88
WORD_X0, WORD_X1, WORD_CAP = 4.5, 368.1, 71.5

# "Core" rides the wordmark's baseline at a third of its cap and a word's
# distance away: the distribution's name qualifies the product's, so it reads as
# a suffix and not as the second half of the name. Set in another face for the
# same reason - the wordmark's own italic would rejoin the word it follows.
CORE_SCALE = 0.34
CORE_GAP = 18
CORE_X0, CORE_X1, CORE_CAP = 8.1, 205.9, 70.6

W = round(PAD_X * 2 + MARK_SIZE + GAP + (WORD_X1 - WORD_X0) * WORD_SCALE
          + CORE_GAP + (CORE_X1 - CORE_X0) * CORE_SCALE)
H = MARK_SIZE + PAD_Y * 2
# mark and wordmark share one centre line, so neither sits high
MARK_X = PAD_X
TEXT_X = PAD_X + MARK_SIZE + GAP - WORD_X0 * WORD_SCALE
TEXT_Y = H / 2 + WORD_CAP * WORD_SCALE / 2
CORE_X = TEXT_X + WORD_X1 * WORD_SCALE + CORE_GAP - CORE_X0 * CORE_SCALE

# Lato Light @ 100px, tracking 0.16em, extracted with fontTools SVGPathPen so it
# never falls back to Arial on someone else's machine. Light and not Black: the
# mark is hairline art, and a heavy wordmark reads as a different brand sharing
# the canvas.
WORDMARK = "M44.8 -62.2Q44.4 -61.3 43.5 -61.3Q42.9 -61.3 41.8 -62.2Q40.8 -63.2 39.1 -64.3Q37.3 -65.4 34.7 -66.3Q32.1 -67.3 28.2 -67.3Q24.4 -67.3 21.4 -66.2Q18.5 -65.1 16.5 -63.2Q14.6 -61.3 13.5 -58.8Q12.5 -56.3 12.5 -53.6Q12.5 -50 14 -47.6Q15.6 -45.2 18.1 -43.6Q20.6 -42 23.7 -40.8Q26.9 -39.7 30.2 -38.6Q33.6 -37.5 36.8 -36.2Q40 -34.9 42.5 -32.9Q45 -30.9 46.5 -27.9Q48 -25 48 -20.7Q48 -16.2 46.5 -12.3Q45 -8.3 42.1 -5.5Q39.2 -2.6 35 -0.9Q30.8 0.8 25.4 0.8Q18.4 0.8 13.3 -1.7Q8.2 -4.2 4.5 -8.5L5.9 -10.7Q6.5 -11.4 7.2 -11.4Q7.7 -11.4 8.4 -10.8Q9.1 -10.2 10.1 -9.3Q11.1 -8.5 12.5 -7.4Q13.9 -6.4 15.8 -5.5Q17.6 -4.7 20 -4.1Q22.4 -3.5 25.5 -3.5Q29.7 -3.5 33 -4.7Q36.2 -6 38.5 -8.2Q40.8 -10.4 42 -13.4Q43.2 -16.4 43.2 -19.9Q43.2 -23.7 41.7 -26.1Q40.2 -28.5 37.7 -30.1Q35.1 -31.8 32 -32.9Q28.8 -34 25.5 -35Q22.1 -36.1 18.9 -37.4Q15.8 -38.7 13.2 -40.7Q10.8 -42.7 9.2 -45.7Q7.7 -48.8 7.7 -53.3Q7.7 -56.9 9.1 -60.2Q10.4 -63.5 13 -66Q15.6 -68.5 19.4 -70Q23.2 -71.5 28.2 -71.5Q33.6 -71.5 38 -69.8Q42.4 -68 46 -64.5Z M85.1 0H80V-70.8H85.1Z M160.7 -4.4V0H122V-70.8H127.2V-4.4Z M193.7 0H188.6V-70.8H193.7Z M281.7 -11.9Q282.2 -11.9 282.6 -11.6L284.6 -9.4Q282.4 -7.1 279.8 -5.2Q277.2 -3.3 274.1 -2Q271.1 -0.7 267.4 0.1Q263.7 0.8 259.3 0.8Q251.9 0.8 245.8 -1.8Q239.7 -4.4 235.3 -9.1Q230.9 -13.8 228.5 -20.5Q226 -27.2 226 -35.4Q226 -43.5 228.5 -50.1Q231 -56.8 235.5 -61.5Q240 -66.3 246.3 -68.9Q252.6 -71.5 260.2 -71.5Q267.5 -71.5 273.1 -69.3Q278.7 -67 283.3 -63L281.8 -60.7Q281.4 -60.1 280.5 -60.1Q279.9 -60.1 278.5 -61.2Q277.2 -62.3 274.8 -63.6Q272.4 -65 268.8 -66.1Q265.2 -67.2 260.2 -67.2Q253.8 -67.2 248.5 -65Q243.2 -62.8 239.4 -58.7Q235.5 -54.6 233.4 -48.7Q231.2 -42.8 231.2 -35.4Q231.2 -27.9 233.4 -22Q235.6 -16.1 239.4 -12Q243.2 -8 248.4 -5.8Q253.5 -3.6 259.6 -3.6Q263.4 -3.6 266.3 -4.1Q269.3 -4.6 271.8 -5.6Q274.3 -6.6 276.5 -8.1Q278.6 -9.5 280.7 -11.5Q280.9 -11.7 281.2 -11.8Q281.4 -11.9 281.7 -11.9Z M352.4 -25.7 338 -61.5Q337.2 -63.2 336.6 -65.7Q336.2 -64.5 335.9 -63.4Q335.6 -62.3 335.2 -61.4L320.8 -25.7ZM368.1 0H364.1Q363.4 0 363 -0.4Q362.5 -0.8 362.2 -1.4L354 -21.9H319.2L310.9 -1.4Q310.7 -0.8 310.2 -0.4Q309.7 0 309 0H305.1L334.1 -70.8H339.1Z"

# Ubuntu Sans Italic instanced at wght 300 @ 100px, tracking 0.02em, extracted
# the same way and for the same reason. A grotesque italic against the
# humanist caps: modern enough to read as a different voice, and at 300 it
# carries the wordmark's hairline weight instead of out-inking it at a third
# of the size. Cap 70.6, ink x 8.1 to 205.9.
CORE = "M33.1 1.3Q25.9 1.3 20.2 -1.4Q14.6 -4.2 11.3 -10Q8.1 -15.8 8.1 -24.9Q8.1 -35.7 11.3 -44.2Q14.5 -52.6 20.1 -58.5Q25.8 -64.4 32.9 -67.5Q40.1 -70.6 48 -70.6Q52.7 -70.6 56.8 -69.7Q60.9 -68.7 65.1 -66.4L62.3 -60.8Q58.7 -62.9 54.8 -63.9Q50.9 -64.9 46.9 -64.9Q40.6 -64.9 34.8 -62.1Q29 -59.4 24.5 -54.3Q20 -49.1 17.4 -41.8Q14.8 -34.5 14.8 -25.3Q14.8 -15.2 19.5 -9.9Q24.2 -4.5 33.2 -4.5Q39.9 -4.5 44.4 -5.9Q48.9 -7.2 51.7 -8.8L52.6 -3.1Q48.5 -0.9 43.4 0.2Q38.3 1.3 33.1 1.3Z M84.9 1Q80 1 76.1 -1Q72.2 -3.1 69.9 -7.1Q67.7 -11.1 67.7 -16.8Q67.7 -25.3 70.1 -32Q72.5 -38.6 76.6 -43.3Q80.6 -47.9 85.7 -50.3Q90.8 -52.8 96.2 -52.8Q101.2 -52.8 105 -50.7Q108.9 -48.5 111.1 -44.6Q113.4 -40.6 113.4 -35Q113.4 -26.5 111 -19.8Q108.6 -13.2 104.6 -8.5Q100.5 -3.9 95.4 -1.4Q90.3 1 84.9 1ZM85.3 -4.4Q88.5 -4.4 91.7 -5.8Q94.9 -7.1 97.7 -9.6Q100.5 -12.2 102.6 -15.8Q104.7 -19.5 106 -24.2Q107.2 -28.9 107.2 -34.5Q107.2 -40.7 104 -44Q100.8 -47.3 95.9 -47.3Q92.6 -47.3 89.4 -46Q86.3 -44.6 83.5 -42.1Q80.6 -39.5 78.5 -35.9Q76.3 -32.2 75.1 -27.5Q73.9 -22.7 73.9 -17.1Q73.9 -11 77.1 -7.7Q80.3 -4.4 85.3 -4.4Z M121.4 0 133.1 -49.3Q136.9 -50.8 141.3 -51.8Q145.7 -52.8 149.9 -52.8Q152.1 -52.8 154.3 -52.5Q156.6 -52.2 158.4 -51.5L156.8 -46.1Q154.9 -46.6 152.9 -46.9Q150.9 -47.2 148.8 -47.2Q146.2 -47.2 143.6 -46.8Q141 -46.4 138.4 -45.5L127.4 0Z M181.5 1Q174.4 1 170.2 -1.4Q165.9 -3.7 164 -8Q162 -12.2 162 -17.8Q162 -24.3 164 -30.5Q165.9 -36.7 169.7 -41.8Q173.5 -46.8 178.9 -49.8Q184.4 -52.8 191.4 -52.8Q198.5 -52.8 202.2 -49.2Q205.9 -45.6 205.9 -39.7Q205.9 -32.3 201.2 -28.4Q196.5 -24.4 188 -22.8Q179.5 -21.2 168.2 -20.8Q168.2 -20.3 168.2 -19.7Q168.1 -19.1 168.1 -18.8Q168.3 -14.1 169.5 -10.9Q170.8 -7.7 174 -6.1Q177.2 -4.5 182.7 -4.5Q186.3 -4.5 189.9 -5.5Q193.5 -6.4 196.6 -8.3L197.2 -2.8Q193.8 -1 189.8 0Q185.7 1 181.5 1ZM168.9 -26.2Q180.5 -26.5 187.2 -28Q194 -29.5 196.9 -32.3Q199.8 -35.1 199.8 -39.3Q199.8 -43.4 197.3 -45.4Q194.8 -47.4 190.9 -47.4Q185.4 -47.4 180.9 -44.6Q176.5 -41.8 173.4 -37Q170.4 -32.2 168.9 -26.2Z"

# The banner is embedded as an <img>, so currentColor has nothing to inherit and
# each theme states its own ink. `sig` is the centre crystal, the one emissive
# point: one hue at two stops, because no single chromatic value clears AA on
# both a near-white and a near-black ground. See docs/specs/visual-identity.md.
# The six child nodes are the graph's own community palette, not a set invented
# for the logo: the 212-306deg arc from `graph_export._community_color` at the
# same 0.66 saturation. Two things change, both because the count here is six
# and known rather than unbounded. The walk is even instead of golden-ratio,
# which is what the golden walk approximates when it cannot see the count. And
# there is one lightness band per ground, not two: the alternation exists to
# separate communities the arc has run out of room for, and six evenly spaced
# hues do not need it. It was also what broke the floor - at 0.54 the two
# violets measured 2.34:1 and 2.65:1 against the raised plane, under the 3:1
# WCAG 1.4.11 asks of a graphical object. One band at 0.66 dark / 0.56 paper
# puts the worst of the twelve at 3.66:1 and 3.51:1.
#
# The arc stops short of cyan on purpose, which is what leaves the centre
# crystal at 189deg as the only emissive point in the drawing.
NODES_DARK = ("#6FA4E2", "#6F87E2", "#756FE2", "#936FE2", "#B16FE2", "#CF6FE2")
NODES_LIGHT = ("#458AD9", "#4563D9", "#4D45D9", "#7445D9", "#9A45D9", "#C145D9")

DARK = dict(name="banner.svg", wordmark="#F4F5F7", ink="#F4F5F7", sig="#22B4CC",
            nodes=NODES_DARK)
LIGHT = dict(name="banner-light.svg", wordmark="#1A1815", ink="#1A1815", sig="#0A6070",
             nodes=NODES_LIGHT)


def mark_block(t: dict) -> str:
    """The mark, inlined. Its ids are prefixed: they land in the banner's
    document, where a bare id="c" would collide with the next thing named c.

    The source file's <style> goes: it carries a prefers-color-scheme rule for
    the standalone case, and inside a banner that would override the explicit
    ink the <picture> element already picked. Which is why every colour the mark
    states as a literal - the crystal and the six nodes - is swapped here for
    the twin this theme measured against its own ground."""
    src = (HERE / FAVICON).read_text()
    body = src.split(">", 1)[1].rsplit("</svg>", 1)[0].strip()
    body = re.sub(r"<style>.*?</style>", "", body, flags=re.S)
    body = body.replace(DARK["sig"], t["sig"])
    for dark, themed in zip(NODES_DARK, t["nodes"]):
        body = body.replace(dark, themed)
    for i in re.findall(r'id="([^"]+)"', body):
        body = body.replace(f'id="{i}"', f'id="mark-{i}"')
        body = body.replace(f"url(#{i})", f"url(#mark-{i})")
    return body


def banner(t: dict) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Silica Core">
  <title>Silica Core</title>

  <!-- the mark, inlined from {FAVICON} by build_banners.py.
       Edit that file, not this block. -->
  <svg x="{MARK_X}" y="{PAD_Y}" width="{MARK_SIZE}" height="{MARK_SIZE}" viewBox="0 0 512 512" color="{t['ink']}">
    {mark_block(t)}
  </svg>

  <g transform="translate({TEXT_X:.1f} {TEXT_Y:.1f}) scale({WORD_SCALE})" fill="{t['wordmark']}">
    <path d="{WORDMARK}"/>
  </g>

  <g transform="translate({CORE_X:.1f} {TEXT_Y:.1f}) scale({CORE_SCALE})" fill="{t['wordmark']}">
    <path d="{CORE}"/>
  </g>
</svg>
"""


def build_rasters() -> None:
    """Export the GUI's WebPs from the mascot PNG, alpha kept."""
    from PIL import Image

    src = Image.open(HERE / MASCOT_PNG).convert("RGBA")
    for name, width in RASTERS:
        out = STATIC / name
        height = round(src.height * width / src.width)
        src.resize((width, height), Image.LANCZOS).save(out, "WEBP", quality=90, method=6)
        print(f"{name}: {width}x{height} ({out.stat().st_size // 1024} KB)")


def check() -> None:
    """The silent failure is a reference that outlives its def: the mark's ids
    are prefixed on the way in, and a miss there still renders, just blank or
    black. The other is a wordmark that has drifted off the mark's centre line,
    which reads as a typo nobody can name."""
    for theme in (DARK, LIGHT):
        svg = banner(theme)
        ids = set(re.findall(r'id="([^"]+)"', svg))
        dangling = set(re.findall(r"url\(#([^)]+)\)", svg)) - ids
        assert not dangling, f"{theme['name']}: dangling reference {dangling}"
        assert "mark-h" in ids, "the mark's ids stopped being prefixed"

    top = TEXT_Y - WORD_CAP * WORD_SCALE
    assert abs((top + TEXT_Y) / 2 - H / 2) < 0.5, "wordmark off the centre line"
    assert CORE_X + CORE_X1 * CORE_SCALE <= W - PAD_X + 0.5, "the lockup overruns"
    ink_gap = CORE_X + CORE_X0 * CORE_SCALE - (TEXT_X + WORD_X1 * WORD_SCALE)
    assert abs(ink_gap - CORE_GAP) < 0.5, "Core has closed on the wordmark"


def header_mark() -> str:
    """The favicon cut down again, for the site's nav at 20px.

    One thing goes, mechanically: the masked ring moire, because at 20px it
    renders as a halo around the nodes instead of as rings. The gradient it used
    to strip as well is gone from the source now. What is left inherits
    currentColor, including the crystal and the six child nodes, because at 20px
    a separate emissive point is one pixel. Derived, not redrawn, so it cannot
    drift."""
    src = (HERE / FAVICON).read_text()
    body = src.split(">", 1)[1].rsplit("</svg>", 1)[0]
    body = re.sub(r'<g mask="url\(#h\)".*?</g>', "", body, flags=re.S)
    body = re.sub(r"<style>.*?</style>", "", body, flags=re.S)  # inlined, so the page inks it
    body = re.sub(r"<defs>.*?</defs>", "", body, flags=re.S)     # the mask, now unused
    body = body.replace(DARK["sig"], "currentColor").replace('class="sig" ', "")
    # the six community hues collapse for the same reason the crystal does: at
    # 20px a node is three pixels, so the hue is a tint on a dot rather than an
    # identity, and keeping it would pin six dark-ground literals into a page
    # that inks its own header.
    body = re.sub(r' class="n\d" (?:fill|stroke)="#[0-9A-F]{6}"', "", body)
    if "url(#" in body:
        raise SystemExit(f"{FAVICON}: a reference outlived its def")
    # and it is inked heavier: at 20px the favicon's weights read lighter than
    # the 600-weight wordmark beside it, which makes the pair look unfinished
    body = re.sub(r'stroke-width="([\d.]+)"',
                  lambda m: f'stroke-width="{float(m.group(1)) * 1.6:.0f}"', body)
    body = re.sub(r'r="([\d.]+)"',
                  lambda m: f'r="{float(m.group(1)) * 1.25:.0f}"', body)
    return ('        <!-- the mark, monochrome, generated from '
            f'assets/{FAVICON} by assets/build_banners.py -->\n'
            '        <svg class="brand-mark" viewBox="0 0 512 512" width="20" height="20"'
            ' aria-hidden="true" focusable="false">'
            + body + "</svg>")


def patch_site_mark() -> None:
    page = HERE.parent / "web" / "index.html"
    before = page.read_text()
    after, n = re.subn(r"        <!-- the mark, monochrome.*?</svg>",
                       lambda _: header_mark(), before, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("web/index.html: brand mark block not found")
    page.write_text(after)
    print(f"web/index.html: {'unchanged' if after == before else 'updated'} (brand mark)")


def copy_art() -> None:
    for name, out in ART_COPIES:
        art = (HERE / name).read_bytes()
        verb = "unchanged" if out.exists() and out.read_bytes() == art else "updated"
        out.write_bytes(art)
        print(f"{out.relative_to(HERE.parent)}: {verb} ({len(art) // 1024} KB)")


def main() -> None:
    check()
    copy_art()
    patch_site_mark()
    build_rasters()
    for theme in (DARK, LIGHT):
        path = HERE / theme["name"]
        before = path.read_text() if path.exists() else ""
        after = banner(theme)
        path.write_text(after)
        verb = "unchanged" if after == before else "updated"
        print(f"{theme['name']}: {verb} ({len(after) // 1024} KB)")


if __name__ == "__main__":
    main()
