#!/usr/bin/env python3
"""
Parse AP Statistics exam questions from OCR text files into structured JSON.
Handles MCQ, FRQ, and Practice FR questions.
"""

import json
import re
import os
import glob

HOME = os.path.expanduser("~")
MCQ_BASE = os.path.join(HOME, "ap-study/data/AP/AP 统计/AP统计【历年真题至2025】/AP统计学 【2012-2019+21-25】真题（选择题含简答题）")
FRQ_BASE = os.path.join(HOME, "ap-study/data/AP/AP 统计/AP统计【历年真题至2025】/AP统计学【1998-2025】真题（简答题）")
PRACTICE_BASE = os.path.join(HOME, "ap-study/data/AP/AP 统计/AP统计学 5 Steps to a 5练习题【最新版2024】/赠送的习题")
OUT_DIR = os.path.join(HOME, "ap-study/docs/data/stats/questions")

os.makedirs(OUT_DIR, exist_ok=True)

# ──────────────────────────────────────────────
# Utility functions
# ──────────────────────────────────────────────

def read_file(path):
    """Read file with multiple encoding fallbacks."""
    for enc in ['utf-8', 'utf-8-sig', 'latin-1', 'gbk']:
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""

def strip_page_headers(text):
    """Remove page markers and common header/footer lines."""
    lines = text.split('\n')
    cleaned = []
    skip_patterns = [
        r'^=== Page \d+ ===$',
        r'^Unauthorized copying',
        r'^-\d+-\s*$',
        r'^GO ON TO THE NEXT PAGE',
        r'^\s*GO ON TO THE NEXT PAGE',
        r'^Form [A-Z]',
        r'^Form Code',
        r'collegeboard\.org',
        r'^© \d{4}',
        r'^AP Central is the official',
        r'^\s*$',
    ]
    for line in lines:
        stripped = line.strip()
        if any(re.match(p, stripped) for p in skip_patterns):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def classify_topic(text):
    """Rough topic classification based on keywords."""
    text_lower = text.lower()
    topics = [
        ("inference", ["confidence interval", "hypothesis test", "p-value", "significance", "null hypothesis", "z-test", "t-test", "chi-square", "inference"]),
        ("probability", ["probability", "independent events", "conditional", "bayes", "P(", "p("]),
        ("regression", ["regression", "slope", "residual", "scatterplot", "correlation", "r-squared", "least-squares", "predicted value"]),
        ("sampling-distributions", ["sampling distribution", "central limit", "sample mean", "sample proportion"]),
        ("experimental-design", ["experiment", "treatment", "control group", "random assignment", "confounding", "blocking", "randomized"]),
        ("sampling-methods", ["random sample", "stratified", "cluster sample", "convenience sample", "systematic", "census", "survey", "sampling method"]),
        ("descriptive-stats", ["mean", "median", "standard deviation", "iqr", "interquartile", "boxplot", "histogram", "dotplot", "distribution", "skewed", "bimodal", "quartile", "five-number", "stemplot", "stem-and-leaf"]),
        ("normal-distribution", ["normal distribution", "z-score", "standardized", "percentile", "empirical rule", "68-95-99"]),
        ("random-variables", ["random variable", "expected value", "variance of", "binomial", "geometric distribution"]),
    ]
    for topic_id, keywords in topics:
        if any(kw in text_lower for kw in keywords):
            return topic_id
    return "general"


# ──────────────────────────────────────────────
# MCQ Parsing
# ──────────────────────────────────────────────

def parse_answer_key_from_performance_data(text):
    """Extract answer key from performance data table (2012-2019 format)."""
    answers = {}
    # Format: Question\tSkill\tLearning Objective\tTopic\tKey\t% Correct
    for m in re.finditer(r'^\|\s*(\d+)\s*\|.*?\|\s*([A-E])\s*\|\s*\d+', text, re.MULTILINE):
        answers[int(m.group(1))] = m.group(2)
    # Also try tab-separated format
    for m in re.finditer(r'^(\d+)\t[^|]*?\t[^|]*?\t[^|]*?\t([A-E])\t', text, re.MULTILINE):
        answers[int(m.group(1))] = m.group(2)
    return answers

def parse_answer_key_from_answer_section(text):
    """Extract answer key from explicit answer sections only."""
    answers = {}

    # Only look in answer sections, not the whole text
    # Find answer section markers
    answer_sections = []
    for m in re.finditer(r'(?:^|\n)(?:Answer|ANSWER|答案)\s*\n', text):
        # Get text from this marker to the next page or 2000 chars
        start = m.end()
        end = min(start + 2000, len(text))
        section = text[start:end]
        answer_sections.append(section)
    
    for section in answer_sections:
        # Format: "1. E   2. E   3. D ..." on one line
        for m in re.finditer(r'(\d+)\.\s+([A-E])(?:\s|$)', section):
            answers[int(m.group(1))] = m.group(2)
        
        # Format: "1  E   2  E" (space separated)
        for m in re.finditer(r'(\d+)\s+([A-E])(?:\s|$)', section):
            qnum = int(m.group(1))
            if 1 <= qnum <= 40:
                answers[qnum] = m.group(2)
    
    return answers

