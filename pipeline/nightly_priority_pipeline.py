#!/usr/bin/env python3
"""Nightly post-poll pipeline: unique_critical_info -> unique_cinfo_priority_map -> priority.

Runs three stages, each gated on there being new work so a quiet night is a
cheap no-op:

  1. Diff the just-polled window (public.criticalinfo_snowflakes_data by
     default) against public.unique_critical_info and insert only genuinely
     new (CODE, CODE_AUX, TYPE, description_pattern) rows. Existing rows are
     left untouched.
  2. Diff public.unique_critical_info against public.unique_cinfo_priority_map
     on the same key *excluding CODE_AUX* (the ~450-row canonical table from
     WHY_UNIQUE_CINFO_MAPPING.md) and insert the missing combinations.
  3. Cluster every unique_cinfo_priority_map row still missing a priority
     (TF-IDF + KMeans, <=5 clusters chosen by silhouette score, separately for
     TYPE=ERROR and TYPE=INFO), ask Claude to label each cluster once
     (P0-P4 for ERROR, P100-P104 for INFO), and write the label back to both
     tables for every row in that cluster.

Stage 2/3 re-check live DB state rather than only acting on what the prior
stage just inserted this run, so a run that dies partway through is safe to
simply re-run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
from psycopg2.extras import execute_values
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score

from fetch_device_config import ENV_TO_DB_SECTION, connect_to_db, read_db_config
from query_unique_critical_descriptions import _to_like_pattern, query_unique_descriptions

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

ERROR_PRIORITIES = {
    "P0": "direct video/data loss",
    "P1": "major telemetry/safety signal loss",
    "P2": "moderate functional impact",
    "P3": "connectivity/auxiliary impact",
    "P4": "minor/no immediate loss",
}
INFO_PRIORITIES = {
    "P100": "info related to video/data loss context (e.g. session/recording lifecycle events)",
    "P101": "major telemetry/safety signal context (e.g. GPS/IMU/DMS status info)",
    "P102": "moderate functional relevance (e.g. BT/battery/reset status)",
    "P103": "connectivity/auxiliary info (e.g. network/modem/cloud status)",
    "P104": "minor/routine info, no impact (e.g. periodic heartbeat/debug info)",
}

JSON_BLOB_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Stage 1: new criticalinfo_snowflakes_data rows -> unique_critical_info
# ---------------------------------------------------------------------------

def fetch_new_source_rows(pg_conn, source_table: str, start_ts: str, end_ts: str) -> list[dict]:
    rows = query_unique_descriptions(
        pg_conn=pg_conn,
        table_name=source_table,
        start_date=start_ts,
        end_date=end_ts,
        device_version_substring=None,
        process_names=[],
        tenant_ids=[],
        limit=10_000_000,
        normalized=True,
    )
    for row in rows:
        row["description_pattern"] = _to_like_pattern(row.get("description_pattern"))
    return rows


def insert_new_unique_critical_info(pg_conn, rows: list[dict]) -> list[dict]:
    if not rows:
        return []

    with pg_conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMP TABLE tmp_new_uci (
                "CODE" double precision,
                "CODE_AUX" bigint,
                "TYPE" text,
                description_pattern text,
                sample_description text
            ) ON COMMIT DROP
            """
        )
        execute_values(
            cursor,
            """
            INSERT INTO tmp_new_uci ("CODE", "CODE_AUX", "TYPE", description_pattern, sample_description)
            VALUES %s
            """,
            [
                (
                    row.get("CODE"),
                    row.get("CODE_AUX"),
                    row.get("TYPE"),
                    row.get("description_pattern"),
                    row.get("sample_description"),
                )
                for row in rows
            ],
            page_size=5000,
        )
        cursor.execute("ANALYZE tmp_new_uci")

        cursor.execute(
            """
            INSERT INTO public.unique_critical_info
                ("CODE", "CODE_AUX", "TYPE", description_pattern, sample_description)
            SELECT DISTINCT s."CODE", s."CODE_AUX", s."TYPE", s.description_pattern, s.sample_description
            FROM tmp_new_uci s
            WHERE NOT EXISTS (
                SELECT 1 FROM public.unique_critical_info u
                WHERE u."CODE" IS NOT DISTINCT FROM s."CODE"
                  AND u."CODE_AUX" IS NOT DISTINCT FROM s."CODE_AUX"
                  AND u."TYPE" IS NOT DISTINCT FROM s."TYPE"
                  AND u.description_pattern IS NOT DISTINCT FROM s.description_pattern
            )
            RETURNING "CODE", "CODE_AUX", "TYPE", description_pattern, sample_description
            """
        )
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# Stage 2: unique_critical_info -> unique_cinfo_priority_map (excludes CODE_AUX)
# ---------------------------------------------------------------------------

