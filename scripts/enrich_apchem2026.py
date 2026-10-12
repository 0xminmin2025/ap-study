#!/usr/bin/env python3
"""Blind-solve and enrich AP Chemistry multiple-choice questions.

The answer key is deliberately never sent to the review model.  Results are
validated, cached by question content and model, and checkpointed atomically.
Use ``--fixture-results`` for deterministic/offline runs.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import random
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

UNITS = (
    "Atomic Structure and Properties",
    "Compound Structure and Properties",
    "Properties of Substances and Mixtures",
    "Chemical Reactions",
    "Kinetics",
    "Thermochemistry",
    "Equilibrium",
    "Acids and Bases",
    "Thermodynamics and Electrochemistry",
)
LETTERS = frozenset("ABCD")
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
DEFAULT_VL_MODEL = "Qwen/Qwen2.5-VL-32B-Instruct"
DEFAULT_ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"
CACHE_VERSION = "apchem-review-v2"
PLACEHOLDER_RE = re.compile(r"\b(?:see|refer to|shown in|attached)\b.*\b(?:figure|image|option)\b", re.I | re.S)

SYSTEM_PROMPT = """You are independently solving one AP Chemistry multiple-choice question.
Return only the requested JSON. Choose one answer A-D, classify it into exactly one
allowed AP Chemistry unit, and give a concise English explanation. Do not discuss
an answer key or claim that you saw one."""

SCHEMA = {
    "name": "ap_chemistry_review",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "enum": list("ABCD")},
            "unit": {"type": "string", "enum": list(UNITS)},
            "explanation": {"type": "string"},
        },
        "required": ["answer", "unit", "explanation"],
        "additionalProperties": False,
    },
}


def atomic_json(path: Path, value: Any) -> None:
    """Write JSON durably without ever exposing a partially-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {label} {path}: {exc}") from exc


def validate_question(question: Any, index: int) -> dict[str, Any]:
    if not isinstance(question, dict):
        raise ValueError(f"question at index {index} is not an object")
    if question.get("type") != "mcq":
        raise ValueError(f"question at index {index} is not an MCQ")
    if not isinstance(question.get("text"), str) or not question["text"].strip():
        raise ValueError(f"question at index {index} has no text")
    options = question.get("options")
    if not isinstance(options, dict) or set(options) != LETTERS:
        raise ValueError(f"question at index {index} must have exactly options A-D")
    if any(not isinstance(options[x], str) or not options[x].strip() for x in LETTERS):
        raise ValueError(f"question at index {index} has an empty option")
    if question.get("answer") not in LETTERS:
        raise ValueError(f"question at index {index} has an invalid answer key")
    if not isinstance(question.get("number"), int):
        raise ValueError(f"question at index {index} has no integer number")
    return question


def validate_result(value: Any, label: str = "model result") -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    if set(value) != {"answer", "unit", "explanation"}:
        raise ValueError(f"{label} must contain only answer, unit, and explanation")
    answer, unit, explanation = value["answer"], value["unit"], value["explanation"]
    if answer not in LETTERS:
        raise ValueError(f"{label} answer must be A, B, C, or D")
    if unit not in UNITS:
        raise ValueError(f"{label} has an invalid AP Chemistry unit")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError(f"{label} explanation must be nonempty English text")
    explanation = " ".join(explanation.split())
    if len(explanation) > 1200:
        raise ValueError(f"{label} explanation is not concise")
    if not re.search(r"[A-Za-z]", explanation):
        raise ValueError(f"{label} explanation must be English text")
    return {"answer": answer, "unit": unit, "explanation": explanation}


