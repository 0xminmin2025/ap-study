import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "docs/data/stats/questions"

OFFICIAL_UNITS = {
    "Exploring One-Variable Data and Collecting Data",
    "Probability, Random Variables, and Probability Distributions",
    "Inference for Categorical Data: Proportions",
    "Inference for Quantitative Data: Means",
    "Regression Analysis",
}


def test_statistics_uses_only_the_five_2026_27_units():
    for name in ("mcq.json", "frq.json"):
        questions = json.loads((STATS / name).read_text())
        assert questions
        assert {q["unit"] for q in questions} <= OFFICIAL_UNITS


def test_stats_classifier_lists_the_five_official_units():
    source = (ROOT / "tag_units.py").read_text()
    for unit in OFFICIAL_UNITS:
        assert unit in source
    assert '"stats": [' in source
    assert '"Chi-Square"' not in source.split('"human-geo"')[0]