def sync_unique_cinfo_priority_map(pg_conn) -> list[dict]:
    with pg_conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.unique_cinfo_priority_map
                ("CODE", "TYPE", description_pattern, sample_description)
            SELECT DISTINCT ON (u."CODE", u."TYPE", u.description_pattern)
                u."CODE", u."TYPE", u.description_pattern, u.sample_description
            FROM public.unique_critical_info u
            WHERE NOT EXISTS (
                SELECT 1 FROM public.unique_cinfo_priority_map m
                WHERE m."CODE" IS NOT DISTINCT FROM u."CODE"
                  AND m."TYPE" IS NOT DISTINCT FROM u."TYPE"
                  AND m.description_pattern IS NOT DISTINCT FROM u.description_pattern
            )
            ORDER BY u."CODE", u."TYPE", u.description_pattern, u.sample_description
            RETURNING "CODE", "TYPE", description_pattern, sample_description
            """
        )
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# Stage 3: cluster + label unlabeled unique_cinfo_priority_map rows
# ---------------------------------------------------------------------------

def fetch_unlabeled(pg_conn, type_: str) -> list[dict]:
    with pg_conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT "CODE", description_pattern, sample_description
            FROM public.unique_cinfo_priority_map
            WHERE "TYPE" = %s AND priority IS NULL
            """,
            (type_,),
        )
        return cursor.fetchall()


def choose_k_and_cluster(texts: list[str]):
    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_features=3000,
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b[a-zA-Z_][a-zA-Z_]+\b",
    )
    X = vectorizer.fit_transform(texts)
    n = X.shape[0]

    max_k = min(5, n)
    candidate_ks = list(range(2, max_k + 1)) if max_k >= 2 else [1]

    best_score = None
    best_k = candidate_ks[0]
    best_km = None
    for k in candidate_ks:
        km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(X)
        score = silhouette_score(X, km.labels_) if 2 <= k <= n - 1 else float("-inf")
        if best_score is None or score > best_score:
            best_score, best_k, best_km = score, k, km

    return best_km.labels_, vectorizer, X


def build_cluster_prompt(type_: str, terms: list[str], samples: list[str]) -> str:
    rubric = ERROR_PRIORITIES if type_ == "ERROR" else INFO_PRIORITIES
    tiers = "\n".join(f"{tier}: {desc}" for tier, desc in rubric.items())
    sample_lines = "\n".join(f"- {s}" for s in samples)
    return f"""You are triaging device telemetry event clusters for a fleet safety-camera product.
Classify the following cluster of {type_} event descriptions into exactly one severity tier.

Tiers:
{tiers}

Cluster top terms: {", ".join(terms)}
Sample descriptions:
{sample_lines}

Respond with ONLY a JSON object, no markdown, no extra text:
{{"priority": "<one of {', '.join(rubric)}>", "reason": "<short one-sentence justification>"}}
"""


def classify_cluster_with_claude(client, model: str, type_: str, terms: list[str], samples: list[str]) -> tuple[str, str]:
    rubric = ERROR_PRIORITIES if type_ == "ERROR" else INFO_PRIORITIES
    prompt = build_cluster_prompt(type_, terms, samples)

    message = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    match = JSON_BLOB_RE.search(text)
    payload = json.loads(match.group(0)) if match else json.loads(text)
    priority = str(payload.get("priority", "")).strip().upper()
    reason = str(payload.get("reason", "")).strip()
    if priority not in rubric:
        raise ValueError(f"Claude returned priority '{priority}' not in {list(rubric)} for TYPE={type_}")
    return priority, reason


