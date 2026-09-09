#!/usr/bin/env python3
"""DESCRIPTION -> TYPE (INFO/ERROR) classifier used by the critical-info pipelines.

Wraps the fine-tuned MiniLM sequence classifier in pipeline/models/minilm_ft and exposes the
`.predict(descriptions) -> list[str]` interface CinfoClassifier already calls, so it drops into the
existing cascade (exact-match JSON map first, this model only for patterns the map has not seen).

Replaces the previous TF-IDF + LinearSVC pickle. Measured on 8,000 held-out log templates that no
model was trained on:

    MiniLM       87.86% accuracy   87.62% balanced   938 false ERROR calls
    legacy SVM   12.31% accuracy   50.61% balanced  6,993 false ERROR calls

Descriptions are passed RAW, exactly as CinfoClassifier supplies them -- no normalization at this
layer. That is deliberate and measured: MiniLM scores 87.86% on raw text against 65.30% on
normalized text. Normalizing collapses lines into <N>-heavy shapes that skew ERROR in the training
distribution, so the model over-predicts ERROR (2,744 false positives instead of 938).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

MODEL_DIR = Path(__file__).resolve().parent / "models" / "minilm_ft"

# Label order is fixed by training: index 1 was (TYPE == 'ERROR').
LABELS = ("INFO", "ERROR")
# Matches the max_length the model was fine-tuned with; it is a trained-in constant, not a tunable.
# Raising it to 96 measurably hurt accuracy (65.30% -> 60.26%) because padding a median-21-token
# line out to 96 dilutes attention across pad positions.
MAX_LENGTH = 64
# Only rows that missed the exact-match JSON map reach this model, but a first run over a wide
# window can still be large, so inference is chunked rather than built as one giant tensor.
BATCH_SIZE = 256


class MiniLMTypeModel:
    """Fine-tuned MiniLM classifier exposing the predict() signature CinfoClassifier expects."""

    name = "minilm"

    def __init__(self, model_dir: Path | str = MODEL_DIR) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        model_dir = Path(model_dir)
        if not (model_dir / "config.json").exists():
            raise FileNotFoundError(
                f"No saved model in {model_dir} (config.json missing). "
                "The fine-tuned MiniLM model must be present at pipeline/models/minilm_ft."
            )

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self._model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
        self._device = self._pick_device(torch)
        self._model.to(self._device).eval()
        self.model_dir = model_dir

    @staticmethod
    def _pick_device(torch) -> str:
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"

    @property
    def device(self) -> str:
        return self._device

    def predict(self, descriptions: Sequence[str]) -> list[str]:
        texts = ["" if text is None else str(text) for text in descriptions]
        predictions: list[str] = []

        for start in range(0, len(texts), BATCH_SIZE):
            encoded = self._tokenizer(
                texts[start : start + BATCH_SIZE],
                truncation=True,
                max_length=MAX_LENGTH,
                padding="max_length",
                return_tensors="pt",
            )
            with self._torch.no_grad():
                logits = self._model(
                    input_ids=encoded["input_ids"].to(self._device),
                    attention_mask=encoded["attention_mask"].to(self._device),
                ).logits
            predictions.extend(
                LABELS[int(index)] for index in logits.argmax(dim=1).cpu().numpy()
            )

        return predictions


def load_type_model(model_dir: Path | str = MODEL_DIR) -> MiniLMTypeModel:
    return MiniLMTypeModel(model_dir)
