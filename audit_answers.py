#!/usr/bin/env python3
"""答案键体检：按 (科目,年份) 统计模型-答案键分歧率，找出整批坏掉的答案键。

单题分歧可能是模型错；但一整份卷子 50%+ 分歧，几乎一定是答案键批次错位/错抓。
配合 --verify 可对可疑批次做第二模型盲答交叉验证。

用法:
  python3 audit_answers.py                 # 出报告
  python3 audit_answers.py --verify        # 对可疑批次做双模型交叉验证
  python3 audit_answers.py --mark          # 给可疑批次的题打 answer_key_suspect 标记
"""
import json, glob, os, argparse, urllib.request, random
from collections import defaultdict, Counter

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
API = "https://api.siliconflow.cn/v1/chat/completions"
def _load_key():
    """key 优先级：环境变量 > 同目录 .env.local（不入库）。
    不硬编码到源码里，因为本仓库要推 GitHub。"""
    k = os.environ.get("SILICONFLOW_KEY", "").strip()
    if k:
        return k
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
    if os.path.exists(p):
        for line in open(p):
            if line.startswith("SILICONFLOW_KEY="):
                return line.split("=", 1)[1].strip()
    return ""

KEY = _load_key()
MODEL_B = "Pro/MiniMaxAI/MiniMax-M2.5"   # 与生成用的 Kimi 独立

SUSPECT_RATE = 30.0   # 分歧率阈值(%)
MIN_N = 10            # 样本太少不判

BLIND = """Answer this {subj} multiple-choice question.
Think briefly, then respond with ONLY JSON: {{"answer":"<single letter>"}}

{text}

{opts}"""

SUBJ_NAME = {
    "stats": "AP Statistics", "human-geo": "AP Human Geography",
    "us-history": "AP US History", "art-history": "AP Art History",
    "micro-econ": "AP Microeconomics",
    "physics-mech": "AP Physics C: Mechanics", "physics-em": "AP Physics C: E&M",
}


def collect():
    """返回 {(subj,year): [disputed, generated]}"""
    stat = defaultdict(lambda: [0, 0])
    for f in sorted(glob.glob(f"{DOCS}/*/questions/mcq.json")):
        sub = f.split(os.sep)[-3]
        for q in json.load(open(f)):
            if not (q.get("explanation") or "").strip():
                continue
            k = (sub, str(q.get("year")))
            stat[k][1] += 1
            if q.get("answer_disputed"):
                stat[k][0] += 1
    return stat


def blind_ask(q, subj):
    opts = "\n".join(f"({k}) {v}" for k, v in sorted((q.get("options") or {}).items())
                     if (v or "").strip())
    body = json.dumps({"model": MODEL_B, "messages": [{"role": "user",
                       "content": BLIND.format(subj=SUBJ_NAME.get(subj, subj),
                                               text=q.get("text", ""), opts=opts)}],
                       "temperature": 0, "max_tokens": 300}).encode()
    req = urllib.request.Request(API, data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
    t = t.replace("```json", "").replace("```", "").strip()
    return json.loads(t[t.find("{"):t.rfind("}") + 1], strict=False).get("answer", "").strip().upper()[:1]


def verify(sub, year, n=15):
    """对某批次做第二模型盲答。返回 (两模型一致且都≠key 的比例, 样本数)"""
    d = json.load(open(f"{DOCS}/{sub}/questions/mcq.json"))
    cand = [q for q in d if str(q.get("year")) == year
            and q.get("answer_disputed") and not q.get("image")]
    if not cand:
        return None, 0
    random.seed(3)
    sample = random.sample(cand, min(n, len(cand)))
    agree = 0
    for q in sample:
        try:
            b = blind_ask(q, sub)
        except Exception:
            continue
        key = str(q.get("answer", "")).upper()[:1]
        if b and b == q.get("model_answer") and b != key:
            agree += 1
    return agree / len(sample) * 100, len(sample)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--mark", action="store_true")
    a = ap.parse_args()

    stat = collect()
    suspects = []
    print(f"{'科目':<14}{'年份':<7}{'分歧':>5}{'已生成':>7}{'分歧率':>8}")
    print("-" * 46)
    for (sub, year), (disp, gen) in sorted(stat.items()):
        if gen < MIN_N:
            continue
        rate = disp / gen * 100
        flag = ""
        if rate >= SUSPECT_RATE:
            flag = "  ⚠️"
            suspects.append((sub, year, disp, gen, rate))
        print(f"{sub:<14}{year:<7}{disp:>5}{gen:>7}{rate:>7.0f}%{flag}")

    if not suspects:
        print("\n未发现可疑批次。")
        return

    print(f"\n⚠️ {len(suspects)} 个批次分歧率 ≥{SUSPECT_RATE:.0f}%，答案键可能整批错误：")
    for sub, year, disp, gen, rate in suspects:
        print(f"   {sub} {year}  {disp}/{gen} ({rate:.0f}%)")

    if a.verify:
        print(f"\n用 {MODEL_B} 盲答交叉验证...")
        for sub, year, *_ in suspects:
            pct, n = verify(sub, year)
            if pct is None:
                print(f"   {sub} {year}: 无可验证样本")
            else:
                verdict = "答案键确认错误" if pct >= 80 else ("部分错误" if pct >= 50 else "模型侧问题")
                print(f"   {sub} {year}: {pct:.0f}% ({n}题) 两模型一致反对答案键 → {verdict}")

    if a.mark:
        sus = {(s, y) for s, y, *_ in suspects}
        for f in sorted(glob.glob(f"{DOCS}/*/questions/mcq.json")):
            sub = f.split(os.sep)[-3]
            qs = json.load(open(f))
            n = 0
            for q in qs:
                if (sub, str(q.get("year"))) in sus:
                    q["answer_key_suspect"] = True
                    n += 1
                else:
                    q.pop("answer_key_suspect", None)
            if n:
                tmp = f + ".tmp"
                json.dump(qs, open(tmp, "w"), ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, f)
                print(f"   标记 {sub}: {n} 题")


if __name__ == "__main__":
    main()
