"""Bronze: load raw collector files into a Delta table with Auto Loader.

One row per collector run, kept nested exactly as the API answered, plus the
source file and ingestion time. Runs as an incremental batch
(trigger availableNow): each run picks up only the files it has not seen.
"""

import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

# Explicit schema: inference would type the always-null fields as strings one
# day and something else the next, and silently drift.
RAW_SCHEMA = """
    run_at STRING,
    source STRING,
    lines ARRAY<STRUCT<
        line: STRING,
        direction: INT,
        code: INT,
        sign_main: STRING,
        sign_secondary: STRING,
        requested_at: STRING,
        error: STRING,
        response: STRUCT<
            hr: STRING,
            vs: ARRAY<STRUCT<p: STRING, a: BOOLEAN, ta: STRING, py: DOUBLE, px: DOUBLE>>
        >
    >>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--landing", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--table", required=True)
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()

    stream = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("pathGlobFilter", "*.json.gz")
        .option("recursiveFileLookup", "true")
        .schema(RAW_SCHEMA)
        .load(args.landing)
        .select(
            F.to_timestamp("run_at").alias("run_at"),
            "source",
            "lines",
            F.col("_metadata.file_path").alias("source_file"),
            F.current_timestamp().alias("ingested_at"),
        )
    )

    query = (
        stream.writeStream.option("checkpointLocation", args.checkpoint)
        .trigger(availableNow=True)
        .toTable(args.table)
    )
    query.awaitTermination()
    print(f"Bronze rows: {spark.table(args.table).count()}")


# Databricks runs this file inside IPython, where any SystemExit (even 0)
# marks the task as failed, so main() is called directly and errors raise.
if __name__ == "__main__":
    main()
