# AP Study Hub

Static AP exam practice site. Live: https://0xminmin2025.github.io/ap-study/

## Question bank

| Subject | MCQ | FRQ | Total |
|---|---|---|---|
| Art History | 216 | 62 | 278 |
| Human Geography | 218 | 70 | 288 |
| Microeconomics | 738 | 88 | 826 |
| Physics C: E&M | 323 | 37 | 360 |
| Physics C: Mechanics | 551 | 51 | 602 |
| Statistics | 474 | 163 + 56 practice | 694 |
| US History | 364 | 100 | 464 |

**3,512 questions**, 717 figure images.

Note: ~102 MCQs have empty option values (OCR failures) and are filtered out
at runtime by the `>=2 non-empty options` check in `index.html`.

## Layout

```
docs/                 # GitHub Pages root
  index.html          # entire app (vanilla JS, no build step)
  data/<subject>/
    questions/*.json  # mcq / frq / practice
    images/*.png      # figures, named <year>_q<number>.png
```

## Not in this repo

- `data/` — 479MB of raw exam PDFs and OCR text. Kept locally only.
- `push_to_github.py`, `scripts/push_ap_github.py` — contain hardcoded tokens.

Use `push_fix.py` instead; it reads the token from the git remote URL.

## Regenerating

```bash
python3 parse_stats.py   # raw OCR -> structured JSON
python3 fix_data.py      # cleanup: empty options, College Board watermarks
python3 push_fix.py      # push selected files via Contents API
```

After pushing, purge the CDN:

```bash
curl -s "https://purge.jsdelivr.net/gh/0xminmin2025/ap-study@main/docs/<file>"
```
