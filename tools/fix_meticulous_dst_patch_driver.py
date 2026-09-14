from pathlib import Path

p = Path("tools/meticulous_three_layer_preflight_fix.py")
text = p.read_text(encoding="utf-8")
old = '''def section(path: str, start: str, end: str, replacement: str) -> None:\n    p = Path(path)\n    text = p.read_text(encoding="utf-8")\n    a = text.find(start)\n    if a < 0:\n        raise SystemExit(f"{path}: start marker not found: {start!r}")\n    b = text.find(end, a)\n    if b < 0:\n        raise SystemExit(f"{path}: end marker not found: {end!r}")\n    p.write_text(text[:a] + replacement + text[b:], encoding="utf-8")\n'''
new = '''def section(path: str, start: str, end: str | None, replacement: str) -> None:\n    p = Path(path)\n    text = p.read_text(encoding="utf-8")\n    a = text.find(start)\n    if a < 0:\n        raise SystemExit(f"{path}: start marker not found: {start!r}")\n    if end is None:\n        b = len(text)\n    else:\n        b = text.find(end, a)\n        if b < 0:\n            raise SystemExit(f"{path}: end marker not found: {end!r}")\n    p.write_text(text[:a] + replacement + text[b:], encoding="utf-8")\n'''
if text.count(old) != 1:
    raise SystemExit("section helper shape changed")
text = text.replace(old, new, 1)
old_end = '''    "\\n# END_DST_TESTS\\n" if "\\n# END_DST_TESTS\\n" in Path(GEFS_TEST).read_text(encoding="utf-8") else "\\n\\n\\n",\n'''
if text.count(old_end) != 1:
    raise SystemExit("DST final-test end marker shape changed")
text = text.replace(old_end, "    None,\n", 1)
p.write_text(text, encoding="utf-8")
print("temporary meticulous patch driver repaired for EOF replacement")
