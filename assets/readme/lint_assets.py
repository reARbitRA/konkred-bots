#!/usr/bin/env python3
"""Verify every README asset before it is committed.

Checks, per file:
  1. the file exists and parses as XML
  2. width/height/viewBox agree
  3. every url(#id) reference resolves to an id defined in the same document
  4. every colour is on the KONKRED fleet palette
  5. no <script>, <image>, <foreignObject> or external href — GitHub strips them
  6. nothing is drawn outside the viewBox (rect/line/circle/text anchors)

It also checks that README.md references exactly the generated asset set.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
README = REPO / "README.md"

EXPECTED = [
    "hero-fleet.svg",
    "five-bot-deck.svg",
    "shared-runtime.svg",
    "telegram-ingress.svg",
    "redis-state.svg",
    "payment-rail.svg",
    "gateway-routing.svg",
    "provider-rack.svg",
    "degradation-flow.svg",
    "webhook-flow.svg",
    "test-console.svg",
    "docker-topology.svg",
    "deployment-map.svg",
    "footer-fleet.svg",
]

PALETTE = {
    "#0A0B10", "#10131B", "#141926", "#0D1017", "#0C1016", "#0A0D14", "#080A0F", "#0B0D14",
    "#151A24", "#0E121B", "#232C3C", "#E6EAF2", "#8A94A6", "#5C6678", "#22D3EE", "#8B5CF6", "#EC4899",
    "#10B981", "#F59E0B",
}

NS = "{http://www.w3.org/2000/svg}"
COLOR_ATTRS = ("fill", "stroke", "stop-color")
FORBIDDEN_TAGS = {f"{NS}script", f"{NS}image", f"{NS}foreignObject"}


def lint(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_text(encoding="utf-8")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [f"XML parse error: {exc}"]

    width = float(root.get("width", 0))
    height = float(root.get("height", 0))
    vb = [float(v) for v in root.get("viewBox", "0 0 0 0").split()]
    if [width, height] != vb[2:]:
        problems.append(f"viewBox {vb} does not match width/height {width}x{height}")

    ids = {el.get("id") for el in root.iter() if el.get("id")}
    for ref in set(re.findall(r"url\(#([A-Za-z0-9_.-]+)\)", raw)):
        if ref not in ids:
            problems.append(f"dangling reference url(#{ref})")

    for el in root.iter():
        if el.tag in FORBIDDEN_TAGS:
            problems.append(f"forbidden element <{el.tag.replace(NS, '')}>")
        for attr in COLOR_ATTRS:
            value = el.get(attr)
            if (value and not value.startswith("url(") and value.lower() != "none"
                    and value.upper() not in PALETTE):
                problems.append(f"off-palette {attr}={value}")
        for attr, value in el.attrib.items():
            if attr.endswith("href") and not str(value).startswith("#"):
                problems.append(f"external reference {attr}={value}")

    # geometry inside the viewBox
    for el in root.iter(f"{NS}rect"):
        x, y = float(el.get("x", 0)), float(el.get("y", 0))
        w, hh = float(el.get("width", 0)), float(el.get("height", 0))
        if x < -1 or y < -1 or x + w > width + 1 or y + hh > height + 1:
            problems.append(f"rect outside viewBox at ({x},{y}) {w}x{hh}")
    for el in root.iter(f"{NS}circle"):
        cx, cy, r = float(el.get("cx", 0)), float(el.get("cy", 0)), float(el.get("r", 0))
        if cx - r < -1 or cy - r < -1 or cx + r > width + 1 or cy + r > height + 1:
            problems.append(f"circle outside viewBox at ({cx},{cy}) r={r}")
    for el in root.iter(f"{NS}line"):
        for ax, ay in (("x1", "y1"), ("x2", "y2")):
            x, y = float(el.get(ax, 0)), float(el.get(ay, 0))
            if not (-1 <= x <= width + 1 and -1 <= y <= height + 1):
                problems.append(f"line endpoint outside viewBox at ({x},{y})")
    for el in root.iter(f"{NS}text"):
        x, y = float(el.get("x", 0)), float(el.get("y", 0))
        if not (-1 <= x <= width + 1 and -1 <= y <= height + 1):
            problems.append(f"text anchor outside viewBox at ({x},{y}): {(el.text or '')[:40]!r}")
    return problems


def main() -> int:
    failures = 0
    for name in EXPECTED:
        path = HERE / name
        if not path.exists():
            print(f"[FAIL] {name}: missing")
            failures += 1
            continue
        problems = lint(path)
        if problems:
            failures += 1
            print(f"[FAIL] {name}")
            for problem in dict.fromkeys(problems):
                print(f"         {problem}")
        else:
            kb = path.stat().st_size / 1024
            print(f"[ ok ] {name:24s} {kb:6.1f} KB")

    if README.exists():
        referenced = set(re.findall(r"assets/readme/([A-Za-z0-9_.-]+\.svg)", README.read_text()))
        missing = referenced - set(EXPECTED)
        unused = set(EXPECTED) - referenced
        if missing:
            print(f"[FAIL] README references unknown assets: {sorted(missing)}")
            failures += 1
        if unused:
            print(f"[FAIL] generated assets never referenced by README: {sorted(unused)}")
            failures += 1
        if not missing and not unused:
            print(f"[ ok ] README references all {len(EXPECTED)} assets and nothing else")

    if failures:
        print(f"\n{failures} check(s) failed")
        return 1
    print(f"\n{len(EXPECTED)}/{len(EXPECTED)} README assets present, well-formed, "
          f"on-palette and inside their viewBox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
