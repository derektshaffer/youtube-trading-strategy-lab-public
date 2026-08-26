from pathlib import Path

path = Path('youtube_strategy_app.py')
text = path.read_text(encoding='utf-8')

replacements = {
    ':green-background[AUTO]': ':green-background[🟩 AUTO]',
    ':orange-background[CEILING]': ':orange-background[🟧 CEILING]',
    ':yellow-background[THRESHOLD]': ':yellow-background[🟨 THRESHOLD]',
    ':blue-background[SEARCH]': ':blue-background[🟦 SEARCH]',
    ':violet-background[FLOOR]': ':violet-background[🟪 FLOOR]',
    ':gray-background[FIXED]': ':gray-background[⬜ FIXED]',
}
for old, new in replacements.items():
    text = text.replace(old, new)

css_replacements = {
    '.optimizer-badge {display:inline-flex; align-items:center; border-radius:999px; padding:2px 7px;\n font-size:9px; letter-spacing:.055em; font-weight:900; line-height:1.45; border:1px solid var(--line)}':
    '.optimizer-badge {display:inline-flex; align-items:center; border-radius:999px; padding:2px 8px;\n font-size:10px; letter-spacing:.055em; font-weight:900; line-height:1.45; border:1px solid var(--line)}',
    '.optimizer-badge.auto {color:#b9f6db; background:rgba(53,213,151,.09); border-color:rgba(53,213,151,.27)}':
    '.optimizer-badge.auto {color:#d8ffed; background:rgba(53,213,151,.24); border-color:rgba(53,213,151,.72)}',
    '.optimizer-badge.ceiling {color:#ffdda7; background:rgba(255,186,99,.09); border-color:rgba(255,186,99,.27)}':
    '.optimizer-badge.ceiling {color:#fff0cd; background:rgba(255,186,99,.25); border-color:rgba(255,186,99,.72)}',
    '.optimizer-badge.fixed {color:#d3deed; background:rgba(169,185,207,.09); border-color:rgba(169,185,207,.25)}':
    '.optimizer-badge.fixed {color:#f0f4fa; background:rgba(169,185,207,.18); border-color:rgba(169,185,207,.55)}',
    '.optimizer-badge.floor {color:#dfd1ff; background:rgba(169,139,255,.09); border-color:rgba(169,139,255,.27)}':
    '.optimizer-badge.floor {color:#f1eaff; background:rgba(169,139,255,.25); border-color:rgba(169,139,255,.72)}',
    '.optimizer-badge.threshold {color:#fff0a8; background:rgba(231,205,91,.08); border-color:rgba(231,205,91,.25)}':
    '.optimizer-badge.threshold {color:#fff8ca; background:rgba(231,205,91,.24); border-color:rgba(231,205,91,.70)}',
    '.optimizer-badge.search {color:#c8e8ff; background:rgba(86,185,255,.08); border-color:rgba(86,185,255,.25)}':
    '.optimizer-badge.search {color:#e0f3ff; background:rgba(86,185,255,.24); border-color:rgba(86,185,255,.70)}',
}
for old, new in css_replacements.items():
    if old not in text:
        raise SystemExit(f'Expected CSS anchor not found: {old[:60]}')
    text = text.replace(old, new, 1)

required = [
    ':green-background[🟩 AUTO]',
    ':orange-background[🟧 CEILING]',
    ':yellow-background[🟨 THRESHOLD]',
    ':blue-background[🟦 SEARCH]',
    ':violet-background[🟪 FLOOR]',
    ':gray-background[⬜ FIXED]',
    'background:rgba(53,213,151,.24)',
    'background:rgba(255,186,99,.25)',
    'background:rgba(86,185,255,.24)',
]
for item in required:
    if item not in text:
        raise SystemExit(f'Badge contrast verification failed: {item}')

path.write_text(text, encoding='utf-8')
print('Increased optimizer badge contrast and added integrated color squares.')