def parse_2022_answer_grid(text):
    """Parse the 2022-style answer grid where numbers and letters alternate on lines."""
    answers = {}
    lines = text.split('\n')
    # Find "Answer for Multiple Choice Questions" section
    start = None
    for i, line in enumerate(lines):
        if 'Answer for Multiple Choice Questions' in line:
            start = i
            break
    if start is None:
        return answers
    
    # Collect number-letter pairs from subsequent lines
    nums_and_letters = []
    for i in range(start, min(start + 200, len(lines))):
        stripped = lines[i].strip()
        if re.match(r'^\d+$', stripped):
            nums_and_letters.append(('num', int(stripped)))
        elif re.match(r'^[A-E]$', stripped):
            nums_and_letters.append(('letter', stripped))
        elif '=== Page' in stripped:
            if len(nums_and_letters) > 10:
                break
    
    # Pair them up
    i = 0
    while i < len(nums_and_letters) - 1:
        if nums_and_letters[i][0] == 'num' and nums_and_letters[i+1][0] == 'letter':
            answers[nums_and_letters[i][1]] = nums_and_letters[i+1][1]
            i += 2
        else:
            i += 1
    
    return answers

def parse_2024_answer_file(path):
    """Parse the separate 2024 answer file."""
    text = read_file(path)
    answers = {}
    for m in re.finditer(r'(\d+)\s*[.．:：\s]\s*([A-E])', text):
        answers[int(m.group(1))] = m.group(2)
    # Also try format: "1  D" or just alternating lines
    lines = text.strip().split('\n')
    for line in lines:
        m = re.match(r'^\s*(\d+)\s+([A-E])\s*$', line.strip())
        if m:
            answers[int(m.group(1))] = m.group(2)
    return answers