def canonical_content(question: dict[str, Any]) -> str:
    return json.dumps(
        {"text": question["text"], "options": question["options"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def cache_key(question: dict[str, Any], model: str) -> str:
    content_hash = hashlib.sha256(canonical_content(question).encode("utf-8")).hexdigest()
    protocol = json.dumps({"version": CACHE_VERSION, "prompt": SYSTEM_PROMPT, "schema": SCHEMA},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{content_hash}\0{model}\0{protocol}".encode()).hexdigest()


def subject_data_root(input_path: Path) -> Path:
    parent = input_path.resolve().parent
    return parent.parent if parent.name == "questions" else parent


def _is_supported_image(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError:
        return False
    suffix = path.suffix.lower()
    return ((suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")) or
            (suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff")))


def image_path(question: dict[str, Any], input_path: Path) -> Path | None:
    candidates = []
    raw = question.get("image")
    if isinstance(raw, str):
        candidates.append(raw)
    images = question.get("images")
    if isinstance(images, list):
        candidates.extend(item for item in images if isinstance(item, str))
    for item in candidates:
        supplied = Path(item)
        if supplied.is_absolute() or ".." in supplied.parts:
            continue
        root = subject_data_root(input_path)
        candidate = (root / supplied).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and _is_supported_image(candidate):
            return candidate
    return None


def validate_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if (parsed.scheme != "https" or parsed.hostname != "api.siliconflow.cn" or
            parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)):
        raise ValueError("endpoint must be HTTPS on trusted host api.siliconflow.cn")
    return endpoint


def needs_vision(question: dict[str, Any], input_path: Path) -> Path | None:
    if question.get("requires_visual_review"):
        return image_path(question, input_path)
    # Vision is also useful when textual choices carry no distinguishing content.
    options = question["options"].values()
    if not all(PLACEHOLDER_RE.search(option) for option in options):
        return None
    return image_path(question, input_path)


def make_messages(question: dict[str, Any], image: Path | None) -> list[dict[str, Any]]:
    # Intentionally construct this from only stem/options: never serialize the row,
    # whose `answer` member is the answer key being audited.
    options = "\n".join(f"{letter}. {question['options'][letter]}" for letter in "ABCD")
    prompt = (
        f"Question:\n{question['text']}\n\nChoices:\n{options}\n\n"
        "Allowed units:\n- " + "\n- ".join(UNITS)
    )
    if image is None:
        user_content: Any = prompt
    else:
        mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def extract_response(payload: Any) -> dict[str, str]:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("API response has no assistant content") from exc
    if isinstance(content, dict):
        value = content
    elif isinstance(content, str):
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("assistant content is not JSON") from exc
    else:
        raise ValueError("assistant content is not JSON")
    return validate_result(value)


def call_api(question: dict[str, Any], input_path: Path, model: str, vl_model: str,
             endpoint: str, key: str, retries: int, timeout: float) -> tuple[dict[str, str], str]:
    endpoint = validate_endpoint(endpoint)
    image = needs_vision(question, input_path)
    selected_model = vl_model if image is not None else model
    body = {
        "model": selected_model,
        "messages": make_messages(question, image),
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": SCHEMA},
    }
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            endpoint, data=encoded, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return extract_response(payload), selected_model
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(8.0, (2 ** attempt) + random.random() * 0.25))
    raise RuntimeError(f"question {question['number']} failed after {retries + 1} attempts: {last_error}")


def enrich(question: dict[str, Any], result: dict[str, str], review_model: str) -> dict[str, Any]:
    enriched = dict(question)
    enriched.update({
        "unit": result["unit"],
        "explanation": result["explanation"],
        "model_answer": result["answer"],
        "answer_disputed": result["answer"] != question["answer"],
        "review_model": review_model,
    })
    return enriched


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture-results", type=Path)
    parser.add_argument("--cache", type=Path, help="default: <output>.cache.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--vl-model", default=DEFAULT_VL_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    if args.concurrency < 1 or args.retries < 0 or args.timeout <= 0:
        parser.error("concurrency must be positive, retries nonnegative, and timeout positive")
    try:
        validate_endpoint(args.endpoint)
    except (ValueError, urllib.parse.PortValueError) as exc:
        parser.error(str(exc))
    return args


def main() -> None:
    args = parse_args()
    raw_questions = load_json(args.input, "input")
    if not isinstance(raw_questions, list):
        raise ValueError("input must be a JSON array")
    questions = [validate_question(item, index) for index, item in enumerate(raw_questions)]
    numbers = [item["number"] for item in questions]
    if len(numbers) != len(set(numbers)):
        raise ValueError("question numbers must be unique")

    if args.fixture_results:
        fixtures = load_json(args.fixture_results, "fixtures")
        if not isinstance(fixtures, dict):
            raise ValueError("fixture results must be an object keyed by question number")
        missing = [number for number in numbers if str(number) not in fixtures]
        if missing:
            raise ValueError(f"fixture results missing questions: {missing}")
        output = [
            enrich(question, validate_result(fixtures[str(question["number"])], f"fixture {question['number']}"), "fixture")
            for question in questions
        ]
        atomic_json(args.output, output)
        return

    api_key = os.environ.get("SILICONFLOW_KEY")
    if not api_key:
        raise ValueError("SILICONFLOW_KEY is required unless --fixture-results is used")
    cache_path = args.cache or args.output.with_name(args.output.name + ".cache.json")
    cache: dict[str, Any] = {}
    if cache_path.exists():
        loaded = load_json(cache_path, "cache")
        if not isinstance(loaded, dict):
            raise ValueError("cache must be a JSON object")
        cache = loaded

    output: list[dict[str, Any]] = [dict(question) for question in questions]
    lock = threading.Lock()

    def solve(index: int) -> tuple[int, dict[str, str], str, str]:
        question = questions[index]
        image = needs_vision(question, args.input)
        selected_model = args.vl_model if image is not None else args.model
        key = cache_key(question, selected_model)
        if key in cache:
            return index, validate_result(cache[key], f"cache entry for question {question['number']}"), selected_model, key
        result, actual_model = call_api(
            question, args.input, args.model, args.vl_model, args.endpoint,
            api_key, args.retries, args.timeout,
        )
        return index, result, actual_model, key

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(solve, index) for index in range(len(questions))]
        try:
            for future in concurrent.futures.as_completed(futures):
                index, result, selected_model, key = future.result()
                with lock:
                    cache[key] = result
                    output[index] = enrich(questions[index], result, selected_model)
                    atomic_json(cache_path, cache)
                    atomic_json(args.output, output)
        except BaseException:
            for future in futures:
                future.cancel()
            raise


if __name__ == "__main__":
    main()
