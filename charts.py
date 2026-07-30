#!/usr/bin/env python3
"""Generate conservative, source-verified chart-card candidates.

This is intentionally a standalone scratch stage. It does not modify
chunks.json and render.py does not consume its output.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import common
from figures import Figure, extract_figures


_CUES = [
    ("rose from/to", re.compile(r"\brose\s+from\b.+?\bto\b", re.I | re.S)),
    ("fell from/to", re.compile(r"\bfell\s+from\b.+?\bto\b", re.I | re.S)),
    ("was/now", re.compile(
        r"(?:\bwas\b.+?\bnow\b|\bnow\b.+?\bwas\b)",
        re.I | re.S,
    )),
    ("year comparison", re.compile(
        r"\bin\s+(?:nineteen|twenty)\b.+?\bin\s+(?:nineteen|twenty)\b",
        re.I | re.S,
    )),
    ("against", re.compile(r"\bagainst\b", re.I)),
    ("compared with", re.compile(r"\bcompared\s+with\b", re.I)),
]


@dataclass
class ChartCandidate:
    chunk_index: int
    cue: str
    figures: list[Figure]
    labels: list[str]
    chart_type: str
    title: str
    unit: str
    ratio: float


def _comparison_cue(text: str) -> str | None:
    for label, pattern in _CUES:
        if pattern.search(text):
            return label
    return None


def _verified_in_source(text: str, figure: Figure) -> bool:
    if text[figure.start:figure.end] != figure.raw:
        return False
    reparsed = extract_figures(figure.raw)
    return any(
        abs(item.value - figure.value) <= max(1e-9, abs(figure.value) * 1e-12)
        and item.unit == figure.unit
        for item in reparsed
    )


def _year_labels(figures: list[Figure], values: list[Figure]) -> list[str] | None:
    years = [f for f in figures if f.is_year]
    labels: list[str] = []
    used: set[int] = set()
    for value in values:
        preceding = [
            (i, year) for i, year in enumerate(years)
            if year.end <= value.start and i not in used
        ]
        if not preceding:
            return None
        i, year = preceding[-1]
        used.add(i)
        labels.append(year.display)
    return labels if len(set(labels)) == len(labels) else None


def _fallback_labels(cue: str, count: int) -> list[str]:
    if count == 2:
        if cue in {"rose from/to", "fell from/to"}:
            return ["From", "To"]
        if cue == "was/now":
            return ["Then", "Now"]
        return ["First", "Second"]
    if count == 3:
        return ["First", "Middle", "Last"]
    return [f"Point {i + 1}" for i in range(count)]


def _context_labels(
    text: str,
    all_figures: list[Figure],
    values: list[Figure],
    cue: str,
) -> tuple[list[str], bool]:
    """Use only explicit temporal words/years; otherwise use neutral labels."""
    labels: list[str] = []
    temporal = False
    for i, value in enumerate(values):
        left = values[i - 1].end if i else 0
        context = text[left:value.start].lower()
        years = [
            year for year in all_figures
            if year.is_year and left <= year.start and year.end <= value.start
        ]
        if years:
            labels.append(years[-1].display)
            temporal = True
        elif "now" in context:
            labels.append("Now")
            temporal = True
        elif "peak" in context:
            labels.append("Peak")
            temporal = True
        elif "then" in context:
            labels.append("Then")
            temporal = True
        else:
            labels.append("")

    # A trailing "by/in <year>" labels the immediately preceding value.
    for i, value in enumerate(values):
        right = values[i + 1].start if i + 1 < len(values) else len(text)
        trailing_years = [
            year for year in all_figures
            if (
                year.is_year
                and value.end <= year.start < right
                and re.search(r"\bby\b", text[value.end:year.start], re.I)
            )
        ]
        if trailing_years:
            labels[i] = trailing_years[0].display
            temporal = True

    fallback = _fallback_labels(cue, len(values))
    labels = [label or fallback[i] for i, label in enumerate(labels)]
    return labels, temporal


def _title(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    words = sentence.split()
    if len(words) > 12:
        sentence = " ".join(words[:12]) + "…"
    return sentence


def detect_candidate(chunk: dict, min_ratio: float) -> tuple[ChartCandidate | None, str]:
    text = str(chunk.get("text", ""))
    cue = _comparison_cue(text)
    all_figures = extract_figures(text)
    if cue is None:
        return None, "no explicit comparison phrasing"

    groups: dict[str, list[Figure]] = defaultdict(list)
    for figure in all_figures:
        if figure.is_year or figure.value == 0:
            continue
        groups[figure.unit].append(figure)
    groups = {
        unit: values for unit, values in groups.items()
        if len(values) >= 2
    }
    if not groups:
        return None, "comparison cue found, but fewer than two figures share a unit"

    ranked: list[tuple[float, str, list[Figure]]] = []
    for unit, values in groups.items():
        magnitudes = [abs(f.value) for f in values if f.value != 0]
        ratio = max(magnitudes) / min(magnitudes)
        ranked.append((ratio, unit, values))
    ratio, unit, values = max(ranked, key=lambda item: (item[0], len(item[2])))
    if ratio < min_ratio:
        return None, f"largest same-unit ratio {ratio:.2f} is below {min_ratio:.2f}"
    if not all(_verified_in_source(text, figure) for figure in values):
        return None, "source-span verification failed"

    labels = _year_labels(all_figures, values)
    if labels is None:
        labels, temporal = _context_labels(text, all_figures, values, cue)
    else:
        temporal = True
    over_time = len(values) >= 3 and temporal
    chart_type = "line" if over_time else "bar"
    return ChartCandidate(
        chunk_index=int(chunk["index"]),
        cue=cue,
        figures=values,
        labels=labels,
        chart_type=chart_type,
        title=_title(text),
        unit=unit,
        ratio=ratio,
    ), "accepted"


def render_candidate(candidate: ChartCandidate, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    background = "#101112"
    text_colour = "#F2EBDD"
    amber = "#C99245"
    amber_soft = "#947349"
    line_colour = "#4B4D4F"

    fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    fig.patch.set_facecolor(background)
    ax.set_facecolor(background)
    values = [figure.value for figure in candidate.figures]
    x = list(range(len(values)))

    if candidate.chart_type == "line":
        ax.plot(
            x, values, color=amber, linewidth=3.0, marker="o",
            markersize=10, markerfacecolor=amber, markeredgewidth=0,
        )
    else:
        colours = [amber_soft] * len(values)
        colours[-1] = amber
        bars = ax.bar(x, values, width=0.52, color=colours, edgecolor="none")
        for bar in bars:
            bar.set_alpha(0.96)

    value_span = max(values) - min(values)
    label_offset = max(max(values) * 0.035, value_span * 0.04, 0.1)
    for pos, (value, source) in enumerate(zip(values, candidate.figures)):
        ax.text(
            pos, value + label_offset, source.display,
            ha="center", va="bottom", color=text_colour,
            fontsize=28, fontweight="semibold",
        )

    ax.set_xticks(x, candidate.labels)
    ax.tick_params(axis="x", colors=text_colour, labelsize=24, pad=18, length=0)
    ax.tick_params(axis="y", left=False, labelleft=False)
    ax.grid(False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(line_colour)
    ax.spines["bottom"].set_linewidth(1.2)
    ax.set_ylim(0, max(values) * 1.22)
    ax.set_xlim(-0.65, len(values) - 0.35)

    fig.text(
        0.08, 0.86, candidate.title,
        color=text_colour, fontsize=34, fontweight="semibold",
        ha="left", va="top",
    )
    fig.text(
        0.08, 0.785,
        f"Source-verified comparison · chunk {candidate.chunk_index:03d}",
        color="#A8A39A", fontsize=17, ha="left", va="top",
    )
    plt.subplots_adjust(left=0.10, right=0.92, bottom=0.18, top=0.70)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor=fig.get_facecolor(), dpi=100)
    plt.close(fig)


def generate_for_work_dir(work_dir: Path, cfg: dict, log) -> dict:
    data = common.read_chunks(work_dir)
    chunks = data.get("chunks") or []
    chart_cfg = cfg.get("charts") or {}
    minimum_ratio = float(chart_cfg.get("min_ratio", 1.5))
    maximum = int(chart_cfg.get("max_per_video", 6))
    output_dir = work_dir / "chart_candidates"
    output_dir.mkdir(parents=True, exist_ok=True)

    accepted: list[dict] = []
    rejected: list[dict] = []
    for chunk in chunks:
        figures = extract_figures(str(chunk.get("text", "")))
        cue = _comparison_cue(str(chunk.get("text", "")))
        # Keep the rejection report focused on plausible candidates.
        if cue is None and len(figures) < 2:
            continue
        candidate, reason = detect_candidate(chunk, minimum_ratio)
        if candidate is None:
            rejected.append({
                "chunk_index": int(chunk["index"]),
                "reason": reason,
                "cue": cue,
                "figures": [
                    {
                        "raw": f.raw, "display": f.display,
                        "value": f.value, "unit": f.unit,
                    }
                    for f in figures
                ],
            })
            continue
        if len(accepted) >= maximum:
            rejected.append({
                "chunk_index": int(chunk["index"]),
                "reason": f"accepted by detector but exceeds max_per_video={maximum}",
                "cue": candidate.cue,
                "figures": [f.display for f in candidate.figures],
            })
            continue
        output = output_dir / f"chart_chunk_{candidate.chunk_index:03d}.png"
        render_candidate(candidate, output)
        accepted.append({
            "chunk_index": candidate.chunk_index,
            "cue": candidate.cue,
            "chart_type": candidate.chart_type,
            "unit": candidate.unit,
            "ratio": round(candidate.ratio, 4),
            "labels": candidate.labels,
            "values": [
                {
                    "raw": f.raw, "display": f.display, "value": f.value,
                    "source_start": f.start, "source_end": f.end,
                }
                for f in candidate.figures
            ],
            "output": str(output.relative_to(work_dir)),
        })

    report = {
        "date": data.get("date"),
        "min_ratio": minimum_ratio,
        "max_per_video": maximum,
        "accepted": accepted,
        "rejected": rejected,
        "render_integration": "disabled; scratch candidates only",
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info(
        "%s: %d chart candidate(s) accepted, %d plausible candidate(s) rejected",
        data.get("date"), len(accepted), len(rejected),
    )
    return report


def main() -> int:
    parser = common.build_arg_parser("Generate source-verified chart candidates.")
    args = parser.parse_args()
    ctx = common.StageContext(args, "charts")
    log = ctx.log
    ctx.ensure_dirs()
    if not (ctx.config.get("charts") or {}).get("enabled", False):
        log.info("Chart candidates are disabled (charts.enabled=false); nothing to do.")
        return 0
    generate_for_work_dir(ctx.work_dir, ctx.config, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