def parse_mcq_standard(text, year, answers=None):
    """Parse MCQ from standard AP exam format.
    Handles both inline options (A)...(E) and multi-line options."""
    if answers is None:
        answers = {}
    
    questions = []
    
    # Strategy: Split by question numbers. Questions start with a number followed by .
    # We need to handle different formats:
    # 1. "1. Question text\n(A) ...\n(B) ..."  (2012-2019 main files)
    # 2. "1. Question text\n(A) ...\n(B) ..."  (2022 compiled)
    # 3. "1. Question text\nA.\n option text\nB.\n option text" (2025)
    
    # First clean: remove formula pages, instruction pages
    # Find where actual questions start
    q_start = None
    lines = text.split('\n')
    for i, line in enumerate(lines):
        # Look for "1." or "1 " as start of first question
        if re.match(r'^1\.\s', line.strip()) and i > 20:  # skip early occurrences in instructions
            q_start = i
            break
    
    if q_start is None:
        # Try alternate: look for question after SECTION I directions
        for i, line in enumerate(lines):
            if 'Directions:' in line or 'SECTION I' in line:
                for j in range(i+1, min(i+20, len(lines))):
                    if re.match(r'^1\.\s', lines[j].strip()):
                        q_start = j
                        break
                if q_start:
                    break
    
    if q_start is None:
        # Last resort: find "1. " preceded by a page marker  
        for i, line in enumerate(lines):
            if re.match(r'^1\.\s+\S', line.strip()):
                q_start = i
                break
    
    if q_start is None:
        return questions
    
    # Find where questions end (before answer key, FRQ section, or score conversion)
    q_end = len(lines)
    for i in range(q_start, len(lines)):
        line = lines[i].strip()
        if any(marker in line for marker in [
            'Answer for Multiple Choice',
            'Free-Response Questions',
            'SECTION II',
            'Score Conversion',
            'Question Descriptors and Performance',
            'END OF SECTION I',
            'Scoring Guidelines',
        ]):
            q_end = i
            break
    
    question_text = '\n'.join(lines[q_start:q_end])
    
    # Now parse individual questions
    # Split by question number pattern
    # Pattern: number followed by period at start of line
    q_splits = list(re.finditer(r'^(\d+)\.\s', question_text, re.MULTILINE))
    
    for idx, match in enumerate(q_splits):
        qnum = int(match.group(1))
        start_pos = match.start()
        end_pos = q_splits[idx + 1].start() if idx + 1 < len(q_splits) else len(question_text)
        
        q_block = question_text[start_pos:end_pos].strip()
        
        # Remove the question number prefix
        q_block = re.sub(r'^\d+\.\s*', '', q_block)
        
        # Try to extract options
        options = {}
        q_text = q_block
        
        # Pattern 1: (A) ... (B) ... format
        opt_pattern = r'\(([A-E])\)\s*(.*?)(?=\([A-E]\)|$)'
        opt_matches = list(re.finditer(opt_pattern, q_block, re.DOTALL))
        
        if len(opt_matches) >= 4:
            # Found inline options
            q_text = q_block[:opt_matches[0].start()].strip()
            for om in opt_matches:
                letter = om.group(1)
                opt_text = om.group(2).strip()
                # Clean up option text
                opt_text = re.sub(r'\n\s*', ' ', opt_text).strip()
                opt_text = re.sub(r'\s+', ' ', opt_text)
                options[letter] = opt_text
        else:
            # Pattern 2: "A.\n text" format (2025 style) 
            opt_pattern2 = r'^([A-E])\.\s*$'
            block_lines = q_block.split('\n')
            opt_starts = []
            for li, bl in enumerate(block_lines):
                if re.match(opt_pattern2, bl.strip()):
                    opt_starts.append(li)
            
            if len(opt_starts) >= 4:
                q_text = '\n'.join(block_lines[:opt_starts[0]]).strip()
                for oi, start_li in enumerate(opt_starts):
                    letter = block_lines[start_li].strip()[0]
                    end_li = opt_starts[oi + 1] if oi + 1 < len(opt_starts) else len(block_lines)
                    opt_lines = block_lines[start_li + 1:end_li]
                    opt_text = ' '.join(l.strip() for l in opt_lines if l.strip())
                    options[letter] = opt_text
            else:
                # Pattern 3: "(A) text" on separate lines without closing paren issue
                opt_pattern3 = r'\(([A-E])\)\s*(.*)'
                opt_starts3 = []
                for li, bl in enumerate(block_lines):
                    m = re.match(opt_pattern3, bl.strip())
                    if m:
                        opt_starts3.append((li, m.group(1), m.group(2)))
                
                if len(opt_starts3) >= 4:
                    q_text = '\n'.join(block_lines[:opt_starts3[0][0]]).strip()
                    for oi, (start_li, letter, first_line) in enumerate(opt_starts3):
                        end_li = opt_starts3[oi + 1][0] if oi + 1 < len(opt_starts3) else len(block_lines)
                        opt_lines = [first_line] + [block_lines[l].strip() for l in range(start_li + 1, end_li)]
                        opt_text = ' '.join(l for l in opt_lines if l.strip())
                        opt_text = re.sub(r'\s+', ' ', opt_text).strip()
                        options[letter] = opt_text
        
        if not options:
            # Pattern 4: "A.\n..." inline (not on separate line)
            opt_pattern4 = r'(?:^|\n)\s*\(([A-E])\)\s+'
            parts = re.split(opt_pattern4, q_block)
            if len(parts) >= 11:  # text + 5*(letter+text)
                q_text = parts[0].strip()
                for pi in range(1, len(parts) - 1, 2):
                    letter = parts[pi]
                    opt_text = re.sub(r'\n\s*', ' ', parts[pi+1]).strip()
                    options[letter] = opt_text
        
        # Clean question text
        q_text = re.sub(r'\n\s*', ' ', q_text) if '\n' in q_text else q_text
        q_text = re.sub(r'\s+', ' ', q_text).strip()
        
        # But preserve table formatting if present
        if '|' in q_block and '---' in q_block:
            # Re-extract with tables preserved
            q_text_lines = []
            in_table = False
            raw_lines = q_block.split('\n')
            # Find where options start
            opt_start_line = len(raw_lines)
            for li, rl in enumerate(raw_lines):
                if re.match(r'^\s*\([A-E]\)', rl.strip()) or re.match(r'^[A-E]\.\s*$', rl.strip()):
                    opt_start_line = li
                    break
            
            for li, rl in enumerate(raw_lines):
                if li >= opt_start_line:
                    break
                if li == 0:
                    rl = re.sub(r'^\d+\.\s*', '', rl)
                q_text_lines.append(rl)
            
            q_text = '\n'.join(q_text_lines).strip()
        
        # Skip if no question text or too short
        if len(q_text) < 10 and not options:
            continue
        
        answer = answers.get(qnum, "")
        topic = classify_topic(q_text + ' '.join(options.values()))
        
        q_obj = {
            "id": f"{year}-q{qnum}",
            "year": year,
            "number": qnum,
            "source": "AP Exam",
            "text": q_text,
            "options": options,
            "answer": answer,
            "topic": topic
        }
        questions.append(q_obj)
    
    return questions


