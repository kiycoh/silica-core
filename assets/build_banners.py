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

# "Core" is set in the wordmark's own face one register down, and a hairline
# rule does the subordinating that a second typeface used to do. The rule is
# what keeps the label a qualifier instead of the second half of the name - the
# distribution's name qualifies the product's - which is what lets the caps stay
# at two fifths of the wordmark's rather than a third. That size is the point:
# GitHub serves this banner at 240px on a phone, where the old italic at a third
# of the cap arrived as a smudge.
#
# Tracked 0.20em against the wordmark's 0.16. Tracking is optical and does not
# scale: a short line of small caps set at the big line's value reads tight.
CORE_SCALE = 0.364          # cap 26 against the wordmark's 63
CORE_GAP = 24               # ink to rule, then rule to ink: the same gap twice
CORE_X0, CORE_X1, CORE_CAP = 5.9, 320.4, 71.5

# The rule carries the mark's own outline weight rather than one picked for it,
# and is inked back so it separates without competing: at full ink a bar that
# wide is the heaviest thing in a lockup drawn entirely in hairlines.
RULE_W = 11 * MARK_SIZE / 512
RULE_INK = 0.38

W = round(PAD_X * 2 + MARK_SIZE + GAP + (WORD_X1 - WORD_X0) * WORD_SCALE
          + CORE_GAP * 2 + RULE_W + (CORE_X1 - CORE_X0) * CORE_SCALE)
H = MARK_SIZE + PAD_Y * 2
# mark, wordmark, rule and label share one centre line, so nothing sits high.
# The label is centred on its cap band and not on the wordmark's baseline: two
# sizes of caps sharing a baseline read as one line where the small half slipped.
MARK_X = PAD_X
TEXT_X = PAD_X + MARK_SIZE + GAP - WORD_X0 * WORD_SCALE
TEXT_Y = H / 2 + WORD_CAP * WORD_SCALE / 2
RULE_X = TEXT_X + WORD_X1 * WORD_SCALE + CORE_GAP
RULE_Y, RULE_H = TEXT_Y - WORD_CAP * WORD_SCALE, WORD_CAP * WORD_SCALE
CORE_X = RULE_X + RULE_W + CORE_GAP - CORE_X0 * CORE_SCALE
CORE_Y = H / 2 + CORE_CAP * CORE_SCALE / 2

