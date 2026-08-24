#!/usr/bin/env python3
"""为 AP 题库批量生成解析，并反向校验答案。

每题让模型独立作答 + 给出解析。如果模型答案与标注答案不一致，
标记 answer_disputed=true 供人工复核（不自动改答案）。

有配图的题走视觉模型（图一起喂进去），无图的走文本模型。
题目声称需要图但图不存在 -> 标 no_figure_available，直接跳过不烧钱。

增量写回：每 CHECKPOINT 题落盘一次，中断不丢进度。

用法:
  python3 gen_explanations.py --subject stats --limit 5 --dry
  python3 gen_explanations.py --subject stats
  python3 gen_explanations.py --all
"""
import json, os, sys, time, argparse, hashlib, threading, base64, mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")
CACHE = os.path.join(HOME, ".expl_cache")
PROBE_CACHE = os.path.join(HOME, ".probe_cache")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(PROBE_CACHE, exist_ok=True)

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
MODEL_TEXT = "Pro/moonshotai/Kimi-K2.5"   # 注意: Pro/zai-org/GLM-5 已被禁用(403)
MODEL_VL = "Qwen/Qwen3-VL-30B-A3B-Instruct"  # MoE,激活3B,便宜且够强

CHECKPOINT = 40   # 每 N 题落盘一次

SUBJ_NAME = {
    "stats": "AP Statistics", "human-geo": "AP Human Geography",
    "us-history": "AP US History", "art-history": "AP Art History",
    "micro-econ": "AP Microeconomics",
    "physics-mech": "AP Physics C: Mechanics", "physics-em": "AP Physics C: E&M",
}

COMMON_RULES = """Write a concise explanation in English. Requirements:
- 2-4 sentences. Explain WHY the correct answer is right.
- If a common wrong choice is tempting, briefly say why it's wrong.
- Use plain language. No markdown headers, no bullet lists.
- Do NOT restate the question.

Stay strictly on the subject of {subj}. If the text appears to belong to a
different subject or looks garbled/truncated, set explanation to exactly "BAD_DATA".

First, solve it yourself independently. Then respond with ONLY this JSON:
{{"my_answer": "<letter you independently chose, or ?>", "explanation": "<your explanation>"}}"""

PROMPT_TEXT = """You are an expert {subj} teacher writing answer explanations for a student study site.

Question #{num} ({year}):
{text}

Options:
{opts}

The official answer key says: {ans}

""" + COMMON_RULES + """

CRITICAL — this question may reference a figure, graph, table, or image that is
NOT shown to you. Never invent or describe visual details you cannot see. If the
question cannot be answered without seeing the missing visual, set my_answer to
"?" and set explanation to exactly "NEEDS_FIGURE"."""

PROMPT_VL = """You are an expert {subj} teacher writing answer explanations for a student study site.

The attached image is a FULL scanned exam page that usually contains SEVERAL questions.
Focus ONLY on question number {num}. Ignore all other questions on the page.

Question #{num} ({year}) text as we have it:
{text}

Options:
{opts}

The official answer key says: {ans}

Locate question #{num} on the page and read its figure/diagram/graph/artwork carefully.

STEP 1 — Before anything else, check: does question #{num} actually have a picture,
graph, diagram, table, or artwork printed ON THIS PAGE?
Many pages contain ONLY question text and answer choices, with the artwork/figure
printed in a separate insert booklet that you cannot see.
If question #{num} has NO visual on this page but its wording clearly depends on one
(e.g. "the work shown", "the complex shown", "both works", "the graph above"),
you MUST set my_answer to "?" and explanation to exactly "NEEDS_FIGURE".
Never describe an artwork, building, or figure that is not visibly printed on this page.

STEP 2 — Only if the visual IS present, write the explanation.

""" + COMMON_RULES + """

Base your explanation on what you actually see in the image (shapes, labels, axes,
values, style) so the student can follow. Never invent visual details.
If question #{num} is not present on this page, or its figure is not visible,
set my_answer to "?" and set explanation to exactly "NEEDS_FIGURE"."""

_lock = threading.Lock()
_stats = {"ok": 0, "cached": 0, "fail": 0, "disputed": 0, "tokens": 0,
          "skipped": 0, "vl": 0, "txt": 0, "nofig": 0, "novis": 0}

SKIP_MARKERS = ("NEEDS_FIGURE", "BAD_DATA", "BAD_IMAGE")