def parse_2025_mcq(text, answers=None):
    """Parse 2025 MCQ format which has different structure."""
    if answers is None:
        answers = {}
    
    questions = []
    lines = text.split('\n')
    
    # Questions have format:
    # "1. Question text\nA.\nOption A text\nB.\nOption B text\n..."
    # Or with the number on the same line as the option letter
    
    # Find question blocks
    q_pattern = r'^(\d+)\.\s+'
    q_starts = []
    for i, line in enumerate(lines):
        m = re.match(q_pattern, line.strip())
        if m:
            qnum = int(m.group(1))
            if 1 <= qnum <= 40:
                q_starts.append((i, qnum))
    
    for idx, (start_line, qnum) in enumerate(q_starts):
        end_line = q_starts[idx + 1][0] if idx + 1 < len(q_starts) else len(lines)
        block_lines = lines[start_line:end_line]
        q_block = '\n'.join(block_lines)
        
        # Remove question number
        q_block = re.sub(r'^\d+\.\s*', '', q_block.strip())
        block_lines_clean = q_block.split('\n')
        
        # Find options: "A." on its own line, followed by option text
        options = {}
        opt_indices = []
        for li, bl in enumerate(block_lines_clean):
            if re.match(r'^[A-E]\.\s*$', bl.strip()):
                opt_indices.append(li)
            elif re.match(r'^\([A-E]\)\s', bl.strip()):
                opt_indices.append(li)
        
        if len(opt_indices) >= 4:
            q_text = '\n'.join(block_lines_clean[:opt_indices[0]]).strip()
            for oi, si in enumerate(opt_indices):
                letter_match = re.match(r'^([A-E])\.\s*$', block_lines_clean[si].strip())
                if not letter_match:
                    letter_match = re.match(r'^\(([A-E])\)\s*(.*)', block_lines_clean[si].strip())
                if letter_match:
                    letter = letter_match.group(1)
                    ei = opt_indices[oi + 1] if oi + 1 < len(opt_indices) else len(block_lines_clean)
                    if letter_match.lastindex and letter_match.lastindex >= 2 and letter_match.group(2):
                        opt_text = letter_match.group(2) + ' ' + ' '.join(block_lines_clean[si+1:ei])
                    else:
                        opt_text = ' '.join(l.strip() for l in block_lines_clean[si+1:ei] if l.strip())
                    opt_text = re.sub(r'\s+', ' ', opt_text).strip()
                    # Remove trailing page markers
                    opt_text = re.sub(r'\s*=== Page \d+ ===.*', '', opt_text)
                    options[letter] = opt_text
        else:
            q_text = q_block.strip()
        
        q_text = re.sub(r'\s*=== Page \d+ ===.*', '', q_text)
        q_text = q_text.strip()
        
        if len(q_text) < 5:
            continue
        
        answer = answers.get(qnum, "")
        topic = classify_topic(q_text + ' '.join(options.values()))
        
        questions.append({
            "id": f"2025-q{qnum}",
            "year": 2025,
            "number": qnum,
            "source": "AP Exam",
            "text": q_text,
            "options": options,
            "answer": answer,
            "topic": topic
        })
    
    return questions


def process_all_mcq():
    """Process all MCQ files."""
    all_questions = []
    
    # Files directly in MCQ_BASE: 2012-2019, 2021, 2025
    direct_files = glob.glob(os.path.join(MCQ_BASE, "*选择题*_ocr.txt")) + \
                   glob.glob(os.path.join(MCQ_BASE, "*选择题_ocr.txt"))
    
    for fpath in sorted(set(direct_files)):
        fname = os.path.basename(fpath)
        # Extract year
        year_match = re.search(r'(20\d{2})', fname)
        if not year_match:
            continue
        year = int(year_match.group(1))
        print(f"Processing MCQ: {fname} (year={year})")
        
        text = read_file(fpath)
        
        # Extract answers
        answers = {}
        answers.update(parse_answer_key_from_performance_data(text))
        answers.update(parse_answer_key_from_answer_section(text))
        
        if year == 2025:
            # 2025 has answer section at end
            answer_section = text[text.rfind('Answer'):] if 'Answer' in text else ""
            for m in re.finditer(r'(\d+)\.\s*([A-E])', answer_section):
                answers[int(m.group(1))] = m.group(2)
            # Also check for inline answers
            for m in re.finditer(r'(\d+)\.\s+([A-E])\s', answer_section):
                answers[int(m.group(1))] = m.group(2)
            
            questions = parse_2025_mcq(text, answers)
        else:
            questions = parse_mcq_standard(text, year, answers)
        
        all_questions.extend(questions)
        print(f"  Found {len(questions)} questions, {sum(1 for q in questions if q['answer'])} with answers")
    
    # Subdirectory files: 2022, 2023, 2024
    subdirs = {
        '2022 选择题': 2022,
        '2023 选择题': 2023,
        '2024 选择题 统计': 2024,
    }
    
    for subdir, year in subdirs.items():
        subdir_path = os.path.join(MCQ_BASE, subdir)
        if not os.path.exists(subdir_path):
            continue
        
        ocr_files = glob.glob(os.path.join(subdir_path, "*_ocr.txt"))
        
        # For 2024, separate answer file
        answer_file = None
        question_file = None
        for f in ocr_files:
            if '答案' in f:
                answer_file = f
            else:
                question_file = f
        
        if not question_file and ocr_files:
            question_file = ocr_files[0]
        
        if not question_file:
            continue
        
        print(f"Processing MCQ subdir: {subdir} (year={year})")
        text = read_file(question_file)
        
        answers = {}
        if answer_file:
            answers = parse_2024_answer_file(answer_file)
        
        # Also check for answers in the main file
        answers.update(parse_2022_answer_grid(text))
        answers.update(parse_answer_key_from_performance_data(text))
        answers.update(parse_answer_key_from_answer_section(text))
        
        questions = parse_mcq_standard(text, year, answers)
        all_questions.extend(questions)
        print(f"  Found {len(questions)} questions, {sum(1 for q in questions if q['answer'])} with answers")
    
    return all_questions


