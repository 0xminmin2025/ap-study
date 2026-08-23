#!/usr/bin/env python3
"""给题目补 image 字段（指向实际存在的图片文件），并标记资料缺失的死题。

- 图片命名规则: docs/data/{subj}/images/{year}_q{number}.png
- 有图    -> q["image"] = "images/2024_q24.png"  (相对 docs/data/{subj}/)
- 需图但无图 -> q["no_figure_available"] = True  (前端不展示解析区)
"""
import json, os, glob, re

HOME = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HOME, "docs", "data")

# 题干里出现这些说明依赖视觉素材
FIG_HINT = re.compile(
    r"\b(figure|graph|chart|table|diagram|image|map|photograph|painting|sculpture|"
    r"shown above|shown below|above|below|on the left|on the right|pictured)\b",
    re.I)

EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def find_image(imgdir, year, num):
    if not os.path.isdir(imgdir):
        return None
    for ext in EXTS:
        fn = f"{year}_q{num}{ext}"
        if os.path.exists(os.path.join(imgdir, fn)):
            return fn
    return None


def main():
    grand = {"linked": 0, "dead": 0, "total": 0}
    print("%-14s %6s %6s %6s" % ("科目", "总题", "关联图", "死题"))
    print("-" * 40)
    for path in sorted(glob.glob(f"{DOCS}/*/questions/mcq.json")):
        subj = path.split(os.sep)[-3]
        imgdir = os.path.join(DOCS, subj, "images")
        qs = json.load(open(path))
        linked = dead = 0

        for q in qs:
            fn = find_image(imgdir, q.get("year"), q.get("number"))
            if fn:
                q["image"] = f"images/{fn}"
                q.pop("no_figure_available", None)
                linked += 1
                continue

            q.pop("image", None)
            # 无图：判断是否本来就需要图
            needs = (q.get("has_figure")
                     or q.get("expl_skipped") == "NEEDS_FIGURE"
                     or bool(FIG_HINT.search(q.get("text") or "")))
            if needs:
                q["no_figure_available"] = True
                dead += 1
            else:
                q.pop("no_figure_available", None)

        json.dump(qs, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
        grand["linked"] += linked
        grand["dead"] += dead
        grand["total"] += len(qs)
        print("%-14s %6d %6d %6d" % (subj, len(qs), linked, dead))

    print("-" * 40)
    print("%-14s %6d %6d %6d" % ("TOTAL", grand["total"], grand["linked"], grand["dead"]))


if __name__ == "__main__":
    main()