def apply_cluster_priority(pg_conn, type_: str, cluster_rows: list[dict], priority: str) -> None:
    with pg_conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMP TABLE tmp_cluster_keys (
                "CODE" double precision,
                description_pattern text
            ) ON COMMIT DROP
            """
        )
        execute_values(
            cursor,
            'INSERT INTO tmp_cluster_keys ("CODE", description_pattern) VALUES %s',
            [(row.get("CODE"), row.get("description_pattern")) for row in cluster_rows],
            page_size=5000,
        )
        cursor.execute("ANALYZE tmp_cluster_keys")

        cursor.execute(
            """
            UPDATE public.unique_cinfo_priority_map m
            SET priority = %s
            FROM tmp_cluster_keys t
            WHERE m."TYPE" = %s
              AND m."CODE" IS NOT DISTINCT FROM t."CODE"
              AND m.description_pattern IS NOT DISTINCT FROM t.description_pattern
            """,
            (priority, type_),
        )
        cursor.execute(
            """
            UPDATE public.unique_critical_info u
            SET priority = %s
            FROM tmp_cluster_keys t
            WHERE u."TYPE" = %s
              AND u."CODE" IS NOT DISTINCT FROM t."CODE"
              AND u.description_pattern IS NOT DISTINCT FROM t.description_pattern
            """,
            (priority, type_),
        )
    pg_conn.commit()


def cluster_and_label_type(pg_conn, client, model: str, type_: str, samples_per_cluster: int, dry_run: bool) -> int:
    rows = fetch_unlabeled(pg_conn, type_)
    if not rows:
        print(f"  {type_}: no unlabeled rows.")
        return 0

    texts = [(row["sample_description"] or "") for row in rows]
    labels, vectorizer, X = choose_k_and_cluster(texts)
    n_clusters = int(labels.max()) + 1
    terms = np.array(vectorizer.get_feature_names_out())

    labeled_count = 0
    print(f"  {type_}: {len(rows)} unlabeled row(s) -> {n_clusters} cluster(s)")
    for cluster_id in range(n_clusters):
        member_idxs = [i for i, lbl in enumerate(labels) if lbl == cluster_id]
        if not member_idxs:
            continue
        cluster_rows = [rows[i] for i in member_idxs]

        centroid = np.asarray(X[member_idxs].mean(axis=0)).ravel()
        top_term_idx = centroid.argsort()[::-1][:8]
        top_terms = list(terms[top_term_idx])
        cluster_samples = [texts[i] for i in member_idxs[:samples_per_cluster]]

        print(f"    cluster {cluster_id} (n={len(cluster_rows)}): top terms = {', '.join(top_terms)}")
        for s in cluster_samples:
            print(f"      - {s}")

        if dry_run:
            print("    [dry-run] skipping Claude call and DB write")
            continue

        priority, reason = classify_cluster_with_claude(client, model, type_, top_terms, cluster_samples)
        apply_cluster_priority(pg_conn, type_, cluster_rows, priority)
        print(f"    -> labeled {priority} ({reason}) for {len(cluster_rows)} row(s)")
        labeled_count += len(cluster_rows)

    return labeled_count


def build_anthropic_client_and_model(model_override: str | None):
    import anthropic

    if ENV_PATH.exists():
        from dotenv import load_dotenv

        load_dotenv(str(ENV_PATH), override=False)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = model_override or os.environ.get("CLAUDE_MODEL", "")
    if not api_key or not model:
        raise EnvironmentError(
            f"Anthropic credentials not found. Set ANTHROPIC_API_KEY and CLAUDE_MODEL in {ENV_PATH} "
            "(or pass --model)."
        )
    return anthropic.Anthropic(api_key=api_key), model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-config", default=str(Path(__file__).resolve().parents[1] / "db_credentials.ini"))
    parser.add_argument("--db-section", default="IRAVATH_DB")
    parser.add_argument("--env", choices=["prod", "production", "staging", "stag"], default=None)
    parser.add_argument("--source-table", default="public.criticalinfo_snowflakes_data")
    parser.add_argument("--start-ts", required=True, help="Inclusive lower bound, e.g. 2026-07-14T01:00:00")
    parser.add_argument("--end-ts", required=True, help="Exclusive upper bound, e.g. 2026-07-15T01:00:00")
    parser.add_argument("--stage", choices=["all", "1", "2", "3"], default="all")
    parser.add_argument("--samples-per-cluster", type=int, default=5)
    parser.add_argument("--model", default=None, help="Override CLAUDE_MODEL from .env")
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude calls and DB writes in stage 3")
    args = parser.parse_args()

    db_section = ENV_TO_DB_SECTION[args.env] if args.env else args.db_section
    db_params = read_db_config(args.db_config, db_section)
    pg_conn = connect_to_db(db_params)
    if not pg_conn:
        print("Failed to connect to Postgres.")
        return 1

    run_stage = lambda s: args.stage in ("all", s)

    try:
        if run_stage("1"):
            print("=== Stage 1: unique_critical_info ===")
            source_rows = fetch_new_source_rows(pg_conn, args.source_table, args.start_ts, args.end_ts)
            inserted = insert_new_unique_critical_info(pg_conn, source_rows)
            pg_conn.commit()
            print(f"  fetched {len(source_rows)} unique pattern(s) from {args.start_ts} -> {args.end_ts}")
            print(f"  inserted {len(inserted)} new unique_critical_info row(s)")
            if not inserted and args.stage == "all":
                print("No new unique_critical_info rows -- stopping (skipping stages 2 and 3).")
                return 0

        if run_stage("2"):
            print("=== Stage 2: unique_cinfo_priority_map ===")
            mapped = sync_unique_cinfo_priority_map(pg_conn)
            pg_conn.commit()
            print(f"  inserted {len(mapped)} new unique_cinfo_priority_map row(s)")

        if run_stage("3"):
            print("=== Stage 3: cluster + label ===")
            client = model = None
            if not args.dry_run:
                client, model = build_anthropic_client_and_model(args.model)
            total_labeled = 0
            for type_ in ("ERROR", "INFO"):
                total_labeled += cluster_and_label_type(
                    pg_conn, client, model, type_, args.samples_per_cluster, args.dry_run
                )
            print(f"  labeled {total_labeled} row(s) total")
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        pg_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