# ──────────────────────────────────────────────
# FRQ Parsing
# ──────────────────────────────────────────────

def parse_frq_file(text, year):
    """Parse FRQ questions from a file."""
    questions = []
    
    lines = text.split('\n')
    
    # Skip formula pages, instructions - find where questions start
    # Questions can start with "1." or " 1." (with leading space)
    q_start = None
    
    # Strategy: find lines that match question patterns (numbered 1-6)
    # and are followed by substantive text (not formulas)
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match "1." or "1\t" at start of a question - with or without text on same line
        if re.match(r'^1[\.\t]\s*\S', stripped) or re.match(r'^1\.\s*$', stripped):
            # Verify it's not in a formula section by checking context
            # Look at surrounding lines for question-like content
            context = '\n'.join(lines[max(0,i-3):min(len(lines),i+8)])
            if any(word in context.lower() for word in ['question', 'directions', 'scatterplot', 'table', 'study', 
                                                          'sample', 'random', 'survey', 'data', 'following',
                                                          'researcher', 'percent', 'probability', 'distribution',
                                                          'company', 'school', 'city', 'state', 'group',
                                                          'experiment', 'test', 'interval', 'mean', 'proportion',
                                                          'examine', 'investigate', 'manager', 'farmer', 'bird',
                                                          'conducted', 'collected', 'selected', 'measures',
                                                          'length', 'hospital', 'interest', 'insurance',
                                                          'patient', 'record', 'result']):
                q_start = i
                break
            # If we're past line 100, it's probably real questions
            if i > 100:
                q_start = i
                break
    
    if q_start is None:
        return questions
    
    # Find end of questions  
    q_end = len(lines)
    for i in range(q_start, len(lines)):
        line = lines[i].strip()
        if any(marker in line for marker in [
            'STOP', 'END OF EXAM', 'Tables begin', 'Table entry',
            'Standard Normal Probabilities',
        ]):
            q_end = i
            break
    
    question_text = '\n'.join(lines[q_start:q_end])
    
    # Split by question numbers (1-6 for AP Stats)
    # Handle both "N." and "N\t" patterns, with optional leading whitespace
    # Also handle "N.\n" where text starts on next line
    # Be strict: only match 1-6 at start of line with specific formatting
    q_splits = []
    seen_nums = set()
    for m in re.finditer(r'(?:^|\n)\s*(\d)[\.\t]\s*(?=\S|\n)', question_text):
        qnum = int(m.group(1))
        if qnum < 1 or qnum > 6:
            continue
        # Only accept each question number once (first occurrence after q_start)
        if qnum in seen_nums:
            continue
        # Verify this looks like a question start by checking following text
        following = question_text[m.end():m.end()+200].strip()
        # Skip if following text is very short or looks like a formula/number
        if len(following) < 20:
            continue
        # Skip if it's just a number (part of table/formula)
        if re.match(r'^[\d\.\s,]+$', following[:50]):
            continue
        seen_nums.add(qnum)
        q_splits.append(m)
    
    # Validate: we should find consecutive numbers starting from 1
    if q_splits:
        nums = [int(m.group(1)) for m in q_splits]
        if 1 not in nums:
            q_splits = []  # Invalid, skip
        else:
            # Keep only consecutive from 1
            valid = []
            for m in q_splits:
                qnum = int(m.group(1))
                if qnum == len(valid) + 1:
                    valid.append(m)
            q_splits = valid
    
    for idx, match in enumerate(q_splits):
        qnum = int(match.group(1))
        if qnum > 6:  # AP Stats only has 6 FRQs
            continue
        
        start_pos = match.end()
        end_pos = q_splits[idx + 1].start() if idx + 1 < len(q_splits) else len(question_text)
        
        q_block = question_text[start_pos:end_pos].strip()
        
        # Clean up page markers etc.
        q_block = re.sub(r'=== Page \d+ ===', '', q_block)
        q_block = re.sub(r'Unauthorized copying.*?\n', '', q_block)
        q_block = re.sub(r'-\d+-\s*\n', '', q_block)
        q_block = re.sub(r'GO ON TO THE NEXT PAGE\.?\s*\n?', '', q_block)
        q_block = re.sub(r'© \d{4}.*?\n', '', q_block)
        q_block = re.sub(r'Visit (the )?College Board.*?\n', '', q_block)
        q_block = re.sub(r'AP®?\s*STATISTICS.*?QUESTIONS\s*\n', '', q_block)
        q_block = re.sub(r'\d{4}\s+AP®?\s+STATISTICS.*?\n', '', q_block)
        q_block = re.sub(r'www\.collegeboard\.org.*?\n', '', q_block)
        q_block = q_block.strip()
        
        # Parse parts: (a), (b), (c) etc. or A., B., C. etc.
        parts = []
        part_pattern = r'(?:^|\n)\s*\(([a-z])\)\s+'
        part_pattern2 = r'(?:^|\n)\s*([A-C])[\.\t]\s+'
        
        part_splits = list(re.finditer(part_pattern, q_block))
        if not part_splits:
            part_splits = list(re.finditer(part_pattern2, q_block))
        
        if part_splits:
            # Text before first part is the question intro
            intro_text = q_block[:part_splits[0].start()].strip()
            
            for pi, pm in enumerate(part_splits):
                label = pm.group(1).lower()
                p_start = pm.end()
                p_end = part_splits[pi + 1].start() if pi + 1 < len(part_splits) else len(q_block)
                part_text = q_block[p_start:p_end].strip()
                
                # Check for sub-parts: (i), (ii), (iii) or i., ii., iii.
                sub_pattern = r'(?:^|\n)\s*(?:\(([iv]+)\)|([iv]+)[\.\)])\s+'
                sub_splits = list(re.finditer(sub_pattern, part_text))
                
                if sub_splits:
                    part_intro = part_text[:sub_splits[0].start()].strip()
                    sub_parts = []
                    for si, sm in enumerate(sub_splits):
                        sub_label = sm.group(1) or sm.group(2)
                        s_start = sm.end()
                        s_end = sub_splits[si + 1].start() if si + 1 < len(sub_splits) else len(part_text)
                        sub_text = part_text[s_start:s_end].strip()
                        sub_parts.append({"label": f"({sub_label})", "text": sub_text})
                    
                    parts.append({
                        "label": f"({label})",
                        "text": part_intro if part_intro else part_text[:sub_splits[0].start()].strip(),
                        "subparts": sub_parts
                    })
                else:
                    parts.append({"label": f"({label})", "text": part_text})
            
            full_text = intro_text
        else:
            full_text = q_block
        
        # Clean up multiple whitespace
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        
        q_obj = {
            "id": f"{year}-frq-{qnum}",
            "year": year,
            "number": qnum,
            "source": "AP Exam",
            "text": full_text,
            "parts": parts,
            "scoringGuideline": ""
        }
        questions.append(q_obj)
    
    return questions


