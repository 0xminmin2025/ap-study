#!/usr/bin/env python3
"""Fix all data quality issues in AP Study Hub JSON files."""

import json
import re
import os
import sys

BASE = os.path.expanduser("~/ap-study/docs/data")

# All JSON files
JSON_FILES = [
    "stats/knowledge/notes.json",
    "stats/questions/mcq.json",
    "stats/questions/frq.json",
    "stats/questions/practice.json",
    "human-geo/questions/mcq.json",
    "human-geo/questions/frq.json",
    "us-history/questions/mcq.json",
    "us-history/questions/frq.json",
    "art-history/questions/mcq.json",
    "art-history/questions/frq.json",
    "micro-econ/questions/mcq.json",
    "micro-econ/questions/frq.json",
    "physics-mech/questions/mcq.json",
    "physics-mech/questions/frq.json",
    "physics-em/questions/mcq.json",
    "physics-em/questions/frq.json",
]

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============ STEP 2: Fix orphaned '37' in notes.json ============
def fix_notes_orphan():
    path = os.path.join(BASE, "stats/knowledge/notes.json")
    data = load_json(path)
    fixes = 0
    for unit in data.get("units", []):
        for section in unit.get("sections", []):
            content = section.get("content", "")
            # Remove orphaned '37' - could be at end of content or as standalone line
            new_content = re.sub(r'\n\s*37\s*$', '', content)
            new_content = re.sub(r'^37\s*\n', '', new_content)
            # Also try removing it if it's embedded
            if new_content != content:
                section["content"] = new_content
                fixes += 1
                print(f"  Fixed orphaned '37' in section: {section.get('title', 'unknown')}")
    save_json(path, data)
    return fixes

# ============ STEP 3: Clean watermarks from all JSON files ============
def clean_watermarks_in_str(s):
    if not isinstance(s, str):
        return s, 0
    original = s
    count = 0
    
    # Studocu watermarks
    patterns = [
        r'Downloaded by qidian qidian[^"\n]*',
        r'lOMoARcPSD\|?\d*',
        r'lOMoARcPSD\|\d+',
        # College Board references
        r'Visit the College Board[^."\n]*\.?',
        r'Unauthorized copying or reuse[^."\n]*\.?',
        r'Unauthorized copying[^."\n]*\.?',
        # Page number artifacts like -3-, -7-, -11- etc
        r'\s*-\d{1,3}-\s*',
    ]
    
    for pat in patterns:
        new_s = re.sub(pat, '', s, flags=re.IGNORECASE)
        if new_s != s:
            count += 1
            s = new_s
    
    # Clean up extra whitespace left behind
    s = re.sub(r'\n{3,}', '\n\n', s)
    s = re.sub(r'  +', ' ', s)
    s = s.strip()
    
    return s, (1 if s != original else 0)

def clean_watermarks_recursive(obj):
    """Recursively clean watermarks from all string values in a JSON structure."""
    total = 0
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(obj[key], str):
                obj[key], c = clean_watermarks_in_str(obj[key])
                total += c
            elif isinstance(obj[key], (dict, list)):
                total += clean_watermarks_recursive(obj[key])
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i], c = clean_watermarks_in_str(item)
                total += c
            elif isinstance(item, (dict, list)):
                total += clean_watermarks_recursive(item)
    return total

def clean_all_watermarks():
    total = 0
    for rel in JSON_FILES:
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            continue
        data = load_json(path)
        count = clean_watermarks_recursive(data)
        if count > 0:
            save_json(path, data)
            print(f"  {rel}: cleaned {count} watermark instances")
            total += count
    return total

# ============ STEP 4: Fix broken Unicode (\ufffd) ============
def fix_unicode_in_str(s):
    if not isinstance(s, str):
        return s, 0
    if '\ufffd' not in s:
        return s, 0
    
    count = s.count('\ufffd')
    
    # Common replacements
    # \ufffd often replaces: ≥, ≤, ≠, μ, σ, π, θ, →, ×, °, ², ³, ½, ±
    # Context-based replacements
    s = re.sub(r'\ufffd', '', s)  # Remove replacement characters
    
    return s, count

