"""Generate the Corral logo SVG. Run it to regenerate docs/screenshots/logo.svg.

The mark is a simple corral: a ring of posts around a central node, the controller herding the fleet.
No dependencies, so it runs anywhere with a plain Python.
"""

from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "logo.svg"


def build() -> str:
    cx = cy = 120
    posts = []
    for i in range(10):
        ang = (i / 10) * 2 * math.pi
        x = cx + 86 * math.cos(ang)
        y = cy + 86 * math.sin(ang)
        posts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="#39c5cf" />')
    ring = "\n    ".join(posts)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="240" height="240" role="img" aria-label="Corral">
  <rect width="240" height="240" rx="48" fill="#0e1116" />
  <circle cx="{cx}" cy="{cy}" r="86" fill="none" stroke="#2b3340" stroke-width="6" stroke-dasharray="4 10" />
  {ring}
  <circle cx="{cx}" cy="{cy}" r="26" fill="#39c5cf" />
  <circle cx="{cx}" cy="{cy}" r="26" fill="none" stroke="#0e1116" stroke-width="4" />
</svg>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