def parse_scoring_guidelines(text, year):
    """Parse scoring guidelines and return dict keyed by question number."""
    guidelines = {}
    
    # Split by "Question N" markers
    q_pattern = r'Question\s+(\d+)'
    q_splits = list(re.finditer(q_pattern, text))
    
    for idx, match in enumerate(q_splits):
        qnum = int(match.group(1))
        start_pos = match.end()
        end_pos = q_splits[idx + 1].start() if idx + 1 < len(q_splits) else len(text)
        
        sg_block = text[start_pos:end_pos].strip()
        
        # Clean up
        sg_block = re.sub(r'=== Page \d+ ===', '', sg_block)
        sg_block = re.sub(r'© \d{4}.*?\n', '', sg_block)
        sg_block = re.sub(r'Visit.*?collegeboard\.org\s*\n?', '', sg_block)
        sg_block = re.sub(r'AP®?\s*STATISTICS\s*\n', '', sg_block)
        sg_block = re.sub(r'\d{4}\s*SCORING GUIDELINES\s*\n', '', sg_block)
        sg_block = re.sub(r'\n{3,}', '\n\n', sg_block)
        sg_block = sg_block.strip()
        
        # Limit to reasonable length
        if len(sg_block) > 5000:
            sg_block = sg_block[:5000] + "..."
        
        guidelines[qnum] = sg_block
    
    return guidelines