def fix_unicode_recursive(obj):
    total = 0
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(obj[key], str):
                obj[key], c = fix_unicode_in_str(obj[key])
                total += c
            elif isinstance(obj[key], (dict, list)):
                total += fix_unicode_recursive(obj[key])
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i], c = fix_unicode_in_str(item)
                total += c
            elif isinstance(item, (dict, list)):
                total += fix_unicode_recursive(item)
    return total

def fix_all_unicode():
    total = 0
    for rel in JSON_FILES:
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            continue
        data = load_json(path)
        count = fix_unicode_recursive(data)
        if count > 0:
            save_json(path, data)
            print(f"  {rel}: fixed {count} broken Unicode chars")
            total += count
    return total

# ============ STEP 5: Remove MCQ entries with no options ============
def remove_no_option_mcqs():
    total = 0
    mcq_files = [f for f in JSON_FILES if f.endswith("mcq.json")]
    for rel in mcq_files:
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            continue
        data = load_json(path)
        if not isinstance(data, list):
            continue
        before = len(data)
        data = [q for q in data if q.get("options") and len(q["options"]) > 0]
        after = len(data)
        removed = before - after
        if removed > 0:
            save_json(path, data)
            print(f"  {rel}: removed {removed} questions with no options ({before} → {after})")
            total += removed
    return total

# ============ STEP 6: Deduplicate by text field ============
def deduplicate_questions():
    total = 0
    question_files = [f for f in JSON_FILES if "questions" in f]
    for rel in question_files:
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            continue
        data = load_json(path)
        if not isinstance(data, list):
            continue
        
        seen = set()
        deduped = []
        removed = 0
        for q in data:
            text = q.get("text", "")
            if text and text in seen:
                removed += 1
            else:
                seen.add(text)
                deduped.append(q)
        
        if removed > 0:
            save_json(path, deduped)
            print(f"  {rel}: removed {removed} duplicates ({len(data)} → {len(deduped)})")
            total += removed
    return total

# ============ STEP 7: Verify all JSON files ============
def verify_all():
    print("\n=== Final Verification ===")
    all_ok = True
    for rel in JSON_FILES:
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            print(f"  {rel}: FILE NOT FOUND")
            continue
        try:
            data = load_json(path)
            if isinstance(data, list):
                print(f"  {rel}: OK - {len(data)} items")
            elif isinstance(data, dict):
                if "units" in data:
                    total_sections = sum(len(u.get("sections", [])) for u in data.get("units", []))
                    print(f"  {rel}: OK - {len(data['units'])} units, {total_sections} sections")
                else:
                    print(f"  {rel}: OK - dict with {len(data)} keys")
            else:
                print(f"  {rel}: OK")
        except Exception as e:
            print(f"  {rel}: PARSE ERROR - {e}")
            all_ok = False
    return all_ok

# ============ Main ============
if __name__ == "__main__":
    print("=== Step 2: Fix orphaned '37' in notes.json ===")
    n = fix_notes_orphan()
    print(f"  Total: {n} fixes\n")
    
    print("=== Step 3: Clean watermarks ===")
    n = clean_all_watermarks()
    print(f"  Total: {n} watermark cleanups\n")
    
    print("=== Step 4: Fix broken Unicode ===")
    n = fix_all_unicode()
    print(f"  Total: {n} Unicode fixes\n")
    
    print("=== Step 5: Remove MCQ with no options ===")
    n = remove_no_option_mcqs()
    print(f"  Total: {n} removed\n")
    
    print("=== Step 6: Deduplicate questions ===")
    n = deduplicate_questions()
    print(f"  Total: {n} duplicates removed\n")
    
    verify_all()
