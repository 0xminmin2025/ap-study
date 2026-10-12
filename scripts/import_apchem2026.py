#!/usr/bin/env python3
"""Reproducibly import semantic JSON from the local AP Chemistry 2026 set.

JSON extraction and ordering are deterministic for fixed sources. Rendered figure
PNG bytes can vary across PyMuPDF/Pillow versions and are not used as a
reproducibility guarantee.  No OCR, network
service, or generative model is used.  The companion key is retained alongside
that key; the two reviewed discrepancies are resolved by explicit overrides.
Word OMML equation text is read directly from document.xml so equations which
python-docx omits are not discarded.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import fitz
from lxml import etree
from PIL import Image

LETTERS = "ABCD"
MCQ_RANGE = range(53, 113)
FRQ_RANGE = range(113, 120)
# Reviewed overrides include two conflicts between source keys and three cases
# where both supplied keys are demonstrably wrong from the question text.
VERIFIED_OVERRIDES = {60: "C", 78: "A", 90: "A", 92: "C", 112: "A"}
IMAGE_CHOICE_QUESTIONS = {75, 79, 89}
# These stems depend on a particle diagram that the PDF text layer does not
# fully describe. Preserve a rendered question image for students/reviewers.
REQUIRED_IMAGE_QUESTIONS = IMAGE_CHOICE_QUESTIONS | {91, 111}
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean(text: str) -> str:
    text = text.replace("\uf0b7", " ").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def numbered_starts(text: str, numbers: range) -> dict[int, int]:
    starts = {}
    for number in numbers:
        match = re.search(rf"(?m)^\s*{number}\.\s*$", text)
        if not match:
            raise ValueError(f"source PDF is missing question {number}")
        starts[number] = match.start()
    return starts


def parse_key(path: Path) -> dict[int, str]:
    # utf-8-sig accepts both ordinary UTF-8 and a possible BOM.
    result = {
        int(number): answer
        for number, answer in re.findall(
            r"(?m)^\s*(\d+)\.\s*([A-D])\s*$",
            path.read_text(encoding="utf-8-sig"),
        )
    }
    missing = set(MCQ_RANGE) - result.keys()
    if missing:
        raise ValueError(f"companion key is missing questions {sorted(missing)}")
    return result


def parse_mcq_chunk(chunk: str, number: int) -> tuple[str, dict[str, str], str]:
    chunk = re.sub(rf"^\s*{number}\.\s*", "", chunk, count=1)
    answers = re.findall(r"(?m)^Answer:\s*([A-D])\s*$", chunk)
    if not answers:
        raise ValueError(f"question {number} has no embedded PDF answer")
    pdf_answer = answers[-1]
    chunk = re.sub(r"(?m)^Answer:\s*[A-D]\s*$", "", chunk).strip()

    marks = list(re.finditer(r"(?m)^([A-D])\.\s*$", chunk))
    sequences = [
        i
        for i in range(len(marks) - 3)
        if [marks[i + j].group(1) for j in range(4)] == list(LETTERS)
    ]
    if not sequences:
        raise ValueError(f"question {number} has no A-D option sequence")
    selected = marks[sequences[-1] : sequences[-1] + 4]
    stem = clean(chunk[: selected[0].start()])
    options = {}
    for index, mark in enumerate(selected):
        end = selected[index + 1].start() if index < 3 else len(chunk)
        value = clean(chunk[mark.end() : end])
        if not value and number in IMAGE_CHOICE_QUESTIONS:
            value = f"See option {mark.group(1)} in the attached figure"
        options[mark.group(1)] = value
    if number == 112:
        options["D"] = re.sub(r"\s*Section:\s*Section II,\s*Difficulty:.*$", "", options["D"], flags=re.S).strip()
    if not stem or any(not options[letter] for letter in LETTERS):
        raise ValueError(f"question {number} contains empty visible content")
    return stem, options, pdf_answer


def page_offsets(page_text: list[str]) -> list[tuple[int, int]]:
    offsets, cursor = [], 0
    for text in page_text:
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text) + 1
    return offsets


def page_for_offset(offsets: list[tuple[int, int]], position: int) -> int:
    for index, (start, end) in enumerate(offsets):
        if start <= position <= end:
            return index
    raise ValueError(f"text offset {position} does not belong to a PDF page")


def question_crop(doc: fitz.Document, first: int, last: int, number: int,
                  next_number: int, target: Path) -> None:
    """Render one tightly bounded, multi-page figure for an image-choice MCQ."""
    parts = []
    scale = fitz.Matrix(1.7, 1.7)
    for page_index in range(first, last + 1):
        page = doc[page_index]
        start_y, end_y = 0.0, page.rect.height
        starts = page.search_for(f"{number}.") if page_index == first else []
        if starts:
            start_y = max(0.0, starts[0].y0 - 8)
        next_hits = page.search_for(f"{next_number}.") if page_index == last else []
        if next_hits:
            end_y = min(page.rect.height, next_hits[0].y0 - 8)
        if end_y <= start_y:
            continue
        clip = fitz.Rect(0, start_y, page.rect.width, end_y)
        pix = page.get_pixmap(matrix=scale, clip=clip, alpha=False)
        with Image.open(io.BytesIO(pix.tobytes("png"))) as image:
            parts.append(image.convert("RGB"))
    if not parts:
        raise ValueError(f"could not render figure for question {number}")
    width = max(image.width for image in parts)
    height = sum(image.height for image in parts)
    combined = Image.new("RGB", (width, height), "white")
    y = 0
    for image in parts:
        combined.paste(image, (0, y))
        y += image.height
    combined.save(target, format="PNG", optimize=True)


def docx_paragraphs_with_math(path: Path) -> list[str]:
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    namespace = {"w": W_NS, "m": M_NS}
    paragraphs = []
    for paragraph in root.xpath("//w:body//w:p", namespaces=namespace):
        # XML order is significant: this includes normal runs and OMML m:t runs.
        pieces = paragraph.xpath(".//w:t/text() | .//m:t/text()", namespaces=namespace)
        value = clean("".join(pieces))
        if value:
            paragraphs.append(value)
    return paragraphs


def split_solution_guides(path: Path) -> dict[int, str]:
    text = "\n".join(docx_paragraphs_with_math(path))
    matches = list(re.finditer(r"(?m)^Question\s+(11[3-9])\b[^\n]*", text))
    found = [int(match.group(1)) for match in matches]
    if found != list(FRQ_RANGE):
        raise ValueError(f"expected solution guides 113-119, found {found}")
    guides = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        guides[int(match.group(1))] = clean(text[match.start() : end])

    # The source's prose omitted the actual substitution for C, despite the PDF
    # supplying both mass and molar mass. Preserve the reviewed deterministic
    # calculation and typography requested by the source correction.
    q113 = guides[113]
    q113 = q113.replace("2Ba(NO3)2", "2 Ba(NO₃)₂")
    q113 = re.sub(
        r"Final Answer:\s*\+8\.48kJ/mol\s*\(divide by actual moles of Ba\(NO₃\)₂ used in the experiment for precise value\)",
        "Final Answer: +39.6 kJ/mol",
        q113,
    )
    correction = (
        "\nVerified calculation: n(Ba(NO₃)₂) = 56.0 g / 261.35 g/mol; "
        "ΔHsolution = 8.480 kJ / n = +39.6 kJ/mol.\n"
        "Balanced equation: 2 Ba(NO₃)₂(s) → 2 BaO(s) + 4 NO₂(g) + O₂(g)."
    )
    if "39.6 kJ/mol" not in q113:
        q113 += correction
    guides[113] = q113
    return guides


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_output_path(out: Path, sources: tuple[Path, ...]) -> Path:
    target = out.resolve()
    repo = Path(__file__).resolve().parents[1]
    if target in {Path("/").resolve(), repo, *(source.resolve() for source in sources)}:
        raise ValueError(f"refusing unsafe output path: {out}")
    safe_scratch_child = any(parent.name.lower() in {"build", "tmp", "temp"} for parent in target.parents)
    if target.name != "chemistry" and not safe_scratch_child:
        raise ValueError("output must be a chemistry directory or a child of build/tmp/temp")
    for source in sources:
        try:
            source.resolve().relative_to(target)
        except ValueError:
            pass
        else:
            raise ValueError("refusing to remove an output tree containing a source file")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--frq-solutions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    for label, path in (("PDF", args.pdf), ("key", args.key), ("FRQ solutions", args.frq_solutions)):
        if not path.is_file():
            parser.error(f"{label} source does not exist: {path}")

    try:
        args.out = validate_output_path(args.out, (args.pdf, args.key, args.frq_solutions))
    except ValueError as exc:
        parser.error(str(exc))

    if args.out.exists():
        shutil.rmtree(args.out)
    questions_dir = args.out / "questions"
    images_dir = args.out / "images"
    questions_dir.mkdir(parents=True)
    images_dir.mkdir()

    txt_key = parse_key(args.key)
    # Hash each immutable input once.  Besides being faster for large PDFs,
    # this ensures every row and the audit manifest cite the identical digest.
    pdf_hash = sha256(args.pdf)
    key_hash = sha256(args.key)
    solutions_hash = sha256(args.frq_solutions)
    doc = fitz.open(args.pdf)
    pages = [page.get_text("text") for page in doc]
    full_text = "\n".join(pages)
    starts = numbered_starts(full_text, range(53, 120))
    offsets = page_offsets(pages)

    mcq = []
    conflicts = []
    for number in MCQ_RANGE:
        end = starts[number + 1]
        stem, options, pdf_answer = parse_mcq_chunk(full_text[starts[number] : end], number)
        txt_answer = txt_key[number]
        if pdf_answer != txt_answer:
            conflicts.append({"number": number, "pdf": pdf_answer, "txt": txt_answer})
        answer = VERIFIED_OVERRIDES.get(number, pdf_answer)
        first = page_for_offset(offsets, starts[number])
        last = page_for_offset(offsets, max(starts[number], end - 1))
        images = []
        requires_visual_review = number in REQUIRED_IMAGE_QUESTIONS
        if requires_visual_review:
            image_name = f"2026_q{number}_figure.png"
            question_crop(doc, first, last, number, number + 1, images_dir / image_name)
            images.append(f"images/{image_name}")
        canonical = json.dumps({"text": stem, "options": options}, ensure_ascii=False, sort_keys=True)
        mcq.append({
            "id": f"chemistry_2026_{number}", "type": "mcq", "year": 2026,
            "number": number, "text": stem, "options": options, "answer": answer,
            "answer_key_pdf": pdf_answer, "answer_key_txt": txt_answer,
            "answer_source": "verified_override" if number in VERIFIED_OVERRIDES else "matching_source_keys",
            "source": {"file": args.pdf.name, "sha256": pdf_hash,
                       "pages": list(range(first + 1, last + 2))},
            "images": images,
            **({"image": images[0]} if images else {}),
            **({"requires_visual_review": True} if requires_visual_review else {}),
            "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "archived": False,
        })

    guides = split_solution_guides(args.frq_solutions)
    frq = []
    for number in FRQ_RANGE:
        end = starts.get(number + 1, len(full_text))
        chunk = clean(full_text[starts[number] : end])
        chunk = re.sub(rf"^\s*{number}\.\s*", "", chunk, count=1)
        chunk = re.sub(r"(?m)^Answer:\s*$", "", chunk).strip()
        first = page_for_offset(offsets, starts[number])
        last = page_for_offset(offsets, max(starts[number], end - 1))
        canonical = json.dumps({"text": chunk, "guide": guides[number]}, ensure_ascii=False, sort_keys=True)
        frq.append({
            "id": f"chemistry_2026_{number}", "type": "frq", "year": 2026,
            "number": number, "text": chunk, "scoringGuideline": guides[number],
            "source": {"question_file": args.pdf.name, "question_sha256": pdf_hash,
                       "solution_file": args.frq_solutions.name,
                       "solution_sha256": solutions_hash,
                       "pages": list(range(first + 1, last + 2))},
            "images": [], "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "archived": False,
        })
    doc.close()

    all_hashes = [row["content_hash"] for row in mcq + frq]
    unresolved = [
        item for item in conflicts
        if VERIFIED_OVERRIDES.get(item["number"]) not in {item["pdf"], item["txt"]}
        or item["number"] not in VERIFIED_OVERRIDES
    ]
    empty_options = [
        {"number": row["number"], "option": letter}
        for row in mcq for letter, value in row["options"].items() if not value.strip()
    ]
    report = {
        "format": "apchem2026-import-audit-v1",
        "sources": {
            "pdf": {"file": args.pdf.name, "sha256": pdf_hash},
            "txt_answer_key": {"file": args.key.name, "sha256": key_hash},
            "frq_solutions": {"file": args.frq_solutions.name, "sha256": solutions_hash},
        },
        "mcq_count": len(mcq), "frq_count": len(frq),
        "key_conflicts": conflicts,
        "verified_overrides": {str(k): v for k, v in VERIFIED_OVERRIDES.items()},
        "unresolved_key_conflicts": unresolved,
        "empty_options": empty_options,
        "duplicate_hashes": sum(count - 1 for count in Counter(all_hashes).values() if count > 1),
        "image_files": len(list(images_dir.glob("*"))),
    }
    write_json(questions_dir / "mcq.json", mcq)
    write_json(questions_dir / "frq.json", frq)
    write_json(args.out / "audit_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