def find_frq_pairs():
    """Find pairs of FRQ question files and scoring guideline files."""
    pairs = []
    
    # Scan all subdirectories
    for root, dirs, files in os.walk(FRQ_BASE):
        ocr_files = [f for f in files if f.endswith('_ocr.txt')]
        
        frq_files = []
        sg_files = []
        
        for f in ocr_files:
            fl = f.lower()
            if any(x in fl for x in ['sg', 'scoring', 'guideline', '答案']):
                sg_files.append(os.path.join(root, f))
            elif any(x in fl for x in ['chief-reader', 'score-distribution']):
                continue  # Skip these
            elif 'apc-statistics' in fl:
                continue  # These are individual question commentaries
            else:
                # Question file
                frq_files.append(os.path.join(root, f))
        
        if frq_files:
            # Extract year - try from the deepest directory first, then from filename
            # The path structure is like .../2012-2019/2012/... or .../2021年简答题/...
            # We want the specific year, not the range
            path_parts = root.split('/')
            year = None
            
            # Try from deepest to shallowest directory
            for part in reversed(path_parts):
                # Skip range directories like "2012-2019" and "1998-2011"
                if re.match(r'^\d{4}-\d{4}$', part):
                    continue
                year_match = re.search(r'(19\d{2}|20\d{2})', part)
                if year_match:
                    year = int(year_match.group(1))
                    break
            
            if year is None:
                # Try from filename
                for f in frq_files:
                    fname = os.path.basename(f)
                    year_match = re.search(r'(19\d{2}|20\d{2})', fname)
                    if year_match:
                        year = int(year_match.group(1))
                        break
            
            if year is None:
                continue
            
            # Check for Form B
            is_form_b = root.endswith('/B') or '/B/' in root or 'form_b' in root.lower() or 'formb' in root.lower()
            
            for frq_file in frq_files:
                sg_file = sg_files[0] if sg_files else None
                suffix = "B" if is_form_b else ""
                pairs.append((frq_file, sg_file, year, suffix))
    
    return pairs


def process_all_frq():
    """Process all FRQ files."""
    all_questions = []
    pairs = find_frq_pairs()
    
    for frq_file, sg_file, year, suffix in sorted(pairs, key=lambda x: (x[2], x[3])):
        print(f"Processing FRQ: {os.path.basename(frq_file)} (year={year}{suffix})")
        
        text = read_file(frq_file)
        if not text.strip():
            continue
        
        questions = parse_frq_file(text, year)
        
        # Try to match scoring guidelines
        if sg_file:
            sg_text = read_file(sg_file)
            guidelines = parse_scoring_guidelines(sg_text, year)
            for q in questions:
                if q["number"] in guidelines:
                    q["scoringGuideline"] = guidelines[q["number"]]
        
        # Add suffix for Form B
        if suffix:
            for q in questions:
                q["id"] = f"{year}{suffix}-frq-{q['number']}"
                q["source"] = f"AP Exam Form {suffix}"
        
        all_questions.extend(questions)
        print(f"  Found {len(questions)} questions")
    
    # Also handle the 2022 compiled file which has FRQs + scoring guidelines
    compiled_2022 = os.path.join(MCQ_BASE, "2022 选择题", "2022 统计 AP Stats 2022_ocr.txt")
    if os.path.exists(compiled_2022):
        text = read_file(compiled_2022)
        # Check if FRQs are in this file and not already processed
        year_2022_ids = [q["id"] for q in all_questions if q["year"] == 2022]
        if not year_2022_ids:
            # Try to find FRQ section
            frq_start = text.find("Free-Response Questions")
            sg_start = text.find("Scoring Guidelines for Free-Response Questions")
            if frq_start > 0 and sg_start > 0:
                frq_text = text[frq_start:sg_start]
                sg_text = text[sg_start:]
                frq_qs = parse_frq_file(frq_text, 2022)
                guidelines = parse_scoring_guidelines(sg_text, 2022)
                for q in frq_qs:
                    if q["number"] in guidelines:
                        q["scoringGuideline"] = guidelines[q["number"]]
                all_questions.extend(frq_qs)
                print(f"  Found {len(frq_qs)} FRQ questions from 2022 compiled file")
    
    return all_questions


# ──────────────────────────────────────────────
# Practice FR Parsing
# ──────────────────────────────────────────────

CHAPTER_TOPICS = {
    1: "Exploring One-Variable Data",
    2: "Exploring One-Variable Data",
    3: "Exploring Two-Variable Data",
    5: "Probability and Random Variables",
    7: "Sampling Distributions",
    8: "Confidence Intervals",
    9: "Significance Tests",
    10: "Comparing Two Populations",
    11: "Chi-Square Tests",
    13: "Inference for Regression",
}