# Lato Light @ 100px, tracking 0.16em, extracted with fontTools SVGPathPen so it
# never falls back to Arial on someone else's machine. Light and not Black: the
# mark is hairline art, and a heavy wordmark reads as a different brand sharing
# the canvas.
WORDMARK = "M44.8 -62.2Q44.4 -61.3 43.5 -61.3Q42.9 -61.3 41.8 -62.2Q40.8 -63.2 39.1 -64.3Q37.3 -65.4 34.7 -66.3Q32.1 -67.3 28.2 -67.3Q24.4 -67.3 21.4 -66.2Q18.5 -65.1 16.5 -63.2Q14.6 -61.3 13.5 -58.8Q12.5 -56.3 12.5 -53.6Q12.5 -50 14 -47.6Q15.6 -45.2 18.1 -43.6Q20.6 -42 23.7 -40.8Q26.9 -39.7 30.2 -38.6Q33.6 -37.5 36.8 -36.2Q40 -34.9 42.5 -32.9Q45 -30.9 46.5 -27.9Q48 -25 48 -20.7Q48 -16.2 46.5 -12.3Q45 -8.3 42.1 -5.5Q39.2 -2.6 35 -0.9Q30.8 0.8 25.4 0.8Q18.4 0.8 13.3 -1.7Q8.2 -4.2 4.5 -8.5L5.9 -10.7Q6.5 -11.4 7.2 -11.4Q7.7 -11.4 8.4 -10.8Q9.1 -10.2 10.1 -9.3Q11.1 -8.5 12.5 -7.4Q13.9 -6.4 15.8 -5.5Q17.6 -4.7 20 -4.1Q22.4 -3.5 25.5 -3.5Q29.7 -3.5 33 -4.7Q36.2 -6 38.5 -8.2Q40.8 -10.4 42 -13.4Q43.2 -16.4 43.2 -19.9Q43.2 -23.7 41.7 -26.1Q40.2 -28.5 37.7 -30.1Q35.1 -31.8 32 -32.9Q28.8 -34 25.5 -35Q22.1 -36.1 18.9 -37.4Q15.8 -38.7 13.2 -40.7Q10.8 -42.7 9.2 -45.7Q7.7 -48.8 7.7 -53.3Q7.7 -56.9 9.1 -60.2Q10.4 -63.5 13 -66Q15.6 -68.5 19.4 -70Q23.2 -71.5 28.2 -71.5Q33.6 -71.5 38 -69.8Q42.4 -68 46 -64.5Z M85.1 0H80V-70.8H85.1Z M160.7 -4.4V0H122V-70.8H127.2V-4.4Z M193.7 0H188.6V-70.8H193.7Z M281.7 -11.9Q282.2 -11.9 282.6 -11.6L284.6 -9.4Q282.4 -7.1 279.8 -5.2Q277.2 -3.3 274.1 -2Q271.1 -0.7 267.4 0.1Q263.7 0.8 259.3 0.8Q251.9 0.8 245.8 -1.8Q239.7 -4.4 235.3 -9.1Q230.9 -13.8 228.5 -20.5Q226 -27.2 226 -35.4Q226 -43.5 228.5 -50.1Q231 -56.8 235.5 -61.5Q240 -66.3 246.3 -68.9Q252.6 -71.5 260.2 -71.5Q267.5 -71.5 273.1 -69.3Q278.7 -67 283.3 -63L281.8 -60.7Q281.4 -60.1 280.5 -60.1Q279.9 -60.1 278.5 -61.2Q277.2 -62.3 274.8 -63.6Q272.4 -65 268.8 -66.1Q265.2 -67.2 260.2 -67.2Q253.8 -67.2 248.5 -65Q243.2 -62.8 239.4 -58.7Q235.5 -54.6 233.4 -48.7Q231.2 -42.8 231.2 -35.4Q231.2 -27.9 233.4 -22Q235.6 -16.1 239.4 -12Q243.2 -8 248.4 -5.8Q253.5 -3.6 259.6 -3.6Q263.4 -3.6 266.3 -4.1Q269.3 -4.6 271.8 -5.6Q274.3 -6.6 276.5 -8.1Q278.6 -9.5 280.7 -11.5Q280.9 -11.7 281.2 -11.8Q281.4 -11.9 281.7 -11.9Z M352.4 -25.7 338 -61.5Q337.2 -63.2 336.6 -65.7Q336.2 -64.5 335.9 -63.4Q335.6 -62.3 335.2 -61.4L320.8 -25.7ZM368.1 0H364.1Q363.4 0 363 -0.4Q362.5 -0.8 362.2 -1.4L354 -21.9H319.2L310.9 -1.4Q310.7 -0.8 310.2 -0.4Q309.7 0 309 0H305.1L334.1 -70.8H339.1Z"

