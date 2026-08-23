#!/usr/bin/env python3
"""AP全套 OCR — 4进程并发版，带单文件超时保护"""

import fitz, requests, json, base64, os, time, sys, signal
from pathlib import Path
from multiprocessing import Pool
from datetime import datetime

SILICONFLOW_KEY = "sk-swqqhzfeibdgkclaudxkxpjuwguqfudqubqivfbfgmvpqvln"
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL = "Qwen/Qwen3-VL-8B-Instruct"

INPUT_DIR = Path("/Users/clawd/Downloads/AP全套-")
OUTPUT_DIR = Path(os.path.expanduser("~/ap-study/data/AP"))
DPI = 150
WORKERS = 4
MAX_PAGES_OCR = 200  # Skip OCR for PDFs with > 200 pages (huge textbooks)

def has_text_layer(doc, sample_pages=3):
    for i in range(min(sample_pages, len(doc))):
        if len(doc[i].get_text().strip()) > 50:
            return True
    return False

def extract_text_direct(doc):
    pages = []
    for i in range(len(doc)):
        text = doc[i].get_text().strip()
        if text:
            pages.append(f"=== Page {i+1} ===\n{text}")
    return '\n\n'.join(pages)

def ocr_page(img_bytes):
    b64 = base64.b64encode(img_bytes).decode()
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": "Extract all text from this page. Keep structure, question numbers, answer choices, formulas. Output text only."}
        ]}],
        "max_tokens": 4096
    }
    headers = {"Authorization": f"Bearer {SILICONFLOW_KEY}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(SILICONFLOW_URL, json=payload, headers=headers, timeout=90)
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content']
            elif r.status_code == 429:
                time.sleep(15 * (attempt + 1))
            else:
                time.sleep(3)
        except:
            time.sleep(5)
    return None

def process_one(args):
    idx, total, pdf_path = args
    pdf = Path(pdf_path)
    rel = pdf.relative_to(INPUT_DIR)

    out_dir = OUTPUT_DIR / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ocr_file = out_dir / f"{pdf.stem}_ocr.txt"

    if ocr_file.exists() and ocr_file.stat().st_size > 50:
        return ('skip', str(rel), 0)

    try:
        doc = fitz.open(str(pdf))
        n_pages = len(doc)
    except:
        return ('fail', str(rel), 0)

    if has_text_layer(doc):
        text = extract_text_direct(doc)
        ocr_file.write_text(text, encoding='utf-8')
        doc.close()
        return ('text', str(rel), n_pages)
    else:
        if n_pages > MAX_PAGES_OCR:
            doc.close()
            # Write a placeholder
            ocr_file.write_text(f"[SKIPPED - {n_pages} pages, too large for OCR]", encoding='utf-8')
            return ('big_skip', str(rel), n_pages)
        pages = []
        for i in range(n_pages):
            pix = doc[i].get_pixmap(dpi=DPI)
            t = ocr_page(pix.tobytes("png"))
            pages.append(f"=== Page {i+1} ===\n{t or '[OCR FAILED]'}")
            time.sleep(0.2)
        ocr_file.write_text('\n\n'.join(pages), encoding='utf-8')
        doc.close()
        return ('ocr', str(rel), n_pages)

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_pdfs = sorted(INPUT_DIR.rglob("*.pdf"))
    total = len(all_pdfs)
    print(f"[{datetime.now():%H:%M}] Total: {total} PDFs, Workers: {WORKERS}, Max OCR pages: {MAX_PAGES_OCR}", flush=True)

    tasks = [(i, total, str(p)) for i, p in enumerate(all_pdfs)]
    stats = {'text': 0, 'ocr': 0, 'skip': 0, 'fail': 0, 'big_skip': 0, 'pages': 0}
    done = 0
    t0 = time.time()

    with Pool(WORKERS) as pool:
        for result in pool.imap_unordered(process_one, tasks):
            rtype, name, pages = result
            stats[rtype] = stats.get(rtype, 0) + 1
            stats['pages'] += pages
            done += 1
            if done % 20 == 0 or rtype in ('ocr', 'big_skip'):
                elapsed = time.time() - t0
                rate = done / elapsed * 3600 if elapsed > 0 else 0
                eta_h = (total - done) / rate if rate > 0 else 0
                print(f"  [{done}/{total}] {rtype.upper():8s} {name[:60]} | "
                      f"text={stats['text']} ocr={stats['ocr']} skip={stats['skip']} "
                      f"big={stats['big_skip']} | ETA {eta_h:.1f}h", flush=True)

    elapsed = time.time() - t0
    print(f"\n{'='*60}", flush=True)
    print(f"DONE in {elapsed/3600:.1f}h!", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)

if __name__ == '__main__':
    main()