# 第一道关：先让视觉模型判定「这一页上，第N题到底有没有配图」。
# 不能靠正文 prompt 自觉 —— 模型能凭学科知识反推答案，然后倒过来编造"我看到了"。
# 所以必须用一次独立的、只问事实的调用来把关，判定为无图就退回纯文本分支。
PROBE = """Look at this scanned exam page. The target is question number {num}.
Answer ONLY with JSON, no other text:
{{"q_found": true/false, "has_visual_for_q": true/false, "visual_type": "<diagram|graph|table|artwork|photo|none>"}}

Set "has_visual_for_q" to true ONLY if an actual picture, graph, diagram, table,
chart or artwork belonging to question {num} is printed ON THIS PAGE.
A page showing only question text plus lettered answer choices is false.
Artwork printed in a separate insert booklet (not on this page) is false."""


def probe_has_visual(q, subj, session, img_path):
    """独立判定该页是否真有本题配图。结果单独缓存。"""
    pk = hashlib.sha1(f"probe|{img_path}|{q.get('number')}".encode()).hexdigest()
    pf = os.path.join(PROBE_CACHE, pk + ".json")
    if os.path.exists(pf):
        try:
            return json.load(open(pf)).get("has_visual_for_q", False)
        except Exception:
            pass
    try:
        r = session.post(API, headers={"Authorization": f"Bearer {KEY}"},
                         json={"model": MODEL_VL,
                               "messages": [{"role": "user", "content": [
                                   {"type": "image_url",
                                    "image_url": {"url": img_data_url(img_path)}},
                                   {"type": "text",
                                    "text": PROBE.format(num=q.get("number"))}]}],
                               "temperature": 0, "max_tokens": 150},
                         timeout=120)
        if r.status_code != 200:
            return False
        body = r.json()
        with _lock:
            _stats["tokens"] += body.get("usage", {}).get("total_tokens", 0)
        t = body["choices"][0]["message"]["content"].strip()
        t = t.replace("```json", "").replace("```", "").strip()
        out = json.loads(t[t.find("{"):t.rfind("}") + 1], strict=False)
        json.dump(out, open(pf, "w"), ensure_ascii=False)
        return bool(out.get("has_visual_for_q"))
    except Exception:
        return False