# The same face at the same weight, extracted the same way. One voice at two
# sizes: a lockup earns a second typeface only when something in the drawing
# asks for one, and nothing here does - the italic was answering a question the
# rule answers better. Cap 71.5, ink x 5.9 to 320.4.
CORE = "M61.5 -11.9Q62 -11.9 62.4 -11.6L64.4 -9.4Q62.2 -7.1 59.6 -5.2Q57 -3.3 53.9 -2Q50.9 -0.7 47.2 0.1Q43.5 0.8 39.1 0.8Q31.8 0.8 25.6 -1.8Q19.5 -4.4 15.1 -9.1Q10.7 -13.8 8.3 -20.5Q5.9 -27.2 5.9 -35.4Q5.9 -43.5 8.4 -50.1Q10.9 -56.8 15.4 -61.5Q19.9 -66.3 26.2 -68.9Q32.5 -71.5 40.1 -71.5Q47.3 -71.5 52.9 -69.3Q58.5 -67 63.1 -63L61.6 -60.7Q61.2 -60.1 60.3 -60.1Q59.7 -60.1 58.4 -61.2Q57 -62.3 54.6 -63.6Q52.2 -65 48.6 -66.1Q45.1 -67.2 40.1 -67.2Q33.6 -67.2 28.3 -65Q23 -62.8 19.2 -58.7Q15.4 -54.6 13.2 -48.7Q11.1 -42.8 11.1 -35.4Q11.1 -27.9 13.2 -22Q15.4 -16.1 19.2 -12Q23 -8 28.2 -5.8Q33.4 -3.6 39.4 -3.6Q43.2 -3.6 46.1 -4.1Q49.1 -4.6 51.6 -5.6Q54.1 -6.6 56.2 -8.1Q58.4 -9.5 60.5 -11.5Q60.8 -11.7 61 -11.8Q61.2 -11.9 61.5 -11.9Z M160.9 -35.4Q160.9 -27.2 158.4 -20.5Q156 -13.9 151.6 -9.1Q147.1 -4.4 140.9 -1.8Q134.7 0.8 127.2 0.8Q119.7 0.8 113.5 -1.8Q107.3 -4.4 102.8 -9.1Q98.4 -13.9 95.9 -20.5Q93.5 -27.2 93.5 -35.4Q93.5 -43.6 95.9 -50.2Q98.4 -56.9 102.8 -61.6Q107.3 -66.4 113.5 -69Q119.7 -71.5 127.2 -71.5Q134.7 -71.5 140.9 -69Q147.1 -66.4 151.6 -61.7Q156 -56.9 158.4 -50.2Q160.9 -43.6 160.9 -35.4ZM155.6 -35.4Q155.6 -42.8 153.6 -48.7Q151.5 -54.6 147.8 -58.7Q144 -62.8 138.8 -65Q133.5 -67.2 127.2 -67.2Q120.9 -67.2 115.6 -65Q110.4 -62.8 106.6 -58.7Q102.8 -54.6 100.7 -48.7Q98.7 -42.8 98.7 -35.4Q98.7 -28 100.7 -22.1Q102.8 -16.2 106.6 -12.1Q110.4 -8 115.6 -5.8Q120.9 -3.7 127.2 -3.7Q133.5 -3.7 138.8 -5.8Q144 -8 147.8 -12.1Q151.5 -16.2 153.6 -22.1Q155.6 -28 155.6 -35.4Z M214.6 -35.8Q219.3 -35.8 223 -36.9Q226.8 -38.1 229.3 -40.2Q231.9 -42.2 233.2 -45.2Q234.6 -48.2 234.6 -51.9Q234.6 -59.4 229.7 -63.1Q224.8 -66.7 215.3 -66.7H202.3V-35.8ZM246 0H241.6Q240.8 0 240.2 -0.3Q239.6 -0.6 239.1 -1.3L216.4 -30Q216 -30.6 215.6 -31Q215.2 -31.4 214.6 -31.6Q214.1 -31.8 213.4 -31.9Q212.7 -32 211.6 -32H202.3V0H197.2V-70.8H215.3Q227.5 -70.8 233.6 -66Q239.7 -61.3 239.7 -52.2Q239.7 -48.1 238.2 -44.8Q236.8 -41.4 234.2 -38.9Q231.6 -36.4 227.9 -34.8Q224.2 -33.1 219.5 -32.6Q220.7 -31.9 221.7 -30.6Z M320.4 -4.2 320.3 0H277.9V-70.8H320.3V-66.5H283.1V-37.9H314.1V-33.8H283.1V-4.2Z"

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

  <!-- the rule: the mark's outline weight, the wordmark's cap band -->
  <rect x="{RULE_X:.1f}" y="{RULE_Y:.1f}" width="{RULE_W:.2f}" height="{RULE_H:.1f}" fill="{t['ink']}" opacity="{RULE_INK}"/>

  <g transform="translate({CORE_X:.1f} {CORE_Y:.1f}) scale({CORE_SCALE})" fill="{t['wordmark']}">
    <path d="{CORE}"/>
  </g>
</svg>
"""


def build_rasters() -> None:
    """Export the GUI's WebPs from the mascot PNG, alpha kept."""
    # ponytail: the three jobs below the banners write into the GUI, the site
    # and the tabs, none of which exist in this cut. They skip rather than
    # abort, so the banners can still be regenerated here.
    from PIL import Image

    if not STATIC.is_dir():
        return print("rasters: no static dir, skipped")
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
    left = RULE_X - (TEXT_X + WORD_X1 * WORD_SCALE)
    right = CORE_X + CORE_X0 * CORE_SCALE - (RULE_X + RULE_W)
    assert abs(left - CORE_GAP) < 0.5 and abs(right - CORE_GAP) < 0.5, \
        f"the rule sits off centre in its gap ({left:.1f} / {right:.1f})"
    assert abs(CORE_Y - CORE_CAP * CORE_SCALE / 2 - H / 2) < 0.5, \
        "Core has drifted off the centre line"


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
    if not page.exists():
        return print("web/index.html: absent, skipped")
    before = page.read_text()
    after, n = re.subn(r"        <!-- the mark, monochrome.*?</svg>",
                       lambda _: header_mark(), before, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("web/index.html: brand mark block not found")
    page.write_text(after)
    print(f"web/index.html: {'unchanged' if after == before else 'updated'} (brand mark)")


def copy_art() -> None:
    for name, out in ART_COPIES:
        if not out.parent.is_dir():
            print(f"{out.relative_to(HERE.parent)}: absent, skipped")
            continue
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
