"""Copy new raw files from S3 into a Unity Catalog volume.

Databricks Free Edition has no external locations, so Auto Loader cannot read
s3:// directly. This task mirrors the raw prefix into a managed volume with a
read-only access key; Auto Loader then picks the files up from there. It is
idempotent: files already in the volume are skipped, so reruns are free.
"""

import argparse
import os

import boto3
from databricks.sdk.runtime import dbutils


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--landing", required=True, help="/Volumes/... directory")
    parser.add_argument("--secret-scope", required=True)
    args = parser.parse_args()

    s3 = boto3.client(
        "s3",
        region_name="sa-east-1",
        aws_access_key_id=dbutils.secrets.get(args.secret_scope, "aws_access_key_id"),
        aws_secret_access_key=dbutils.secrets.get(args.secret_scope, "aws_secret_access_key"),
    )

    prefix = args.prefix.rstrip("/") + "/"
    existing = set()
    for root, _dirs, files in os.walk(args.landing):
        for name in files:
            existing.add(os.path.relpath(os.path.join(root, name), args.landing).replace(os.sep, "/"))

    copied = 0
    seen = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=args.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            seen += 1
            relative = obj["Key"][len(prefix):]
            if relative in existing:
                continue
            target = os.path.join(args.landing, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with s3.get_object(Bucket=args.bucket, Key=obj["Key"])["Body"] as body:
                data = body.read()
            # Write to a temp name and rename, so Auto Loader never sees a
            # half-written file.
            tmp = target + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
            copied += 1

    print(f"S3 objects: {seen}, already in volume: {len(existing)}, copied: {copied}")


# Databricks runs this file inside IPython, where any SystemExit (even 0)
# marks the task as failed, so main() is called directly and errors raise.
if __name__ == "__main__":
    main()