def parse_practice_fr(text, chapter, filename):
    """Parse practice FR questions from a chapter file."""
    questions = []
    lines = text.split('\n')
    
    # Clean page markers
    text_clean = re.sub(r'=== Page \d+ ===\s*', '', text)
    
    # Find questions by number pattern
    q_splits = list(re.finditer(r'^(\d+)\.\s+', text_clean, re.MULTILINE))
    
    for idx, match in enumerate(q_splits):
        qnum = int(match.group(1))
        start_pos = match.end()
        end_pos = q_splits[idx + 1].start() if idx + 1 < len(q_splits) else len(text_clean)
        
        q_block = text_clean[start_pos:end_pos].strip()
        
        # Parse parts
        parts = []
        part_pattern = r'(?:^|\n)\s*\(([a-z])\)\s+'
        part_splits = list(re.finditer(part_pattern, q_block))
        
        if part_splits:
            intro_text = q_block[:part_splits[0].start()].strip()
            for pi, pm in enumerate(part_splits):
                label = pm.group(1)
                p_start = pm.end()
                p_end = part_splits[pi + 1].start() if pi + 1 < len(part_splits) else len(q_block)
                part_text = q_block[p_start:p_end].strip()
                parts.append({"label": f"({label})", "text": part_text})
            full_text = intro_text
        else:
            full_text = q_block
        
        topic = CHAPTER_TOPICS.get(chapter, f"Chapter {chapter}")
        
        questions.append({
            "id": f"practice-ch{chapter:02d}-q{qnum}",
            "chapter": chapter,
            "number": qnum,
            "source": "5 Steps to a 5",
            "topic": topic,
            "text": full_text,
            "parts": parts
        })
    
    return questions


def process_all_practice():
    """Process all practice FR files."""
    all_questions = []
    
    practice_files = glob.glob(os.path.join(PRACTICE_BASE, "*_ocr.txt"))
    
    for fpath in sorted(practice_files):
        fname = os.path.basename(fpath)
        
        # Skip non-question files
        if '重要' in fname:
            continue
        
        # Extract chapter number
        chap_match = re.search(r'chap(\d{1,2})(?:\d{2})?_', fname, re.IGNORECASE)
        if not chap_match:
            chap_match = re.search(r'Chapter\s*(\d+)', fname, re.IGNORECASE)
        
        if not chap_match:
            continue
        
        chapter = int(chap_match.group(1))
        
        # Handle combined chapter files like chap0708 -> use first chapter
        if chapter > 20:
            # Extract first 2 digits as chapter
            chap_str = str(chapter)
            chapter = int(chap_str[:2]) if len(chap_str) == 4 else int(chap_str[:1])
        print(f"Processing Practice: {fname} (chapter={chapter})")
        
        text = read_file(fpath)
        questions = parse_practice_fr(text, chapter, fname)
        all_questions.extend(questions)
        print(f"  Found {len(questions)} questions")
    
    return all_questions


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Processing AP Statistics MCQ...")
    print("=" * 60)
    mcq = process_all_mcq()
    
    # Deduplicate by id
    seen = set()
    mcq_dedup = []
    for q in mcq:
        if q["id"] not in seen:
            seen.add(q["id"])
            mcq_dedup.append(q)
    mcq = mcq_dedup
    
    mcq_path = os.path.join(OUT_DIR, "mcq.json")
    with open(mcq_path, 'w', encoding='utf-8') as f:
        json.dump(mcq, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(mcq)} MCQ questions to {mcq_path}")
    
    print("\n" + "=" * 60)
    print("Processing AP Statistics FRQ...")
    print("=" * 60)
    frq = process_all_frq()
    
    seen = set()
    frq_dedup = []
    for q in frq:
        if q["id"] not in seen:
            seen.add(q["id"])
            frq_dedup.append(q)
    frq = frq_dedup
    
    frq_path = os.path.join(OUT_DIR, "frq.json")
    with open(frq_path, 'w', encoding='utf-8') as f:
        json.dump(frq, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(frq)} FRQ questions to {frq_path}")
    
    print("\n" + "=" * 60)
    print("Processing Practice FR...")
    print("=" * 60)
    practice = process_all_practice()
    
    practice_path = os.path.join(OUT_DIR, "practice.json")
    with open(practice_path, 'w', encoding='utf-8') as f:
        json.dump(practice, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(practice)} Practice questions to {practice_path}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"MCQ: {len(mcq)} questions")
    mcq_years = sorted(set(q['year'] for q in mcq))
    print(f"  Years: {mcq_years}")
    mcq_with_answers = sum(1 for q in mcq if q.get('answer'))
    print(f"  With answers: {mcq_with_answers}/{len(mcq)}")
    
    print(f"FRQ: {len(frq)} questions")
    frq_years = sorted(set(q['year'] for q in frq))
    print(f"  Years: {frq_years}")
    frq_with_sg = sum(1 for q in frq if q.get('scoringGuideline'))
    print(f"  With scoring guidelines: {frq_with_sg}/{len(frq)}")
    
    print(f"Practice: {len(practice)} questions")
    practice_chapters = sorted(set(q['chapter'] for q in practice))
    print(f"  Chapters: {practice_chapters}")


if __name__ == "__main__":
    main()
