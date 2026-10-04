"""Create a fresh access key for the Databricks reader user and store it in the
Databricks secret scope, then delete the previous keys.

The secret never touches the screen, a file or the Terraform state: it goes
from the IAM response straight into `databricks secrets put-secret` via stdin.
Also uploads the home zone from config/places.local.json, which must not be
published, as a secret the pipeline reads at runtime.

Requires local AWS credentials allowed to manage the user's keys and an
authenticated Databricks CLI.

    python scripts/rotate_databricks_key.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import boto3

USER = "sp-bus-eta-databricks-reader"
SCOPE = "sp-bus-eta"
PLACES = Path(__file__).resolve().parent.parent / "config" / "places.local.json"


def put_secret(cli, key, value):
    subprocess.run(
        [cli, "secrets", "put-secret", SCOPE, key],
        input=value.encode("utf-8"),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    cli = shutil.which("databricks")
    if not cli:
        print("databricks CLI not found on PATH", file=sys.stderr)
        return 1

    iam = boto3.client("iam")
    old_keys = [k["AccessKeyId"] for k in iam.list_access_keys(UserName=USER)["AccessKeyMetadata"]]
    if len(old_keys) >= 2:
        # IAM allows two keys per user; drop the oldest to make room.
        iam.delete_access_key(UserName=USER, AccessKeyId=old_keys.pop(0))

    new = iam.create_access_key(UserName=USER)["AccessKey"]
    put_secret(cli, "aws_access_key_id", new["AccessKeyId"])
    put_secret(cli, "aws_secret_access_key", new["SecretAccessKey"])

    # Only remove the old keys once the new one is safely stored.
    for key_id in old_keys:
        iam.delete_access_key(UserName=USER, AccessKeyId=key_id)

    home = json.loads(PLACES.read_text(encoding="utf-8"))["home"]
    put_secret(cli, "home_zone", json.dumps(home))

    print(f"Stored key ...{new['AccessKeyId'][-4:]} in scope '{SCOPE}', "
          f"removed {len(old_keys)} old key(s), uploaded home zone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
