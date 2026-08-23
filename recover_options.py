#!/usr/bin/env python3
"""从原始 OCR 文本回捞被解析器丢掉的选项，修复 docs/data 里的坏题。

背景：parse_stats.py 抽取选项时漏掉了部分 (E) 选项和整段选项块，
导致 156 道题出现"答案键缺失/答案指向空选项/空壳"。原始 OCR 文本
(data/AP/**/*_ocr.txt) 里这些选项是完整的。

策略：用题干前 60 字在 OCR 全文里定位，然后向后扫描连续的 (A)...(E) 块。
"""
import json, glob, os, re, sys, collections

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
RAW = os.path.join(HOME, "data", "AP")

SUBJ_KW = {
    "human-geo":    ["人文"],
    "physics-mech": ["物理C力学", "力学"],
    "physics-em":   ["电磁"],
    "micro-econ":   ["微观"],
    "stats":        ["统计"],
    "us-history":   ["美国历史"],
    "art-history":  ["艺术史"],
}

# (A) xxx / A. xxx / (A)xxx
OPT_RE = re.compile(r'^\s*[(\[]?([A-E])[)\].]\s*(.+?)\s*$')


def load_corpus(slug):
    kws = SUBJ_KW[slug]
    texts = []
    for f in glob.glob(f"{RAW}/**/*.txt", recursive=True):
        if any(k in f for k in kws):
            try:
                texts.append(open(f, errors="ignore").read())
            except Exception:
                pass
    return texts


def norm(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def extract_options(text, pos):
    """从 pos 开始向后扫，收集连续的 (A)-(E) 选项行。"""
    seg = text[pos:pos + 2500]
    lines = seg.split('\n')
    opts = {}
    started = False
    for ln in lines:
        m = OPT_RE.match(ln)
        if m:
            letter, body = m.group(1), norm(m.group(2))
            # 必须按 A,B,C... 顺序出现
            expect = chr(ord('A') + len(opts))
            if letter != expect:
                if started:
                    break
                continue
            if not body:
                continue
            opts[letter] = body
            started = True
        elif started:
            # 选项块结束：允许选项内换行续行
            if ln.strip() and not re.match(r'^\s*(Unauthorized|GO ON|-\d+-|===)', ln):
                last = chr(ord('A') + len(opts) - 1)
                if last in opts and len(ln.strip()) < 120:
                    opts[last] += ' ' + norm(ln)
                    continue
            if len(opts) >= 4:
                break
    return opts if len(opts) >= 4 else {}


def find_in_corpus(corpus, stem):
    """用题干定位，返回题干结束处的偏移。"""
    probe = norm(stem)[:60]
    if len(probe) < 20:
        return None
    for text in corpus:
        flat = re.sub(r'\s+', ' ', text)
        i = flat.find(probe)
        if i < 0:
            continue
        # 映射回原文：用题干后半段在原文里再定位一次
        tail = probe[-25:]
        j = text.find(tail.split(' ')[0])
        # 简化：直接在原文里找题干的前 40 字（容忍换行）
        pat = re.compile(r'\s+'.join(map(re.escape, probe.split(' ')[:8])))
        m = pat.search(text)
        if m:
            return (text, m.end())
    return None


def main():
    bad = json.load(open("/tmp/ap_bad.json"))
    by_subj = collections.defaultdict(list)
    for b in bad:
        by_subj[b["subject"]].append(b["id"])

    total_fixed = 0
    report = []

    for slug, ids in sorted(by_subj.items()):
        path = f"{DOCS}/{slug}/questions/mcq.json"
        qs = json.load(open(path))
        qmap = {q.get("id"): q for q in qs}
        print(f"\n=== {slug}  待修 {len(ids)} 题 ===", flush=True)
        corpus = load_corpus(slug)
        print(f"    OCR 语料 {len(corpus)} 份", flush=True)

        fixed = 0
        for qid in ids:
            q = qmap.get(qid)
            if not q:
                continue
            hit = find_in_corpus(corpus, q.get("text", ""))
            if not hit:
                report.append((qid, "题干未定位"))
                continue
            text, pos = hit
            opts = extract_options(text, pos)
            if not opts:
                report.append((qid, "选项块未识别"))
                continue
            old_n = len([v for v in (q.get("options") or {}).values() if (v or "").strip()])
            if len(opts) > old_n:
                q["options"] = opts
                fixed += 1
                report.append((qid, f"修复 {old_n}->{len(opts)}"))
            else:
                report.append((qid, f"无改善 {old_n}vs{len(opts)}"))

        if fixed:
            json.dump(qs, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
        print(f"    修复 {fixed} 题")
        total_fixed += fixed

    print(f"\n===== 合计修复 {total_fixed} / {len(bad)} =====")
    c = collections.Counter(r[1].split()[0] for r in report)
    print("结果分布:", dict(c))
    json.dump(report, open("/tmp/ap_fix_report.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
