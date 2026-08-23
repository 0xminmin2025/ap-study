#!/usr/bin/env python3
"""AP全套 智能文字提取:
- 有文字层的PDF → fitz直接提取 (免费, 瞬间)
- 纯图片PDF → SiliconFlow Qwen3-VL-8B OCR (免费)
"""

import fitz, requests, json, base64, os, time, sys
from pathlib import Path

SILICONFLOW_KEY = os.environ.get("SILICONFLOW_KEY", "")
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL = "Qwen/Qwen3-VL-8B-Instruct"

INPUT_DIR = Path("/Users/clawd/Downloads/AP全套-")
OUTPUT_DIR = Path(os.path.expanduser("~/ap-study/data/AP"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DPI = 150

def has_text_layer(doc, sample_pages=3):
    """Check if PDF has extractable text."""
    for i in range(min(sample_pages, len(doc))):
        if len(doc[i].get_text().strip()) > 50:
            return True
    return False

def extract_text_direct(doc):
    """Direct text extraction from PDF with text layer."""
    pages = []
    for i in range(len(doc)):
        text = doc[i].get_text().strip()
        if text:
            pages.append(f"=== Page {i+1} ===\n{text}")
        else:
            pages.append(f"=== Page {i+1} ===\n[empty]")
    return '\n\n'.join(pages)

def ocr_page(img_bytes):
    """OCR one page via SiliconFlow."""
    b64 = base64.b64encode(img_bytes).decode()
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "Extract all text from this page. Keep structure, question numbers, answer choices, formulas. Output text only."}
            ]
        }],
        "max_tokens": 4096
    }
    headers = {"Authorization": f"Bearer {SILICONFLOW_KEY}", "Content-Type": "application/json"}
    
    for attempt in range(3):
        try:
            r = requests.post(SILICONFLOW_URL, json=payload, headers=headers, timeout=90)
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content']
            elif r.status_code == 429:
                time.sleep(10 * (attempt + 1))
            else:
                time.sleep(3)
        except:
            time.sleep(5)
    return None

def ocr_pdf(doc):
    """OCR all pages of an image-only PDF."""
    pages = []
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(dpi=DPI)
        text = ocr_page(pix.tobytes("png"))
        pages.append(f"=== Page {i+1} ===\n{text or '[OCR FAILED]'}")
        time.sleep(0.3)
    return '\n\n'.join(pages)

def main():
    all_pdfs = sorted(INPUT_DIR.rglob("*.pdf"))
    print(f"Total PDFs: {len(all_pdfs)}", flush=True)
    
    stats = {'text_extract': 0, 'ocr': 0, 'skip': 0, 'fail': 0,
             'text_pages': 0, 'ocr_pages': 0}
    
    for i, pdf in enumerate(all_pdfs):
        rel = pdf.relative_to(INPUT_DIR)
        out_dir = OUTPUT_DIR / rel.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        ocr_file = out_dir / f"{pdf.stem}_ocr.txt"
        
        # Skip done
        if ocr_file.exists() and ocr_file.stat().st_size > 50:
            stats['skip'] += 1
            continue
        
        try:
            doc = fitz.open(str(pdf))
            n_pages = len(doc)
        except Exception as e:
            stats['fail'] += 1
            print(f"  ❌ [{i+1}] {rel} - open failed: {e}", flush=True)
            continue
        
        if has_text_layer(doc):
            # Fast path: direct extraction
            text = extract_text_direct(doc)
            ocr_file.write_text(text, encoding='utf-8')
            stats['text_extract'] += 1
            stats['text_pages'] += n_pages
            method = "TEXT"
        else:
            # Slow path: OCR
            text = ocr_pdf(doc)
            ocr_file.write_text(text, encoding='utf-8')
            stats['ocr'] += 1
            stats['ocr_pages'] += n_pages
            method = "OCR"
        
        doc.close()
        
        if (i + 1) % 20 == 0 or method == "OCR":
            done = stats['text_extract'] + stats['ocr'] + stats['skip']
            print(f"  [{done}/{len(all_pdfs)}] {method} {rel.name[:50]} ({n_pages}p) | text={stats['text_extract']} ocr={stats['ocr']} skip={stats['skip']}", flush=True)
    
    print(f"\n{'='*60}", flush=True)
    print(f"DONE!", flush=True)
    print(f"  Direct text: {stats['text_extract']} PDFs ({stats['text_pages']} pages) - FREE & instant", flush=True)
    print(f"  OCR:         {stats['ocr']} PDFs ({stats['ocr_pages']} pages)", flush=True)
    print(f"  Skipped:     {stats['skip']} (already done)", flush=True)
    print(f"  Failed:      {stats['fail']}", flush=True)

if __name__ == '__main__':
    main()
