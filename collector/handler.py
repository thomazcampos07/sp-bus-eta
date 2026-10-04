"""Polls SPTrans Olho Vivo for the live positions of a few bus lines and lands
the raw responses in S3, one gzipped JSON object per run.

Runs on AWS Lambda every minute (EventBridge Scheduler). Nothing is parsed or
filtered here: the raw payload is the contract, and all cleaning happens
downstream in Databricks, so a bug there can always be fixed by reprocessing.
"""

import gzip
import http.cookiejar
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BASE_URL = "https://api.olhovivo.sptrans.com.br/v2.1"
# The SPTrans firewall answers 403 to Python's default User-Agent.
USER_AGENT = "sp-bus-eta/0.1 (+https://github.com/thomazcampos07/sp-bus-eta)"
HTTP_TIMEOUT = 10
# Internal line codes can change when SPTrans updates its registry, so they are
# looked up at runtime and refreshed every few hours instead of hardcoded.
CODES_TTL_SECONDS = 6 * 3600

BUCKET = os.environ["BUCKET"]
TOKEN_PARAMETER = os.environ["TOKEN_PARAMETER"]
RAW_PREFIX = os.environ.get("RAW_PREFIX", "raw/positions")
HC_PING_URL = os.environ.get("HC_PING_URL", "")
LINES = json.loads((Path(__file__).parent / "lines.json").read_text())["lines"]

_aws_config = Config(retries={"total_max_attempts": 3, "mode": "standard"})
s3 = boto3.client("s3", config=_aws_config)
ssm = boto3.client("ssm", config=_aws_config)

# Reused across warm invocations.
_token = None
_line_codes = None
_line_codes_at = 0.0


class OlhoVivo:
    """Minimal client: authentication sets a session cookie used by later calls."""

    def __init__(self, token):
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        if self._request("POST", "/Login/Autenticar", token=token) is not True:
            raise RuntimeError("Olho Vivo authentication failed")

    def _request(self, method, path, **params):
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            method=method,
            data=b"" if method == "POST" else None,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with self._opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read() or b"null")

    def find_lines(self, term):
        return self._request("GET", "/Linha/Buscar", termosBusca=term) or []

    def positions(self, line_code):
        return self._request("GET", "/Posicao/Linha", codigoLinha=line_code)


def get_token():
    global _token
    if _token is None:
        resp = ssm.get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)
        _token = resp["Parameter"]["Value"]
    return _token


def resolve_line_codes(api):
    """Map each configured (line, direction) to its current internal code."""
    global _line_codes, _line_codes_at
    if _line_codes is not None and time.time() - _line_codes_at < CODES_TTL_SECONDS:
        return _line_codes

    codes = []
    for name in sorted({entry["line"] for entry in LINES}):
        number, suffix = name.rsplit("-", 1)
        for found in api.find_lines(name):
            if found["lt"] == number and str(found["tl"]) == suffix:
                codes.append({
                    "line": name,
                    "direction": found["sl"],
                    "code": found["cl"],
                    "sign_main": found["tp"],
                    "sign_secondary": found["ts"],
                })
    wanted = {(entry["line"], entry["direction"]) for entry in LINES}
    codes = [c for c in codes if (c["line"], c["direction"]) in wanted]
    missing = wanted - {(c["line"], c["direction"]) for c in codes}
    if missing:
        # Not fatal: collect what exists and leave a trace for the data checks.
        logger.warning("Lines not found in Olho Vivo: %s", sorted(missing))

    _line_codes, _line_codes_at = codes, time.time()
    return codes


def collect(api, codes):
    results = []
    for line in codes:
        requested_at = datetime.now(timezone.utc)
        try:
            payload = api.positions(line["code"])
            error = None
        except Exception as exc:  # one failing line must not lose the others
            payload, error = None, f"{type(exc).__name__}: {exc}"
            logger.warning("Positions failed for %s: %s", line, error)
        results.append({
            **line,
            "requested_at": requested_at.isoformat(),
            "error": error,
            "response": payload,
        })
    return results


def object_key(run_at):
    # Partitioned by UTC date and hour; the run timestamp makes the key unique,
    # so a retried run never overwrites an earlier one.
    return (
        f"{RAW_PREFIX}/dt={run_at:%Y-%m-%d}/hour={run_at:%H}/"
        f"positions_{run_at:%Y%m%dT%H%M%S}Z.json.gz"
    )


def ping_healthcheck(suffix=""):
    if not HC_PING_URL:
        return
    try:
        urllib.request.urlopen(HC_PING_URL + suffix, timeout=5).close()
    except Exception as exc:  # monitoring must never break collection
        logger.warning("Healthcheck ping failed: %s", exc)


def lambda_handler(event, context):
    run_at = datetime.now(timezone.utc)
    api = OlhoVivo(get_token())
    codes = resolve_line_codes(api)
    results = collect(api, codes)

    body = json.dumps(
        {"run_at": run_at.isoformat(), "source": BASE_URL, "lines": results},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    key = object_key(run_at)
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=gzip.compress(body),
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    vehicles = sum(len((r["response"] or {}).get("vs") or []) for r in results)
    failed = sum(1 for r in results if r["error"])
    logger.info("Wrote s3://%s/%s: %d lines, %d vehicles, %d failed",
                BUCKET, key, len(results), vehicles, failed)
    # A run where every line failed is a failure for monitoring purposes.
    ping_healthcheck("/fail" if results and failed == len(results) else "")
    return {"key": key, "lines": len(results), "vehicles": vehicles, "failed": failed}
