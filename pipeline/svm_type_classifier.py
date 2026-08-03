#!/usr/bin/env python3
"""
Train and run a DESCRIPTION -> TYPE classifier using Linear SVM.

This script is designed for large CSV files and supports chunked inference
for millions of rows before database insertion.
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

VALID_LABELS = {"INFO", "ERROR"}


def _normalize_description(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str)


def _normalize_type(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def _prepare_features(df: pd.DataFrame, description_col: str) -> pd.Series:
    if description_col not in df.columns:
        raise ValueError(
            f"Input must contain column '{description_col}'. "
            f"Found: {list(df.columns)}"
        )

    return _normalize_description(df[description_col])


def _build_pipeline(max_features: int, ngram_max: int, max_iter: int) -> Pipeline:
    preprocess = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, ngram_max),
        min_df=2,
        max_features=max_features,
        sublinear_tf=True,
    )

    model = LinearSVC(class_weight="balanced", random_state=42, max_iter=max_iter)

    return Pipeline(
        steps=[
            ("preprocess", preprocess),
            ("model", model),
        ]
    )


def train_model(args: argparse.Namespace) -> None:
    data_path = Path(args.data)
    model_out = Path(args.model_out)
    metrics_out = Path(args.metrics_out)

    if not data_path.exists():
        raise FileNotFoundError(f"Training file not found: {data_path}")

    df = pd.read_csv(data_path, dtype=str)
    if args.description_col not in df.columns or args.type_col not in df.columns:
        raise ValueError(
            f"Training CSV must contain columns '{args.description_col}' and '{args.type_col}'. "
            f"Found: {list(df.columns)}"
        )

    y = _normalize_type(df[args.type_col])
    valid_mask = y.isin(VALID_LABELS)
    dropped = int((~valid_mask).sum())

    df_valid = df.loc[valid_mask].copy()
    y_valid = y.loc[valid_mask].copy()
    x_valid = _prepare_features(df_valid, args.description_col)

    if len(x_valid) < 100:
        raise ValueError("Too few valid training rows after filtering to INFO/ERROR labels.")

    x_train, x_test, y_train, y_test = train_test_split(
        x_valid,
        y_valid,
        test_size=args.test_size,
        random_state=args.random_state,
        stratify=y_valid,
    )

    pipeline = _build_pipeline(
        max_features=args.max_features,
        ngram_max=args.ngram_max,
        max_iter=args.max_iter,
    )
    pipeline.fit(x_train, y_train)

    y_pred = pipeline.predict(x_test)

    labels = ["ERROR", "INFO"]
    report = classification_report(y_test, y_pred, labels=labels, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_test, y_pred, labels=labels)

    metrics: dict[str, Any] = {
        "data_path": str(data_path),
        "rows_total": int(len(df)),
        "rows_valid": int(len(x_valid)),
        "rows_dropped_invalid_type": dropped,
        "train_rows": int(len(x_train)),
        "test_rows": int(len(x_test)),
        "test_size": args.test_size,
        "labels": labels,
        "label_distribution": {
            "train": y_train.value_counts().to_dict(),
            "test": y_test.value_counts().to_dict(),
        },
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
        "feature_config": {
            "max_features": args.max_features,
            "ngram_range": [1, args.ngram_max],
            "max_iter": args.max_iter,
            "description_feature": "tfidf",
        },
    }

    model_out.parent.mkdir(parents=True, exist_ok=True)
    metrics_out.parent.mkdir(parents=True, exist_ok=True)

    with model_out.open("wb") as f:
        pickle.dump(pipeline, f)
    metrics_out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(f"Model saved: {model_out}")
    print(f"Metrics saved: {metrics_out}")
    print(f"Rows used: {len(x_valid)} / {len(df)} (dropped: {dropped})")
    print(f"Test accuracy: {report['accuracy']:.4f}")
    print("ERROR precision/recall/f1:", end=" ")
    print(
        f"{report['ERROR']['precision']:.4f} / "
        f"{report['ERROR']['recall']:.4f} / "
        f"{report['ERROR']['f1-score']:.4f}"
    )


def predict_model(args: argparse.Namespace) -> None:
    model_path = Path(args.model)
    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)

    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    with model_path.open("rb") as f:
        pipeline: Pipeline = pickle.load(f)

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    first_chunk = True
    total_rows = 0

    for chunk in pd.read_csv(input_csv, dtype=str, chunksize=args.chunksize):
        x_chunk = _prepare_features(chunk, args.description_col)
        predicted = pipeline.predict(x_chunk)

        out_chunk = chunk.copy()
        out_chunk[args.output_type_col] = predicted

        if args.include_margin:
            model = pipeline.named_steps["model"]
            if hasattr(model, "decision_function"):
                margin = model.decision_function(pipeline.named_steps["preprocess"].transform(x_chunk))
                if getattr(margin, "ndim", 1) > 1:
                    # Multi-class fallback; not expected here but kept safe.
                    margin = margin.max(axis=1)
                out_chunk[args.margin_col] = margin

        out_chunk.to_csv(output_csv, mode="w" if first_chunk else "a", header=first_chunk, index=False)
        first_chunk = False
        total_rows += len(out_chunk)

    print(f"Prediction completed. Rows written: {total_rows}")
    print(f"Output file: {output_csv}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Linear SVM classifier for TYPE using DESCRIPTION only",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="Train model and evaluate on train/test split")
    train_parser.add_argument("--data", required=True, help="Path to labeled training CSV")
    train_parser.add_argument("--description-col", default="DESCRIPTION", help="Input description column name")
    train_parser.add_argument("--type-col", default="TYPE", help="Target label column name")
    train_parser.add_argument("--test-size", type=float, default=0.2, help="Test split ratio")
    train_parser.add_argument("--random-state", type=int, default=42, help="Random seed")
    train_parser.add_argument("--max-features", type=int, default=250000, help="Max TF-IDF features")
    train_parser.add_argument("--ngram-max", type=int, default=2, choices=[1, 2, 3], help="Max n-gram size")
    train_parser.add_argument("--max-iter", type=int, default=20000, help="Max iterations for LinearSVC")
    train_parser.add_argument(
        "--model-out",
        default="pipeline/models/svm_type_classifier.pkl",
        help="Output path for serialized model (.pkl)",
    )
    train_parser.add_argument(
        "--metrics-out",
        default="pipeline/models/svm_type_classifier_metrics.json",
        help="Output path for evaluation metrics JSON",
    )
    train_parser.set_defaults(func=train_model)

    predict_parser = subparsers.add_parser("predict", help="Predict TYPE for new CSV rows in chunks")
    predict_parser.add_argument("--model", required=True, help="Path to trained model pickle (.pkl)")
    predict_parser.add_argument("--input-csv", required=True, help="CSV file with unclassified rows")
    predict_parser.add_argument("--output-csv", required=True, help="Output CSV path with predicted TYPE")
    predict_parser.add_argument("--description-col", default="DESCRIPTION", help="Input description column name")
    predict_parser.add_argument("--output-type-col", default="TYPE", help="Predicted label output column name")
    predict_parser.add_argument("--chunksize", type=int, default=100000, help="Chunk size for large CSV inference")
    predict_parser.add_argument("--include-margin", action="store_true", help="Add SVM margin score column")
    predict_parser.add_argument("--margin-col", default="SVM_MARGIN", help="Margin score column name")
    predict_parser.set_defaults(func=predict_model)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
