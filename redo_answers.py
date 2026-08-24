#!/usr/bin/env python3
"""重做答案键：对答案键已确认不可信的整卷，用三个独立模型盲答投票重建答案。

背景：human-geo 2024 / micro-econ 2025 两份卷的答案键与模型判断大规模冲突，
且经偏移检验(-2..+2)确认不是错位，是纯乱码 —— 原答案键无法修复，只能重做。

策略：
  1) 三个独立模型(MiniMax / GLM / Kimi)盲答，互不可见彼此结果
  2) 3/3 一致 -> 高置信，直接采纳，恢复上架
  3) 2/3 一致 -> 中置信，采纳但标记 consensus=2
  4) 无多数   -> 放弃，保持归档
  5) 带图且图缺失的题 -> 跳过，保持归档(no_figure_available)

用法:
  python3 redo_answers.py --dry            # 只跑几道看看
  python3 redo_answers.py                  # 全量重做
"""
import json, os, argparse, urllib.request, urllib.error, time, re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
# 硅基流动 2026-08 余额耗尽(HTTP 402)，改用阿里百炼
API = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

TARGETS = [("stats", 2019), ("micro-econ", 2019), ("micro-econ", 2021), ("us-history", 2024)]

MODELS = [
    "qwen3.6-plus",
    "qwen-max",
    "qwen-plus",
]

SUBJ_NAME = {
    "human-geo": "AP Human Geography",
    "micro-econ": "AP Microeconomics",
    "stats": "AP Statistics",
    "us-history": "AP US History",
    "art-history": "AP Art History",
    "physics-mech": "AP Physics C: Mechanics",
    "physics-em": "AP Physics C: E&M",
}


def _load_key():
    k = os.environ.get("DASHSCOPE_KEY", "").strip()
    if k:
        return k
    p = os.path.join(HOME, ".env.local")
    if os.path.exists(p):
        for line in open(p):
            if line.startswith("DASHSCOPE_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


KEY = _load_key()

BLIND = """You are an expert {subj} teacher grading an official AP exam question.

Answer this multiple-choice question. Reason carefully but briefly.
Then respond with ONLY a JSON object: {{"answer":"<single letter>","confidence":"high|medium|low"}}

Question:
{text}

Options:
{opts}"""


def ask(model, subj, q, retries=3):
    opts = "\n".join(f"{k}. {v}" for k, v in sorted((q.get("options") or {}).items()))
    prompt = BLIND.format(subj=SUBJ_NAME.get(subj, subj), text=q.get("text", ""), opts=opts)
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 1200,
    }).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API, data=body,
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.load(r)
            txt = out["choices"][0]["message"]["content"]
            m = re.search(r'"answer"\s*:\s*"?([A-E])"?', txt)
            if m:
                c = re.search(r'"confidence"\s*:\s*"(\w+)"', txt)
                return m.group(1), (c.group(1) if c else "unknown")
            m = re.search(r'\b([A-E])\b', txt[::-1])
            if m:
                return m.group(1), "weak"
        except Exception as e:
            if attempt == retries - 1:
                return None, f"err:{type(e).__name__}"
            time.sleep(2 * (attempt + 1))
    return None, "nomatch"


def redo_one(subj, q):
    """三模型并发盲答，返回投票结果"""
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {m: ex.submit(ask, m, subj, q) for m in MODELS}
        votes = {m: f.result() for m, f in futs.items()}
    answers = [v[0] for v in votes.values() if v[0]]
    if not answers:
        return None, 0, votes
    cnt = Counter(answers)
    top, n = cnt.most_common(1)[0]
    return top, n, votes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只跑前5道")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not KEY:
        print("!! 没有 SILICONFLOW_KEY"); return

    grand = Counter()
    for subj, yr in TARGETS:
        path = f"{DOCS}/{subj}/questions/mcq.json"
        data = json.load(open(path))
        targets = [q for q in data
                   if q.get("year") == yr
                   and q.get("archived_reason") == "answer_key_unreliable"]
        # 图缺失的跳过
        todo = [q for q in targets if not q.get("no_figure_available")]
        skipped = len(targets) - len(todo)
        if args.dry:
            todo = todo[:5]
        elif args.limit:
            todo = todo[:args.limit]

        print(f"\n===== {subj} {yr}: 待重做 {len(todo)} 道 (跳过缺图 {skipped}) =====", flush=True)
        stats = Counter()
        for i, q in enumerate(todo, 1):
            new, n, votes = redo_one(subj, q)
            old = q.get("answer")
            if new is None:
                stats["fail"] += 1
                print(f"  [{i}/{len(todo)}] {q['id']} 全部失败", flush=True)
                continue
            if n >= 2:
                q["answer"] = new
                q["answer_source"] = "model_consensus_3way"
                q["consensus"] = n
                q["answer_key_original"] = old
                q["archived"] = False
                q.pop("archived_reason", None)
                q.pop("answer_key_suspect", None)
                q.pop("answer_key_suspect_reason", None)
                q.pop("answer_disputed", None)
                # 解析与新答案不符的，清掉等重生成
                if q.get("explanation") and new != old:
                    q["explanation_stale"] = True
                stats[f"consensus{n}"] += 1
                mark = "✓✓✓" if n == 3 else "✓✓ "
            else:
                stats["nomajority"] += 1
                mark = "✗  "
            vs = "/".join(str(v[0] or "-") for v in votes.values())
            if i % 5 == 0 or n < 2 or args.dry:
                print(f"  [{i}/{len(todo)}] {mark} {q['id']} 键={old} 新={new} [{vs}]", flush=True)

        if not args.dry:
            json.dump(data, open(path, "w"), ensure_ascii=False, indent=1)
            print(f"  已写入 {path}")
        print(f"  {subj} {yr}: {dict(stats)}")
        grand.update(stats)

    print("\n===== 总计 =====")
    for k, v in grand.most_common():
        print(f"  {k:<14}{v}")


if __name__ == "__main__":
    main()
