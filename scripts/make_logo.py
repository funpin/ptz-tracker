"""Логотип-надпись (lockup) для README: знак | линия | «PTZ TRACKER» кривыми, рамка ровно по содержимому.

Запуск: python scripts/make_logo.py — пишет docs/assets/logo-lockup-{dark,light}.svg и исходник
docs/design/logo/ptz-tracker-lockup.svg. Нужен fonttools (pip install fonttools; в зависимости проекта не входит).
Буквы — кривые из Inter (src/ptztracker/ui/fonts): GitHub показывает SVG картинкой без шрифтов, и с текстом
ширина надписи зависела от шрифта зрителя, а логотип стоял не по центру. Поля одинаковые со всех сторон."""

from __future__ import annotations

from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "src" / "ptztracker" / "ui" / "fonts" / "InterVariable.ttf"
OUTS = {
    ROOT / "docs" / "assets" / "logo-lockup-dark.svg": "#F5F5F2",
    ROOT / "docs" / "assets" / "logo-lockup-light.svg": "#17191D",
    ROOT / "docs" / "design" / "logo" / "ptz-tracker-lockup.svg": "#F5F5F2",
}
LIME, GREY = "#B7F34A", "#777C83"
PAD = 14  # поле вокруг содержимого
MARK_DX, MARK_DY = 16, 20  # знак 120×120 из ptz-tracker-mark.svg сдвинут сюда
MARK_X0, MARK_X1 = MARK_DX + 26.5, MARK_DX + 93.5  # скобки 31…89 и половина толщины 9
LINE_X, LINE_Y0, LINE_Y1 = 145, 30, 130
# (текст, x, базовая линия, кегль, насыщенность, разрядка) — как в прежнем SVG с <text>
WORDS = [("PTZ", 175, 77, 48, 750, 1), ("TRACKER", 177, 116, 29, 400, 6)]


def outline(word: str, x: float, y: float, size: float, wght: int, spacing: float) -> tuple[str, float, float]:
    """Контур слова одной строкой d="…" и его левая и правая граница по чернилам."""
    font = instancer.instantiateVariableFont(TTFont(FONT), {"wght": wght, "opsz": 32})
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    k = size / font["head"].unitsPerEm
    d, left, right, pen_x = [], None, None, x
    for ch in word:
        name = cmap[ord(ch)]
        svg = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        glyphs[name].draw(TransformPen(svg, (k, 0, 0, -k, pen_x, y)))
        d.append(svg.getCommands())
        ink = font["glyf"][name]
        left = pen_x + ink.xMin * k if left is None else left
        right = pen_x + ink.xMax * k
        pen_x += hmtx[name][0] * k + spacing
    return "".join(d), left, right


def lockup(ink: str, paths: list[str], x0: float, x1: float) -> str:
    vx, vy = x0 - PAD, LINE_Y0 - PAD
    vw, vh = x1 - x0 + 2 * PAD, LINE_Y1 - LINE_Y0 + 2 * PAD
    words = "\n".join(f'    <path d="{d}"/>' for d in paths)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:g} {vy:g} {vw:.1f} {vh:g}" role="img" aria-labelledby="title desc">
  <title id="title">PTZ Tracker logo</title>
  <desc id="desc">PTZ Tracker wordmark with a geometric tracking symbol.</desc>
  <g transform="translate({MARK_DX} {MARK_DY})" fill="none" stroke="{ink}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round">
    <path d="M31 58V42c0-6.075 4.925-11 11-11h16"/>
    <path d="M62 89h16c6.075 0 11-4.925 11-11V62"/>
  </g>
  <circle cx="{MARK_DX + 60}" cy="{MARK_DY + 60}" r="10" fill="{LIME}"/>
  <path d="M{LINE_X} {LINE_Y0}V{LINE_Y1}" stroke="{GREY}" stroke-width="1.5"/>
  <g fill="{ink}">
{words}
  </g>
</svg>
"""


def main() -> None:
    outlined = [outline(*w) for w in WORDS]
    x1 = max(r for _, _, r in outlined)
    for path, ink in OUTS.items():
        path.write_text(lockup(ink, [d for d, _, _ in outlined], MARK_X0, x1), encoding="utf-8")
    print(f"содержимое {MARK_X0}…{x1:.1f}, ширина рамки {x1 - MARK_X0 + 2 * PAD:.1f}")


if __name__ == "__main__":
    main()
