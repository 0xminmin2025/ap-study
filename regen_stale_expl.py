#!/usr/bin/env python3
"""为答案键重做后的题重新生成解析。

redo_answers.py 改了答案，旧解析是按错误答案键写的（甚至自相矛盾），
必须重写。只处理 explanation_stale=true 的题。

用法:
  python3 regen_stale_expl.py --dry
  python3 regen_stale_expl.py
"""
import json, os, argparse, urllib.request, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
API = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "qwen-max"

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

PROMPT = """You are an expert {subj} teacher writing an answer explanation for a student.

The correct answer is {ans}. Explain in 3-4 sentences why {ans} is correct,
and briefly why the most tempting wrong option is wrong.
Write in clear English prose. No markdown, no bullet points, no restating the question.

Question:
{text}

Options:
{opts}"""


def gen(subj, q, retries=3):
    opts = "\n".join(f"{k}. {v}" for k, v in sorted((q.get("options") or {}).items()))
    prompt = PROMPT.format(subj=SUBJ_NAME.get(subj, subj), ans=q["answer"],
                           text=q.get("text", ""), opts=opts)
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 600,
    }).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API, data=body,
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.load(r)
            txt = (out["choices"][0]["message"]["content"] or "").strip()
            txt = re.sub(r'^\s*[-*#]+\s*', '', txt)
            if len(txt) > 40:
                return txt
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(2 * (attempt + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not KEY:
        print("!! 没有 DASHSCOPE_KEY"); return

    total_ok = total_fail = 0
    for subj in sorted(os.listdir(DOCS)):
        path = f"{DOCS}/{subj}/questions/mcq.json"
        if not os.path.exists(path):
            continue
        data = json.load(open(path))
        todo = [q for q in data if q.get("explanation_stale")]
        if not todo:
            continue
        if args.dry:
            todo = todo[:3]
        print(f"\n===== {subj}: {len(todo)} 道待重写解析 =====", flush=True)
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(gen, subj, q): q for q in todo}
            for i, f in enumerate(as_completed(futs), 1):
                q = futs[f]
                txt = f.result()
                if txt:
                    q["explanation"] = txt
                    q["expl_via"] = "regen_after_answer_fix"
                    q.pop("explanation_stale", None)
                    ok += 1
                else:
                    fail += 1
                if i % 10 == 0 or args.dry:
                    print(f"  [{i}/{len(todo)}] ok={ok} fail={fail}", flush=True)
        if not args.dry:
            json.dump(data, open(path, "w"), ensure_ascii=False, indent=1)
            print(f"  已写入 {path}")
        print(f"  {subj}: 成功 {ok} 失败 {fail}")
        total_ok += ok; total_fail += fail

    print(f"\n===== 总计: 成功 {total_ok} 失败 {total_fail} =====")


if __name__ == "__main__":
    main()
