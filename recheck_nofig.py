#!/usr/bin/env python3
"""复核 no_figure_available 误判。

背景：link_images.py 用正则判断"题目是否需要图"，把 above/below/table
等词一律当作需要视觉素材。实测大量误判：
  "inclined 30° above the horizontal"  -> above 指方位，不需要图
  "a mean of 46.0 inches"              -> 描述分布，不需要图
  "Which form of migration below"      -> below 指下面的选项，不需要图

这些题本可以直接生成解析，却被标成死题跳过了。

策略：只复核"仅靠正则判定"的题（has_figure / 模型 NEEDS_FIGURE 判定的不动，
那两个信号可信）。让模型只回答一个问题：不看图能不能答这道题。

用法:
  python3 recheck_nofig.py            # 预演，只报告
  python3 recheck_nofig.py --apply    # 清除误判的 no_figure_available
"""
import json, os, glob, re, argparse, threading, urllib.request, time, hashlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
CACHE = os.path.join(HOME, ".nofig_cache")
API = "https://api.siliconflow.cn/v1/chat/completions"
MODEL = "Pro/moonshotai/Kimi-K2.5"

_lock = threading.Lock()
_stats = Counter()


def _load_key():
    k = os.environ.get("SILICONFLOW_KEY", "").strip()
    if k:
        return k
    p = os.path.join(HOME, ".env.local")
    if os.path.exists(p):
        for line in open(p):
            if line.startswith("SILICONFLOW_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


KEY = _load_key()

PROMPT = """You are auditing an exam question bank.

The question below was flagged as "requires a figure/image to answer".
That flag may be wrong — it was set by a crude keyword match on words like
"above" / "below" / "table", which often appear for unrelated reasons
(e.g. "30 degrees above the horizontal", "the choices below").

Decide: can this question be answered correctly WITHOUT seeing any image?

Respond with ONLY JSON:
{{"needs_figure": true|false, "why": "<10 words max>"}}

QUESTION:
{text}

OPTIONS:
{opts}"""


def ask(q):
    raw = json.dumps({"t": q.get("text"), "o": q.get("options")},
                     ensure_ascii=False, sort_keys=True)
    h = hashlib.md5(raw.encode()).hexdigest()[:16]
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, h + ".json")
    if os.path.exists(cp):
        try:
            with _lock:
                _stats["cached"] += 1
            return json.load(open(cp))
        except Exception:
            pass

    opts = "\n".join(f"({k}) {v}" for k, v in
                     sorted((q.get("options") or {}).items()) if (v or "").strip())
    body = json.dumps({"model": MODEL, "messages": [{"role": "user",
                       "content": PROMPT.format(text=q.get("text", ""), opts=opts)}],
                       "temperature": 0, "max_tokens": 200}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = None
    for attempt in range(3):
        try:
            t = json.load(urllib.request.urlopen(req, timeout=90))
            t = t["choices"][0]["message"]["content"]
            break
        except Exception:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if t is None:
        with _lock:
            _stats["fail"] += 1
        return None
    t = t.replace("```json", "").replace("```", "").strip()
    try:
        out = json.loads(t[t.find("{"):t.rfind("}") + 1], strict=False)
    except Exception:
        with _lock:
            _stats["parse_fail"] += 1
        return None
    json.dump(out, open(cp, "w"), ensure_ascii=False)
    with _lock:
        _stats["ok"] += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()

    if not KEY:
        print("缺少 SILICONFLOW_KEY")
        return

    # 只复核"仅靠正则"判定的：has_figure 和模型 NEEDS_FIGURE 的不碰
    targets = []
    for path in sorted(glob.glob(f"{DOCS}/*/questions/mcq.json")):
        subj = path.split(os.sep)[-3]
        for q in json.load(open(path)):
            if not q.get("no_figure_available"):
                continue
            if q.get("has_figure") or q.get("expl_skipped") == "NEEDS_FIGURE":
                continue
            targets.append((subj, path, q))

    print(f"待复核（仅正则判定的死题）: {len(targets)} 道")
    results = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(ask, q): (s, p, q) for s, p, q in targets}
        done = 0
        for fut in as_completed(futs):
            s, p, q = futs[fut]
            try:
                r = fut.result()
            except Exception:
                r = None
            if r is not None:
                results[id(q)] = r
            done += 1
            if done % 40 == 0:
                print(f"   {done}/{len(targets)} ok={_stats['ok']} "
                      f"cache={_stats['cached']} fail={_stats['fail']}", flush=True)

    freed = Counter()
    kept = Counter()
    for s, p, q in targets:
        r = results.get(id(q))
        if r is None:
            continue
        if r.get("needs_figure") is False:
            freed[s] += 1
        else:
            kept[s] += 1

    print(f"\n{'科目':<14}{'可解放':>8}{'确需图':>8}")
    print("-" * 32)
    for s in sorted(set(list(freed) + list(kept))):
        print(f"{s:<14}{freed[s]:>8}{kept[s]:>8}")
    print("-" * 32)
    print(f"{'TOTAL':<14}{sum(freed.values()):>8}{sum(kept.values()):>8}")
    print(f"\n统计: ok={_stats['ok']} cache={_stats['cached']} "
          f"fail={_stats['fail']} parse_fail={_stats['parse_fail']}")

    if a.apply:
        n = 0
        for path in sorted(glob.glob(f"{DOCS}/*/questions/mcq.json")):
            qs = json.load(open(path))
            changed = 0
            # 用内容哈希匹配回原对象
            want = {}
            for s, p, q in targets:
                if p != path:
                    continue
                r = results.get(id(q))
                if r is not None and r.get("needs_figure") is False:
                    raw = json.dumps({"t": q.get("text"), "o": q.get("options")},
                                     ensure_ascii=False, sort_keys=True)
                    want[hashlib.md5(raw.encode()).hexdigest()] = True
            for q in qs:
                raw = json.dumps({"t": q.get("text"), "o": q.get("options")},
                                 ensure_ascii=False, sort_keys=True)
                if hashlib.md5(raw.encode()).hexdigest() in want:
                    q.pop("no_figure_available", None)
                    q.pop("expl_skipped", None)
                    changed += 1
            if changed:
                tmp = path + ".tmp"
                json.dump(qs, open(tmp, "w"), ensure_ascii=False,
                          separators=(",", ":"))
                os.replace(tmp, path)
                n += changed
        print(f"\n已解放 {n} 道，可重新跑 gen_explanations.py 生成解析")
    else:
        print("\n（预演，加 --apply 才会修改）")


if __name__ == "__main__":
    main()
