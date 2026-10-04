"""Gold: observed trips between the two zones, and ride-time statistics.

A trip is the same bus seen in one zone and later in the other. Positions are
first collapsed into zone visits (gaps and islands), then each visit is paired
with the next one of the same bus: home -> park or park -> home. The ride time
runs from the last sighting in the origin zone to the first in the destination.

The home zone is personal, so it comes from a secret, not from this public code.
"""

import argparse
import json

from databricks.sdk.runtime import dbutils
from pyspark.sql import SparkSession

# Plausibility bounds for one ride between the two zones.
MIN_RIDE_MINUTES = 5
MAX_RIDE_MINUTES = 120
# Two sightings in the same zone further apart than this are separate visits.
VISIT_GAP_MINUTES = 30


def distance_m(lat, lon, lat0, lon0):
    """Haversine distance in meters, as a SQL expression."""
    return (
        f"2 * 6371000 * asin(sqrt("
        f"pow(sin(radians({lat} - {lat0}) / 2), 2) + "
        f"cos(radians({lat0})) * cos(radians({lat})) * "
        f"pow(sin(radians({lon} - {lon0}) / 2), 2)))"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--silver", required=True)
    parser.add_argument("--trips", required=True)
    parser.add_argument("--stats", required=True)
    parser.add_argument("--park-lat", type=float, required=True)
    parser.add_argument("--park-lon", type=float, required=True)
    parser.add_argument("--park-radius-m", type=float, required=True)
    parser.add_argument("--secret-scope", required=True)
    args = parser.parse_args()

    home = json.loads(dbutils.secrets.get(args.secret_scope, "home_zone"))
    spark = SparkSession.builder.getOrCreate()

    home_dist = distance_m("lat", "lon", home["lat"], home["lon"])
    park_dist = distance_m("lat", "lon", args.park_lat, args.park_lon)

    spark.sql(f"""
        CREATE OR REPLACE TABLE {args.trips}
        COMMENT 'One row per observed ride between the home and park zones.'
        AS
        WITH zoned AS (
            SELECT line, direction, vehicle, position_at,
                CASE
                    WHEN {home_dist} <= {home["radius_m"]} THEN 'home'
                    WHEN {park_dist} <= {args.park_radius_m} THEN 'park'
                END AS zone
            FROM {args.silver}
        ),
        in_zone AS (
            SELECT *,
                lag(zone) OVER w AS prev_zone,
                lag(position_at) OVER w AS prev_at
            FROM zoned
            WHERE zone IS NOT NULL
            WINDOW w AS (PARTITION BY line, direction, vehicle ORDER BY position_at)
        ),
        numbered AS (
            SELECT *,
                sum(CASE
                    WHEN prev_zone IS NULL OR prev_zone <> zone
                      OR position_at > prev_at + INTERVAL {VISIT_GAP_MINUTES} MINUTES
                    THEN 1 ELSE 0 END
                ) OVER (PARTITION BY line, direction, vehicle ORDER BY position_at) AS visit_id
            FROM in_zone
        ),
        visits AS (
            SELECT line, direction, vehicle, visit_id, zone,
                min(position_at) AS entered_at,
                max(position_at) AS left_at
            FROM numbered
            GROUP BY line, direction, vehicle, visit_id, zone
        ),
        paired AS (
            SELECT *,
                lead(zone) OVER w AS next_zone,
                lead(entered_at) OVER w AS next_entered_at
            FROM visits
            WINDOW w AS (PARTITION BY line, direction, vehicle ORDER BY entered_at)
        )
        SELECT
            CASE zone WHEN 'home' THEN 'home_to_park' ELSE 'park_to_home' END AS trip,
            line,
            direction,
            vehicle,
            left_at AS departed_at,
            next_entered_at AS arrived_at,
            round((unix_timestamp(next_entered_at) - unix_timestamp(left_at)) / 60.0, 1) AS ride_minutes,
            from_utc_timestamp(left_at, 'America/Sao_Paulo') AS departed_local
        FROM paired
        WHERE next_zone IS NOT NULL
          AND next_zone <> zone
          AND (unix_timestamp(next_entered_at) - unix_timestamp(left_at)) / 60.0
              BETWEEN {MIN_RIDE_MINUTES} AND {MAX_RIDE_MINUTES}
    """)

    spark.sql(f"""
        CREATE OR REPLACE TABLE {args.stats}
        COMMENT 'Typical (p50) and bad-day (p90) ride times by trip, day type and hour.'
        AS
        SELECT
            trip,
            CASE WHEN dayofweek(departed_local) IN (1, 7) THEN 'weekend' ELSE 'weekday' END AS day_type,
            hour(departed_local) AS hour_local,
            count(*) AS trips,
            percentile_approx(ride_minutes, 0.5) AS p50_minutes,
            percentile_approx(ride_minutes, 0.9) AS p90_minutes
        FROM {args.trips}
        GROUP BY ALL
    """)

    print(spark.sql(f"SELECT trip, count(*) AS trips, percentile_approx(ride_minutes, 0.5) AS p50 "
                    f"FROM {args.trips} GROUP BY trip").collect())


# Databricks runs this file inside IPython, where any SystemExit (even 0)
# marks the task as failed, so main() is called directly and errors raise.
if __name__ == "__main__":
    main()