def cache_key(q, has_img):
    raw = json.dumps({"t": q.get("text"), "o": q.get("options"),
                      "a": q.get("answer"), "img": has_img},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()


def img_data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def gen_one(q, subj, session, retries=3):
    img_rel = q.get("image")
    img_path = os.path.join(DOCS, subj, img_rel) if img_rel else None
    use_vl = bool(img_path and os.path.exists(img_path))

    # 有图也不能直接信：先独立探测这一页是否真印着本题的配图。
    # art-history 的扫描页大多只有题干文字（作品图在单独 insert 册），
    # 直接喂给视觉模型会让它凭学科知识编造"我看到的画面"。
    if use_vl and not probe_has_visual(q, subj, session, img_path):
        use_vl = False
        with _lock:
            _stats["novis"] += 1

    ck = cache_key(q, use_vl)
    cf = os.path.join(CACHE, ck + ".json")
    if os.path.exists(cf):
        with _lock:
            _stats["cached"] += 1
        try:
            return json.load(open(cf))
        except Exception:
            pass  # 缓存损坏则重新生成

    opts = q.get("options") or {}
    opt_txt = "\n".join(f"({k}) {v}" for k, v in sorted(opts.items()) if (v or "").strip())
    tmpl = PROMPT_VL if use_vl else PROMPT_TEXT
    prompt = tmpl.format(subj=SUBJ_NAME.get(subj, subj), num=q.get("number"),
                         year=q.get("year"), text=q.get("text", ""),
                         opts=opt_txt, ans=q.get("answer"))

    if use_vl:
        content = [{"type": "image_url", "image_url": {"url": img_data_url(img_path)}},
                   {"type": "text", "text": prompt}]
        model = MODEL_VL
    else:
        content = prompt
        model = MODEL_TEXT

    for attempt in range(retries):
        try:
            r = session.post(API, headers={"Authorization": f"Bearer {KEY}"},
                             json={"model": model,
                                   "messages": [{"role": "user", "content": content}],
                                   "temperature": 0.2, "max_tokens": 700},
                             timeout=180)
            if r.status_code != 200:
                time.sleep(2 * (attempt + 1))
                continue
            body = r.json()
            with _lock:
                _stats["tokens"] += body.get("usage", {}).get("total_tokens", 0)
            txt = body["choices"][0]["message"]["content"].strip()
            txt = txt.replace("```json", "").replace("```", "").strip()
            i, j = txt.find("{"), txt.rfind("}")
            if i < 0 or j < 0:
                continue
            out = json.loads(txt[i:j + 1], strict=False)
            if not out.get("explanation"):
                continue
            out["_via"] = "vl" if use_vl else "txt"
            json.dump(out, open(cf, "w"), ensure_ascii=False)
            return out
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def apply_result(q, res):
    """把模型结果写进题目对象。返回是否成功生成解析。"""
    expl = (res.get("explanation") or "").strip()
    if expl in SKIP_MARKERS:
        q["expl_skipped"] = expl
        with _lock:
            _stats["skipped"] += 1
        return False

    q.pop("expl_skipped", None)
    q["explanation"] = expl
    q["expl_via"] = res.get("_via", "txt")

    mine = str(res.get("my_answer", "")).strip().upper()[:1]
    official = str(q.get("answer", "")).strip().upper()[:1]
    if mine and official and mine != official:
        q["answer_disputed"] = True
        q["model_answer"] = mine
        with _lock:
            _stats["disputed"] += 1
    else:
        q.pop("answer_disputed", None)
        q.pop("model_answer", None)

    with _lock:
        _stats["ok"] += 1
        _stats["vl" if res.get("_via") == "vl" else "txt"] += 1
    return True


def run_subject(subj, limit=None, dry=False, workers=8):
    path = f"{DOCS}/{subj}/questions/mcq.json"
    qs = json.load(open(path))

    todo = []
    for q in qs:
        if (q.get("explanation") or "").strip():
            continue
        if not str(q.get("answer") or "").strip():
            continue
        if len([v for v in (q.get("options") or {}).values() if (v or "").strip()]) < 2:
            continue
        # 需要图但图不存在 -> 死题，不烧钱
        if q.get("no_figure_available"):
            _stats["nofig"] += 1
            continue
        # 已归档（原图未抓取到/数据损坏，人工确认无法作答）-> 永久跳过
        if q.get("archived"):
            _stats["nofig"] += 1
            continue
        todo.append(q)

    if limit:
        todo = todo[:limit]
    n_vl = sum(1 for q in todo if q.get("image"))
    print(f"[{subj}] 待生成 {len(todo)} / 总 {len(qs)}  (视觉 {n_vl} / 文本 {len(todo)-n_vl})",
          flush=True)
    if not todo:
        return

    def flush():
        if dry:
            return
        # 防并发覆盖：写盘前重读磁盘最新版，只回填本脚本负责的字段。
        # 否则与 tag_units.py 同时跑时，会把对方刚写入的 unit 整个覆盖掉。
        try:
            disk = json.load(open(path))
            dmap = {q.get("id"): q for q in disk}
            MINE = ("explanation", "expl_via", "expl_skipped",
                    "answer_disputed", "model_answer")
            for q in qs:
                tgt = dmap.get(q.get("id"))
                if tgt is None:
                    continue
                for k in MINE:
                    if k in q:
                        tgt[k] = q[k]
                    else:
                        tgt.pop(k, None)
            out = disk
        except Exception:
            out = qs   # 读不到就退回整体写，至少不丢自己的结果
        tmp = path + ".tmp"
        json.dump(out, open(tmp, "w"), ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)   # 原子替换，防写坏

    session = requests.Session()
    session.trust_env = False
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(gen_one, q, subj, session): q for q in todo}
            for fut in as_completed(futs):
                q = futs[fut]
                try:
                    res = fut.result()
                except Exception:
                    res = None
                done += 1
                if not res:
                    with _lock:
                        _stats["fail"] += 1
                else:
                    apply_result(q, res)

                if done % CHECKPOINT == 0:
                    flush()
                    print(f"    {done}/{len(todo)}  ok={_stats['ok']} "
                          f"(vl={_stats['vl']} txt={_stats['txt']}) "
                          f"cache={_stats['cached']} skip={_stats['skipped']} "
                          f"disp={_stats['disputed']} fail={_stats['fail']}  [saved]",
                          flush=True)
    finally:
        flush()   # 无论正常结束还是异常/中断，都落盘
        print(f"[{subj}] {'DRY RUN 未写回' if dry else '已写回'}", flush=True)

    if dry:
        for q in todo[:3]:
            if q.get("explanation"):
                print(f"\n  样例 [{q['id']}] ans={q['answer']} via={q.get('expl_via')}"
                      f"{' ⚠️争议->' + q.get('model_answer', '') if q.get('answer_disputed') else ''}")
                print(f"  Q: {q['text'][:100]}")
                print(f"  E: {q['explanation'][:300]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    t0 = time.time()
    subs = sorted(SUBJ_NAME) if a.all else [a.subject]
    for s in subs:
        run_subject(s, a.limit, a.dry, a.workers)
    dt = time.time() - t0
    print(f"\n===== 完成 {dt:.0f}s =====")
    print(f"生成 {_stats['ok']} (视觉{_stats['vl']} 文本{_stats['txt']})  "
          f"命中缓存 {_stats['cached']}  跳过 {_stats['skipped']}  "
          f"死题跳过 {_stats['nofig']}  探测无图退文本 {_stats['novis']}  "
          f"失败 {_stats['fail']}  答案争议 {_stats['disputed']}")
    print(f"tokens {_stats['tokens']:,}")
