#!/usr/bin/env python3
"""给 AP 题目打考点单元标签(unit)，用于前端按单元筛选练习。

用官方 AP Course Framework 的单元划分。模型只做分类，不生成文本，
所以用最便宜的模型、最短输出即可。

用法:
  python3 tag_units.py --subject stats --limit 20 --dry
  python3 tag_units.py --all
"""
import json, os, time, argparse, hashlib, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
CACHE = os.path.join(HOME, ".unit_cache")
os.makedirs(CACHE, exist_ok=True)

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
# 纯分类任务(只输出一个数字)，不需要旗舰模型
MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
FALLBACK = "Pro/moonshotai/Kimi-K2.5"

CHECKPOINT = 100   # 每 N 题落盘，中断不丢

# 官方 AP 考纲单元
UNITS = {
    "stats": [
        "Exploring One-Variable Data", "Exploring Two-Variable Data",
        "Collecting Data", "Probability & Random Variables",
        "Sampling Distributions", "Inference for Proportions",
        "Inference for Means", "Chi-Square", "Inference for Regression",
    ],
    "human-geo": [
        "Thinking Geographically", "Population & Migration",
        "Cultural Patterns & Processes", "Political Patterns & Processes",
        "Agriculture & Rural Land-Use", "Cities & Urban Land-Use",
        "Industrial & Economic Development",
    ],
    "us-history": [
        "Period 1-2: 1491-1754", "Period 3: 1754-1800", "Period 4: 1800-1848",
        "Period 5: 1844-1877", "Period 6: 1865-1898", "Period 7: 1890-1945",
        "Period 8: 1945-1980", "Period 9: 1980-Present",
    ],
    "art-history": [
        "Global Prehistory", "Ancient Mediterranean", "Early Europe & Colonial Americas",
        "Later Europe & Americas", "Indigenous Americas", "Africa",
        "West & Central Asia", "South & Southeast Asia", "East Asia",
        "The Pacific", "Global Contemporary",
    ],
    "micro-econ": [
        "Basic Economic Concepts", "Supply & Demand",
        "Production, Cost & Perfect Competition",
        "Imperfect Competition", "Factor Markets",
        "Market Failure & the Role of Government",
    ],
    "physics-mech": [
        "Kinematics", "Newton's Laws", "Work, Energy & Power",
        "Systems of Particles & Linear Momentum", "Rotation",
        "Oscillations", "Gravitation",
    ],
    "physics-em": [
        "Electrostatics", "Conductors & Capacitors", "Electric Circuits",
        "Magnetic Fields", "Electromagnetism",
    ],
}

_lock = threading.Lock()
_stats = {"ok": 0, "cached": 0, "fail": 0}


def gen_one(q, subj, session, retries=3):
    units = UNITS[subj]
    raw = json.dumps({"t": q.get("text"), "s": subj}, sort_keys=True, ensure_ascii=False)
    ck = hashlib.sha1(raw.encode()).hexdigest()
    cf = os.path.join(CACHE, ck + ".txt")
    if os.path.exists(cf):
        with _lock:
            _stats["cached"] += 1
        return open(cf).read().strip()

    lst = "\n".join(f"{i+1}. {u}" for i, u in enumerate(units))
    prompt = (f"Classify this AP question into exactly one course unit.\n\n"
              f"Units:\n{lst}\n\n"
              f"Question: {(q.get('text') or '')[:600]}\n\n"
              f"Reply with ONLY the unit number (1-{len(units)}). No other text.")

    for attempt in range(retries):
        try:
            r = session.post(API, headers={"Authorization": f"Bearer {KEY}"},
                             json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                                   "temperature": 0, "max_tokens": 10},
                             timeout=90)
            if r.status_code != 200:
                time.sleep(2 * (attempt + 1))
                continue
            txt = r.json()["choices"][0]["message"]["content"].strip()
            digits = "".join(c for c in txt if c.isdigit())
            if not digits:
                continue
            idx = int(digits) - 1
            if not (0 <= idx < len(units)):
                continue
            unit = units[idx]
            open(cf, "w").write(unit)
            return unit
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def run_subject(subj, limit=None, dry=False, workers=12):
    for fname in ("mcq.json", "frq.json"):
        path = f"{DOCS}/{subj}/questions/{fname}"
        if not os.path.exists(path):
            continue
        qs = json.load(open(path))
        todo = [q for q in qs if not q.get("unit")]
        if limit:
            todo = todo[:limit]
        if not todo:
            continue
        print(f"[{subj}/{fname}] 待标注 {len(todo)} / 总 {len(qs)}", flush=True)

        def flush():
            if dry:
                return
            # 防并发覆盖：只回填 unit 字段，保留磁盘上别的脚本写入的解析等内容
            try:
                disk = json.load(open(path))
                dmap = {q.get("id"): q for q in disk}
                for q in qs:
                    tgt = dmap.get(q.get("id"))
                    if tgt is None:
                        continue
                    if q.get("unit"):
                        tgt["unit"] = q["unit"]
                out = disk
            except Exception:
                out = qs
            tmp = path + ".tmp"
            json.dump(out, open(tmp, "w"), ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)

        session = requests.Session()
        session.trust_env = False
        done = 0
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(gen_one, q, subj, session): q for q in todo}
                for fut in as_completed(futs):
                    q = futs[fut]
                    try:
                        u = fut.result()
                    except Exception:
                        u = None
                    done += 1
                    if u:
                        q["unit"] = u
                        with _lock:
                            _stats["ok"] += 1
                    else:
                        with _lock:
                            _stats["fail"] += 1
                    if done % CHECKPOINT == 0:
                        flush()
                        print(f"    {done}/{len(todo)}  [saved]", flush=True)
        finally:
            flush()

        if not dry:
            print(f"[{subj}/{fname}] 已写回", flush=True)
        else:
            import collections
            c = collections.Counter(q.get("unit") for q in todo if q.get("unit"))
            print("  分布:", dict(c))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()

    t0 = time.time()
    for s in (sorted(UNITS) if a.all else [a.subject]):
        run_subject(s, a.limit, a.dry, a.workers)
    print(f"\n===== 完成 {time.time()-t0:.0f}s =====")
    print(f"标注 {_stats['ok']}  缓存 {_stats['cached']}  失败 {_stats['fail']}")
