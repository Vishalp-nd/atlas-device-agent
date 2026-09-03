#!/usr/bin/env python3
"""Map ERROR and INFO descriptions from a CSV to semantic priority buckets.

The script embeds each description and compares it against fixed semantic
priority prototypes. Each row is assigned the closest priority bucket for its
TYPE and can be written back to a CSV for review.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np


NULL_LIKE_VALUES = {"", "\\N", "NULL", "NONE", "N/A", "NA"}

ERROR_PRIORITY_PROTOTYPES = {
    "P0": "direct video/data loss, recording failure, session loss, missing video, corrupted video, file generation failure",
    "P1": "major telemetry/safety signal loss, GPS loss, IMU loss, DMS unavailable, sensor outage, safety signal missing, Config related",
    "P2": "moderate functional impact, bluetooth failure, battery issue, reset issue, subsystem malfunction",
    "P3": "connectivity/auxiliary impact, network issue, modem issue, cloud communication failure, upload/connectivity problem",
    "P4": "minor/no immediate loss, routine warning, debug issue, non-critical status",
}

INFO_PRIORITY_PROTOTYPES = {
    "P100": "info related to video/data loss context, session lifecycle, recording lifecycle, file creation, video pipeline context",
    "P101": "major telemetry/safety signal context, GPS status, IMU status, DMS status, sensor state",
    "P102": "moderate functional relevance, bluetooth status, battery status, reset status, subsystem state",
    "P103": "connectivity/auxiliary info, network status, modem status, cloud status, connectivity context",
    "P104": "minor/routine info, heartbeat, periodic status, debug info, routine health message",
}


def normalize_optional_value(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if normalized.upper() in NULL_LIKE_VALUES:
        return None
    return normalized


def normalize_row(row: dict, code_column: str, type_column: str, description_column: str, priority_column: str) -> dict:
    return {
        "CODE": row.get(code_column),
        "TYPE": (row.get(type_column) or "").strip().upper(),
        "description": normalize_optional_value(row.get(description_column)) or "",
        "priority": normalize_optional_value(row.get(priority_column)),
    }


def read_rows_from_csv(
    csv_path: Path,
    code_column: str,
    type_column: str,
    description_column: str,
    priority_column: str,
    only_unassigned: bool,
) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file {csv_path} has no header row.")

        required = {code_column, type_column, description_column}
        missing = [column for column in required if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV file {csv_path} is missing required column(s): {', '.join(missing)}")

        rows = []
        for raw_row in reader:
            row = normalize_row(raw_row, code_column, type_column, description_column, priority_column)
            if row["TYPE"] not in {"ERROR", "INFO"}:
                continue
            if not row["description"]:
                continue
            if only_unassigned and row["priority"]:
                continue
            row["_source_row"] = raw_row
            rows.append(row)
        return rows


def build_embedding_model(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is required for semantic clustering. Install it with: pip install sentence-transformers"
        ) from exc

    return SentenceTransformer(model_name)


def cosine_similarity_matrix(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.matmul(left, right.T)


def get_priority_prototypes(type_: str) -> dict[str, str]:
    if type_ == "ERROR":
        return ERROR_PRIORITY_PROTOTYPES
    if type_ == "INFO":
        return INFO_PRIORITY_PROTOTYPES
    raise ValueError(f"Unsupported TYPE: {type_}")


def assign_priority_by_semantic_similarity(rows: list[dict], model_name: str) -> tuple[list[dict], dict]:
    if not rows:
        return rows, {"counts": {}}

    model = build_embedding_model(model_name)
    type_ = rows[0]["TYPE"]
    prototypes = get_priority_prototypes(type_)
    prototype_labels = list(prototypes.keys())
    prototype_texts = [prototypes[label] for label in prototype_labels]

    description_embeddings = model.encode(
        [row["description"] for row in rows],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    prototype_embeddings = model.encode(
        prototype_texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    similarity = cosine_similarity_matrix(description_embeddings, prototype_embeddings)

    counts: dict[str, int] = {label: 0 for label in prototype_labels}
    for idx, row in enumerate(rows):
        ranked = np.argsort(similarity[idx])[::-1]
        best_idx = int(ranked[0])
        second_idx = int(ranked[1]) if len(ranked) > 1 else best_idx
        best_label = prototype_labels[best_idx]
        second_label = prototype_labels[second_idx]
        row["predicted_priority"] = best_label
        row["predicted_priority_description"] = prototypes[best_label]
        row["similarity_score"] = f"{float(similarity[idx][best_idx]):.6f}"
        row["second_best_priority"] = second_label
        row["second_best_score"] = f"{float(similarity[idx][second_idx]):.6f}"
        counts[best_label] += 1

    return rows, {"counts": counts}


def summarize_priority_mapping(type_: str, rows: list[dict], samples_per_priority: int, metrics: dict) -> None:
    if not rows:
        print(f"=== {type_} ===")
        print("No rows found.\n")
        return

    print(f"=== {type_} ===")
    print(f"Rows: {len(rows)}")
    print("Assigned priorities:")
    for priority, count in metrics["counts"].items():
        print(f"  {priority}: {count}")

    for priority in get_priority_prototypes(type_):
        priority_rows = [row for row in rows if row["predicted_priority"] == priority]
        if not priority_rows:
            continue

        print(f"\n{priority}: {len(priority_rows)} row(s)")
        print(f"Prototype: {priority_rows[0]['predicted_priority_description']}")
        for row in priority_rows[:samples_per_priority]:
            print(
                f"  - score={row['similarity_score']} second={row['second_best_priority']} ({row['second_best_score']}) description={row['description']}"
            )
    print()


def write_mapped_csv(output_path: Path, rows: list[dict]) -> None:
    if not rows:
        print(f"No rows to write to {output_path}")
        return

    fieldnames = list(rows[0]["_source_row"].keys()) + [
        "predicted_priority",
        "predicted_priority_description",
        "similarity_score",
        "second_best_priority",
        "second_best_score",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output_row = dict(row["_source_row"])
            output_row["predicted_priority"] = row.get("predicted_priority", "")
            output_row["predicted_priority_description"] = row.get("predicted_priority_description", "")
            output_row["similarity_score"] = row.get("similarity_score", "")
            output_row["second_best_priority"] = row.get("second_best_priority", "")
            output_row["second_best_score"] = row.get("second_best_score", "")
            writer.writerow(output_row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Input CSV path")
    parser.add_argument("--samples-per-priority", type=int, default=10)
    parser.add_argument("--code-column", default="CODE")
    parser.add_argument("--type-column", default="TYPE")
    parser.add_argument("--description-column", default="sample_description")
    parser.add_argument("--priority-column", default="priority")
    parser.add_argument(
        "--embedding-model",
        default="all-MiniLM-L6-v2",
        help="SentenceTransformer model name for semantic clustering.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional output CSV path. When set, writes each included row with predicted priority columns.",
    )
    parser.add_argument(
        "--include-assigned",
        action="store_true",
        help="Include rows that already have a priority; default is to cluster only unassigned rows.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    rows = read_rows_from_csv(
        csv_path=csv_path,
        code_column=args.code_column,
        type_column=args.type_column,
        description_column=args.description_column,
        priority_column=args.priority_column,
        only_unassigned=not args.include_assigned,
    )

    clustered_rows = []
    for type_ in ("ERROR", "INFO"):
        type_rows = [row for row in rows if row["TYPE"] == type_]
        type_rows, metrics = assign_priority_by_semantic_similarity(type_rows, model_name=args.embedding_model)
        clustered_rows.extend(type_rows)
        summarize_priority_mapping(type_, type_rows, samples_per_priority=args.samples_per_priority, metrics=metrics)

    if args.output_csv:
        write_mapped_csv(Path(args.output_csv), clustered_rows)
        print(f"Wrote mapped rows to {args.output_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())