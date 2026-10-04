"""Silver: one clean row per vehicle position.

Flattens the nested runs, drops positions outside São Paulo or with stale GPS
timestamps, and removes duplicates: the API repeats a vehicle's last position
until it reports a new one, so the same (vehicle, timestamp) shows up in
consecutive runs. Rebuilt in full on every run, which is cheap at this size and
always consistent with bronze; at scale this would become an incremental MERGE.
"""

import argparse

from pyspark.sql import SparkSession

# Rough bounding box of the city of São Paulo.
LAT_MIN, LAT_MAX = -24.1, -23.3
LON_MIN, LON_MAX = -47.0, -46.3
# A position older than this when the run happened is a stale repeat.
MAX_STALENESS_MINUTES = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bronze", required=True)
    parser.add_argument("--table", required=True)
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    spark.sql(f"""
        CREATE OR REPLACE TABLE {args.table}
        COMMENT 'One row per bus position, deduplicated and cleaned.'
        AS
        WITH flat AS (
            SELECT
                l.line,
                l.direction,
                l.code AS line_code,
                v.p AS vehicle,
                v.a AS accessible,
                to_timestamp(v.ta) AS position_at,
                v.py AS lat,
                v.px AS lon,
                b.run_at,
                b.source_file
            FROM {args.bronze} b
            LATERAL VIEW explode(b.lines) AS l
            LATERAL VIEW explode(l.response.vs) AS v
            WHERE l.error IS NULL
        ),
        valid AS (
            SELECT *
            FROM flat
            WHERE lat BETWEEN {LAT_MIN} AND {LAT_MAX}
              AND lon BETWEEN {LON_MIN} AND {LON_MAX}
              AND position_at IS NOT NULL
              AND position_at <= run_at + INTERVAL 1 MINUTE
              AND position_at >= run_at - INTERVAL {MAX_STALENESS_MINUTES} MINUTES
        )
        SELECT * EXCEPT (rn)
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY line, direction, vehicle, position_at
                ORDER BY run_at
            ) AS rn
            FROM valid
        )
        WHERE rn = 1
    """)

    stats = spark.sql(f"""
        SELECT
            (SELECT count(*) FROM {args.bronze} b LATERAL VIEW explode(b.lines) AS l
               LATERAL VIEW explode(l.response.vs) AS v) AS raw_positions,
            (SELECT count(*) FROM {args.table}) AS silver_positions
    """).first()
    print(f"Raw positions: {stats.raw_positions}, silver positions: {stats.silver_positions}")


# Databricks runs this file inside IPython, where any SystemExit (even 0)
# marks the task as failed, so main() is called directly and errors raise.
if __name__ == "__main__":
    main()
