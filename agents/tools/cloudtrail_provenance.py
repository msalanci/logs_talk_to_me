# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
cloudtrail_provenance.py — Whether a CloudTrail row comes from a file CloudTrail signed.

The org trail writes a signed digest every hour, per account and region, listing the log files it
delivered with the SHA-256 of each. For a row-level CloudTrail query, SQLRewriteHook adds the row's
source file ("$path" AS lttm_source_file, `with_source_file`), and run_athena_query replaces it with
a `provenance` label set here, in code:
  SIGNED      a digest whose signature verifies lists the file, with the file's SHA-256
  MISMATCH    the file, or the digest that lists it, is not what CloudTrail signed
  PENDING     no digest lists the file yet; CloudTrail writes them hourly
  NO DIGEST   no digest lists the file, e.g. the history copied in from the old trail bucket, without digests
  UNVERIFIED  the check could not run (a read failed); it is tried again next time
This is the check `aws cloudtrail validate-logs` makes. As checked on real files, the hash is of the
decompressed log file, and the signature covers the decompressed digest.
"""

import binascii
import gzip
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import boto3
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import utils.sql_text as sql_text

SOURCE_COLUMN = "lttm_source_file"
PENDING_HOURS = 3
DIGESTS_TO_TRY = 3
CACHE_LIMIT = 5000

LOG_FILE = re.compile(r"^(?P<account>\d{12})_CloudTrail_(?P<region>[a-z0-9-]+)_(?P<written>\d{8}T\d{4})Z_\w+\.json\.gz$")
DIGEST_END = re.compile(r"_(?P<end>\d{8}T\d{6})Z\.json\.gz$")
AGGREGATES = re.compile(r"\b(count|sum|avg|min|max|approx_\w+|array_agg|map_agg|count_if|arbitrary|max_by|min_by|"
                        r"bool_and|bool_or|every|stddev\w*|variance|var_pop|var_samp|histogram|checksum|listagg)\s*\(")

_labels: dict[str, str] = {}
_digests: dict[str, dict] = {}
_listings: dict[str, list[str]] = {}
_public_keys: dict[tuple[str, str], object] = {}


def with_source_file(sql: str) -> str:
    """Add the row's source file to a row-level CloudTrail query; any other SQL is returned unchanged.

    Row-level: each result row is one event from one file. That is a SELECT on lttm_logs.cloudtrail_logs,
    or the prompt's deduplication over one derived table (`SELECT … FROM (SELECT …, row_number() OVER
    (PARTITION BY eventid …) AS rn FROM lttm_logs.cloudtrail_logs …) AS deduped WHERE rn = 1`), with no
    JOIN, UNION, GROUP BY, DISTINCT or aggregate. In the derived-table form the source file is added
    inside and passed through the outer SELECT. The inner one is added first: it comes after the outer
    SELECT's own FROM, so that FROM's position still holds for the second insert.

    Args:
        sql: The SQL the model wrote, before it runs.

    Returns:
        The SQL with "$path" AS lttm_source_file added; the SQL unchanged when it is not a row-level
        CloudTrail query, or already has the column.
    """
    masked = sql_text.mask(sql).lower()
    if "lttm_logs.cloudtrail_logs" not in masked or SOURCE_COLUMN in masked:
        return sql
    if re.search(r"\b(join|union|intersect|except|group\s+by|distinct)\b", masked) or AGGREGATES.search(masked):
        return sql
    selects = [m.start() for m in re.finditer(r"\bselect\b", masked)]
    if not selects or len(selects) > 2 or not re.match(r"\s*select\b", masked):
        return sql
    froms = [_own_from(masked, at) for at in selects]
    if None in froms:
        return sql
    if len(selects) == 2 and not re.fullmatch(r"from\s*\(\s*", masked[froms[0]:selects[1]]):
        return sql
    if not re.match(r"from\s+lttm_logs\.cloudtrail_logs\b", masked[froms[-1]:]):
        return sql
    inner = froms[-1]
    sql = f'{sql[:inner].rstrip()}, "$path" AS {SOURCE_COLUMN} {sql[inner:]}'
    if len(selects) == 2:
        sql = f"{sql[:froms[0]].rstrip()}, {SOURCE_COLUMN} {sql[froms[0]:]}"
    return sql


def _own_from(masked: str, select_at: int) -> int | None:
    """Find where the FROM of a SELECT starts: the first FROM after it at the same parenthesis depth.

    Args:
        masked: The SQL in lower case, with string literals and comments masked (sql_text.mask).
        select_at: The position of the SELECT keyword in `masked`.

    Returns:
        The position of that SELECT's own FROM; None when it has none.
    """
    depth = masked[:select_at].count("(") - masked[:select_at].count(")")
    for match in re.finditer(r"\bfrom\b", masked[select_at:]):
        at = select_at + match.start()
        if masked[:at].count("(") - masked[:at].count(")") == depth:
            return at
    return None


def label_rows(rows: list[dict]) -> list[dict]:
    """Replace each row's source file with a provenance label.

    Args:
        rows: Rows from run_athena_query, with the source file in SOURCE_COLUMN.

    Returns:
        The same rows, in order, without SOURCE_COLUMN and with a "provenance" value: SIGNED, MISMATCH,
        PENDING, NO DIGEST or UNVERIFIED (also for a row without a source file).
    """
    labels = {path: _label(path) for path in {row.get(SOURCE_COLUMN) for row in rows} if path}
    return [{**{k: v for k, v in row.items() if k != SOURCE_COLUMN},
             "provenance": labels.get(row.get(SOURCE_COLUMN), "UNVERIFIED")} for row in rows]


def _label(path: str) -> str:
    """Give one log file's provenance label, cached per process.

    PENDING and UNVERIFIED are not cached: a pending file gets its digest later, and a failed read is not a
    verdict.

    Args:
        path: The log file's S3 URL, as Athena's "$path" gives it.

    Returns:
        SIGNED, MISMATCH, PENDING or NO DIGEST from the check; UNVERIFIED when the check failed (logged).
    """
    if path in _labels:
        return _labels[path]
    try:
        label = _check(path)
    except Exception as e:
        print(f"[LTTM:Provenance] UNVERIFIED {path[-80:]}: {type(e).__name__}: {e}", flush=True)
        return "UNVERIFIED"
    if label != "PENDING":
        if len(_labels) >= CACHE_LIMIT:
            _labels.clear()
        _labels[path] = label
    return label


def _check(path: str) -> str:
    """Check one log file against the digests that can list it.

    A file younger than PENDING_HOURS that no digest lists yet may still get one.

    Args:
        path: The log file's S3 URL. Its key holds the account's prefix (cloudtrail/AWSLogs/<org>/<account>)
            before "/CloudTrail/".

    Returns:
        SIGNED, MISMATCH, PENDING or NO DIGEST; NO DIGEST also for a file not named as CloudTrail names them.

    Raises:
        Exception: Any error of the S3 or CloudTrail reads; _label catches it as UNVERIFIED.
    """
    bucket, key = path.removeprefix("s3://").split("/", 1)
    name = LOG_FILE.match(key.rsplit("/", 1)[-1])
    if not name or "/CloudTrail/" not in key:
        return "NO DIGEST"
    written = datetime.strptime(name["written"], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
    account_prefix = key.split("/CloudTrail/", 1)[0]
    for digest_key in _digests_after(bucket, account_prefix, name["region"], written):
        digest = _digest(bucket, digest_key, name["region"])
        if key in digest["files"]:
            if not digest["signed"]:
                return "MISMATCH"
            return "SIGNED" if _sha256(bucket, key) == digest["files"][key] else "MISMATCH"
    if datetime.now(timezone.utc) - written < timedelta(hours=PENDING_HOURS):
        return "PENDING"
    return "NO DIGEST"


def _digests_after(bucket: str, account_prefix: str, region: str, written: datetime) -> list[str]:
    """Find the first digests written after a log file, which are the ones that can list it.

    The digest listings of the file's day, and of the day PENDING_HOURS later, are read from S3. Only a
    finished day's listing is cached: a day still gets a digest every hour.

    Args:
        bucket: The lake bucket.
        account_prefix: The key prefix of the file's account, cloudtrail/AWSLogs/<org>/<account>.
        region: The file's region.
        written: When the file was written (UTC), from its name.

    Returns:
        The keys of the first DIGESTS_TO_TRY digests that end after `written`, oldest first; an empty list when
        there are none.
    """
    keys = []
    for day in sorted({written.date(), (written + timedelta(hours=PENDING_HOURS)).date()}):
        prefix = f"{account_prefix}/CloudTrail-Digest/{region}/{day:%Y/%m/%d}/"
        listing = _listings.get(prefix)
        if listing is None:
            s3 = boto3.client("s3")
            listing = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
                       for o in page.get("Contents", [])]
            finished = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1, hours=PENDING_HOURS)
            if finished < datetime.now(timezone.utc):
                if len(_listings) >= CACHE_LIMIT:
                    _listings.clear()
                _listings[prefix] = listing
        keys += listing
    after = []
    for key in keys:
        end = DIGEST_END.search(key)
        if end and datetime.strptime(end["end"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc) > written:
            after.append((end["end"], key))
    return [key for _, key in sorted(after)[:DIGESTS_TO_TRY]]


def _digest(bucket: str, key: str, region: str) -> dict:
    """Read a digest file, cached per process.

    Args:
        bucket: The lake bucket.
        key: The digest's S3 key.
        region: The region whose CloudTrail public keys sign it.

    Returns:
        {"files": {log file key: its SHA-256}, "signed": True when the digest's signature verifies}.
    """
    if key not in _digests:
        obj = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        body = _decompressed(obj["Body"].read())
        digest = json.loads(body)
        files = {f["s3Object"]: f["hashValue"] for f in digest.get("logFiles", [])}
        if len(_digests) >= CACHE_LIMIT:
            _digests.clear()
        _digests[key] = {"files": files, "signed": _signature_valid(digest, body, obj.get("Metadata", {}), region)}
    return _digests[key]


def _signature_valid(digest: dict, body: bytes, metadata: dict, region: str) -> bool:
    """Tell whether CloudTrail's signature on a digest verifies with CloudTrail's public key.

    Args:
        digest: The digest's parsed JSON.
        body: The digest's decompressed bytes, as signed.
        metadata: The digest object's S3 metadata, which holds the `signature`.
        region: The digest's region.

    Returns:
        True when the signature verifies; False when it does not, or the digest has no signature.

    Raises:
        LookupError: CloudTrail lists no public key with the digest's fingerprint (_public_key).
    """
    signature = metadata.get("signature")
    if not signature:
        return False
    key = _public_key(region, digest["digestPublicKeyFingerprint"], digest["digestEndTime"])
    signed = (f"{digest['digestEndTime']}\n{digest['digestS3Bucket']}/{digest['digestS3Object']}\n"
              f"{hashlib.sha256(body).hexdigest()}\n{digest.get('previousDigestSignature') or 'null'}").encode()
    try:
        key.verify(binascii.unhexlify(signature), signed, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def _public_key(region: str, fingerprint: str, end_time: str):
    """Find CloudTrail's public key with a fingerprint, valid around a digest's end time; cached per process.

    Args:
        region: The digest's region.
        fingerprint: The digest's digestPublicKeyFingerprint.
        end_time: The digest's digestEndTime, ISO 8601 in UTC.

    Returns:
        The public key, loaded from its DER form.

    Raises:
        LookupError: No key with that fingerprint is listed a day either side of `end_time`.
    """
    if (region, fingerprint) not in _public_keys:
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        listed = boto3.client("cloudtrail", region_name=region).list_public_keys(
            StartTime=end - timedelta(days=1), EndTime=end + timedelta(days=1))["PublicKeyList"]
        match = next((k for k in listed if k["Fingerprint"] == fingerprint), None)
        if match is None:
            raise LookupError(f"no CloudTrail public key {fingerprint} in {region}")
        _public_keys[(region, fingerprint)] = serialization.load_der_public_key(match["Value"])
    return _public_keys[(region, fingerprint)]


def _sha256(bucket: str, key: str) -> str:
    """Compute the SHA-256 of a log file, decompressed, as CloudTrail's digests hash it.

    Args:
        bucket: The lake bucket.
        key: The log file's S3 key.

    Returns:
        The hash as lower-case hex.
    """
    return hashlib.sha256(_decompressed(boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())).hexdigest()


def _decompressed(data: bytes) -> bytes:
    """Decompress gzip data; anything else is returned as it is.

    Args:
        data: An S3 object's body.

    Returns:
        The decompressed bytes when `data` starts with the gzip magic number; `data` itself otherwise.
    """
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
