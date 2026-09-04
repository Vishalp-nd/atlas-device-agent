#!/usr/bin/env python3
"""Shared TYPE/priority classification for critical-info rows.

Classification order for each row's DESCRIPTION:
1. Normalize the description (digit-bearing tokens -> <N>, trimmed) and look it up in
   unique_cinfo_op_mapped.json. A hit supplies both TYPE and priority directly.
2. Rows that don't match the JSON map fall back to the trained SVM model for TYPE only
   (predicted on the raw, non-normalized description text, matching how the model was trained).
3. SVM-classified rows still need a priority: grouped by (normalized_description, TYPE), assigned
   via the same semantic-similarity-to-prototype logic as cluster_priority_candidates.py.

Newly-discovered normalized patterns (from step 3) are tracked so the caller can persist them back
to unique_cinfo_op_mapped.json and the ClickHouse unique_cinfo_priority_map table, so future runs
match more of them via the fast JSON-map path.

NORMALIZE_TOKEN_REGEX/NORMALIZE_TOKEN_REPL are the single source of truth for this normalization --
reupdate_clickhouse_types.py and scripts/sync_clickhouse_cinfo_priority_map.py build their
ClickHouse-side SQL expressions from these same constants so the SQL-side and Python-side
normalization can never drift apart.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = PIPELINE_DIR.parents[0] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cinfo_normalization import (  # noqa: E402
    NORMALIZE_TOKEN_REGEX,
    NORMALIZE_TOKEN_REPL,
    normalize_description,
)
from cluster_priority_candidates import (  # noqa: E402
    build_embedding_model,
    cosine_similarity_matrix,
    get_priority_prototypes,
)
from sync_clickhouse_cinfo_priority_map import (  # noqa: E402
    build_existing_rows_sql,
    insert_clickhouse_tsv_rows,
    run_clickhouse_tsv_query,
)

DEFAULT_JSON_PATH = PIPELINE_DIR / "unique_cinfo_op_mapped.json"
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

__all__ = [
    "NORMALIZE_TOKEN_REGEX",
    "NORMALIZE_TOKEN_REPL",
    "normalize_description",
    "DEFAULT_JSON_PATH",
    "load_type_priority_map",
    "CinfoClassifier",
    "append_new_patterns_to_json",
    "upsert_new_patterns_to_clickhouse",
]


def load_type_priority_map(json_path: Path) -> dict[str, tuple[str, str]]:
    type_priority_map: dict[str, tuple[str, str]] = {}
    with Path(json_path).open() as json_file:
        rows = json.load(json_file)

    for index, row in enumerate(rows, start=1):
        pattern = (row.get("description_pattern") or "").strip()
        event_type = (row.get("TYPE") or "").strip().upper()
        priority = (row.get("priority") or "").strip().upper()
        if not event_type:
            raise ValueError(f"Row {index} in {json_path} has an empty TYPE")
        if not priority:
            raise ValueError(f"Row {index} in {json_path} has an empty priority")
        if pattern in type_priority_map:
            continue
        type_priority_map[pattern] = (event_type, priority)
    return type_priority_map


class CinfoClassifier:
    def __init__(self, json_path: Path, svm_model, embedding_model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.json_path = Path(json_path)
        self.type_priority_map = load_type_priority_map(self.json_path)
        self.svm_model = svm_model
        self.embedding_model_name = embedding_model_name
        self._embedding_model = None
        # (normalized_description, type) -> priority, covers every SVM-classified pattern seen
        # so far this run so the embedding model is invoked once per unique pair, not per row.
        self._priority_cache: dict[tuple[str, str], str] = {}
        self._new_patterns: dict[str, dict] = {}
        self._description_col = "DESCRIPTION"
        self._code_col = "CODE"

    def classify(self, df: pd.DataFrame, description_col: str = "DESCRIPTION", code_col: str = "CODE") -> pd.DataFrame:
        self._description_col = description_col
        self._code_col = code_col

        df = df.copy()
        descriptions = df[description_col].fillna("").astype(str)
        normalized = descriptions.map(normalize_description)
        matched = normalized.map(self.type_priority_map.get)

        df["normalized_description"] = normalized
        df["matched_via"] = matched.map(lambda m: "json" if m is not None else "svm")
        df["type"] = matched.map(lambda m: m[0] if m is not None else None)
        df["priority"] = matched.map(lambda m: m[1] if m is not None else None)

        svm_mask = df["matched_via"] == "svm"
        if svm_mask.any():
            df.loc[svm_mask, "type"] = self._predict_svm(descriptions[svm_mask])

        return df

    def _predict_svm(self, raw_descriptions: pd.Series) -> list[str]:
        try:
            predictions = self.svm_model.predict(pd.Series(raw_descriptions.tolist(), dtype="string"))
        except Exception as exc:
            raise RuntimeError(
                "Model prediction failed. The loaded model is likely incompatible with the current "
                "description-only pipeline. Retrain the model with pipeline/svm_type_classifier.py "
                "and rerun the pipeline."
            ) from exc

        if len(predictions) != len(raw_descriptions):
            raise RuntimeError(
                f"Model returned {len(predictions)} predictions for {len(raw_descriptions)} rows. "
                "This usually means an old CODE+DESCRIPTION model is being used with the new "
                "description-only pipeline. Retrain the model and rerun."
            )
        return [str(prediction) for prediction in predictions]

    def assign_missing_priorities(self, df: pd.DataFrame) -> pd.DataFrame:
        pending_mask = (df["matched_via"] == "svm") & df["priority"].isna()
        if not pending_mask.any():
            return df

        pending = df.loc[pending_mask]
        groups: dict[tuple[str, str], pd.Series] = {}
        for _, row in pending.drop_duplicates(subset=["normalized_description", "type"]).iterrows():
            key = (row["normalized_description"], row["type"])
            if key in self._priority_cache:
                continue
            groups[key] = row

        if groups:
            self._embed_and_cache(groups)

        df.loc[pending_mask, "priority"] = [
            self._priority_cache.get((normalized_description, type_))
            for normalized_description, type_ in zip(
                df.loc[pending_mask, "normalized_description"], df.loc[pending_mask, "type"]
            )
        ]
        return df

    def _embed_and_cache(self, groups: dict[tuple[str, str], pd.Series]) -> None:
        if self._embedding_model is None:
            self._embedding_model = build_embedding_model(self.embedding_model_name)

        for type_ in ("ERROR", "INFO"):
            type_items = [(key, row) for key, row in groups.items() if key[1] == type_]
            if not type_items:
                continue

            prototypes = get_priority_prototypes(type_)
            labels = list(prototypes.keys())
            prototype_embeddings = self._embedding_model.encode(
                [prototypes[label] for label in labels],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            description_texts = [row[self._description_col] or "" for _, row in type_items]
            description_embeddings = self._embedding_model.encode(
                description_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            similarity = cosine_similarity_matrix(description_embeddings, prototype_embeddings)

            for idx, (key, row) in enumerate(type_items):
                best_label = labels[int(np.argsort(similarity[idx])[::-1][0])]
                self._priority_cache[key] = best_label

                normalized_description, type_value = key
                if normalized_description not in self.type_priority_map:
                    self._new_patterns[normalized_description] = {
                        "description_pattern": normalized_description,
                        "TYPE": type_value,
                        "priority": best_label,
                        "sample_description": row[self._description_col],
                        "sample_code": row.get(self._code_col),
                    }

    def new_patterns(self) -> list[dict]:
        return list(self._new_patterns.values())


def append_new_patterns_to_json(json_path: Path, new_rows: list[dict]) -> int:
    if not new_rows:
        return 0

    json_path = Path(json_path)
    with json_path.open() as json_file:
        existing = json.load(json_file)

    existing_patterns = {(row.get("description_pattern") or "").strip() for row in existing}
    appended = 0
    for row in new_rows:
        pattern = row["description_pattern"]
        if pattern in existing_patterns:
            continue
        existing.append(
            {
                "description_pattern": pattern,
                "TYPE": row["TYPE"],
                "priority": row["priority"],
            }
        )
        existing_patterns.add(pattern)
        appended += 1

    if appended:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(json_path.parent), prefix=f".{json_path.name}.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as tmp_file:
                json.dump(existing, tmp_file, indent=2)
                tmp_file.write("\n")
            os.replace(tmp_path, json_path)
        except Exception:
            os.unlink(tmp_path)
            raise

    return appended


def upsert_new_patterns_to_clickhouse(params: dict[str, object], target_table: str, new_rows: list[dict]) -> int:
    if not new_rows:
        return 0

    existing_rows = run_clickhouse_tsv_query(params, build_existing_rows_sql(target_table))
    existing_keys = {(row[0], row[3], row[2]) for row in existing_rows if len(row) >= 4}

    rows_to_insert: list[tuple[str, str, str, str, str]] = []
    for row in new_rows:
        code_value = row.get("sample_code")
        code_str = "" if code_value is None else str(code_value)
        key = (code_str, row["TYPE"], row["description_pattern"])
        if key in existing_keys:
            continue
        rows_to_insert.append(
            (
                code_str,
                row.get("sample_description") or "",
                row["description_pattern"],
                row["TYPE"],
                row["priority"],
            )
        )

    insert_clickhouse_tsv_rows(params, target_table, rows_to_insert)
    return len(rows_to_insert)
