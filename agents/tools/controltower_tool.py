# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
controltower_tool.py — Strands @tool for reading AWS Control Tower.

Registered on the controltower_agent as @tool query_controltower_api.

Calls the Control Tower API in its home region `CONTROLTOWER_REGION` (agents/utils/agent_vars.py):
ListLandingZones, GetLandingZone, ListEnabledControls, GetEnabledControl, ListEnabledBaselines, ListBaselines,
ListLandingZoneOperations, GetLandingZoneOperation, ListControlOperations, GetControlOperation and
GetBaselineOperation. Control names come from the Control Catalog (GetControl), OU names from AWS Organizations.
No Athena/Glue/S3 infrastructure needed — pure API-based tool. What Control Tower did in the past, and who started
it, comes from CloudTrail in the lake (controltower_events_tool); the API shows drift only as it is now.

Control Tower is a management-account service: every call runs with the execution role's own credentials, and one
call covers the whole landing zone, every OU and account. No role is assumed.

The landing zone is found with ListLandingZones on every call, never by a stored ID: a landing zone that is
decommissioned and created again has a new ID. OU names are read on every call too; only the Control
Catalog's control names, which do not change, are kept for the process.

The counts are computed here over every item, not only the listed ones, so the answer quotes them instead of
counting. botocore checks neither enum values nor list maximums, so every filter is mapped to the
API's single upper-case value before any call, and every page size is within the list call's range. Each action
reads only its own filters (USED); a filter given to another action is named in its result as not used. Empty
results are worded so that OutputIntegrityHook's EMPTY_PART_PATTERNS reads them as a part that rightly has nothing.
"""

import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import NamedTuple

import boto3
from botocore.exceptions import ClientError
from strands import tool

import utils.agent_vars as _vars
from utils.finding_counts import count_line
from utils.sse_emitter import emit_status
from utils.untrusted_text import one_line, one_line_args
from tools.access_analyzer_tool import _get_organization_id

ACTIONS = ("overview", "landing_zone", "enabled_controls", "control_details", "enabled_baselines", "operations",
           "operation")
USED = {
    "overview": (),
    "landing_zone": (),
    "enabled_controls": ("target", "status", "drift_status", "behavior", "control"),
    "control_details": ("target", "control"),
    "enabled_baselines": ("target", "status", "drift_status"),
    "operations": ("target", "status", "start_date", "end_date"),
    "operation": ("operation_id",),
}
ENABLEMENT_STATUSES = ("succeeded", "failed", "under_change")
OPERATION_STATUSES = ("succeeded", "failed", "in_progress")
CONTROL_DRIFT = ("drifted", "in_sync", "not_checking", "unknown")
BASELINE_DRIFT = ("drifted", "in_sync")
BEHAVIORS = ("preventive", "proactive", "detective")

BEHAVIOR_ORDER = ["preventive", "proactive", "detective", "unknown"]
STATUS_ORDER = ["succeeded", "failed", "under_change", "in_progress", "not reported"]
DRIFT_ORDER = ["in_sync", "drifted", "not_checking", "unknown", "not reported"]

MAX_RESULTS = 200
PAGE_SIZE = 100
WHOLE = "the whole landing zone (every OU and account)"

_OPERATION_ID = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
_OU_ID = re.compile(r"^ou-[0-9a-z]{4,32}-[a-z0-9]{8,32}$")
_ACCOUNT_ID = re.compile(r"^\d{12}$")
_ORG_ARN = re.compile(r"^arn:aws[a-z-]*:organizations::\d{12}:(account|ou)/(o-[a-z0-9]{10,32})/"
                      r"(\d{12}|ou-[0-9a-z]{4,32}-[a-z0-9]{8,32})$")
_ORGANIZATIONS_CALLS = ("DescribeOrganizationalUnit", "ListRoots", "ListOrganizationalUnitsForParent")

IMPLEMENTATIONS = {
    "AWS::Organizations::Policy::SERVICE_CONTROL_POLICY": "SCP",
    "AWS::Organizations::Policy::RESOURCE_CONTROL_POLICY": "RCP",
    "AWS::CloudFormation::Type::HOOK": "CloudFormation hook",
    "AWS::Config::ConfigRule": "AWS Config rule",
    "AWS::SecurityHub::SecurityControl": "Security Hub control",
}

INTEGRATIONS = (
    ("centralizedLogging", "centralized logging (CloudTrail)", False),
    ("config", "AWS Config", True),
    ("securityRoles", "security roles", True),
    ("accessManagement", "IAM Identity Center", False),
    ("backup", "AWS Backup", False),
)
DETECTIVE = {
    "off": "Detective controls: not available (they need the AWS Config integration, which is not enabled in "
           "this landing zone)",
    "on": "Detective controls: available (the AWS Config integration is on)",
}
NO_CONFIG_BASELINES = " OU baselines need the AWS Config integration, which is off."


class _NoLandingZone(Exception):
    """Control Tower has no landing zone in its home region: a message for the user, not an error."""


class _Control(NamedTuple):
    """A control as the Control Catalog names it; `named` is False when the catalog could not name it.

    `behavior` is preventive, proactive, detective, or unknown.
    """

    alias: str
    name: str
    behavior: str
    severity: str
    implementation: str
    named: bool


class _Target(NamedTuple):
    """An account or OU that a question names, with the ARN Control Tower knows it by.

    `kind` is "account" or "ou"; `ident` is the account ID or the OU ID.
    """

    kind: str
    ident: str
    arn: str
    label: str


def _code(error: ClientError) -> str:
    """Read the error code of a ClientError.

    Args:
        error: A botocore ClientError.

    Returns:
        Its error code, such as "AccessDeniedException"; "" when the response has none.
    """
    return error.response.get("Error", {}).get("Code", "")


def _account_label(account_id: str) -> str:
    """Label an account ID: "222222222222 (dev)" for an LTTM account, the ID alone for another.

    Args:
        account_id: A 12-digit account ID.

    Returns:
        The label.
    """
    return f"{account_id} ({_vars.ACCOUNTS[account_id]['label']})" if account_id in _vars.ACCOUNTS else account_id


def _breakdown(groups: list[str], order: list[str] = ()) -> str:
    """Count how many times each group occurs, as "a 2, b 1".

    The groups in `order` come first, the rest as first seen. The groups keep their case (count_line lowercases
    them, which would change an OU's name).

    Args:
        groups: One group name per item counted.
        order: Group names in the order to list them; a name with no item is left out.

    Returns:
        The counts, separated by commas; "" when `groups` is empty.
    """
    counts = Counter(groups)
    names = [g for g in order if g in counts] + [g for g in dict.fromkeys(groups) if g not in order]
    return ", ".join(f"{name} {counts[name]}" for name in names)


def _implementation(kind: str) -> str:
    """Say how a control works, from the Control Catalog's implementation type.

    Args:
        kind: The implementation type, such as "AWS::Organizations::Policy::SERVICE_CONTROL_POLICY".

    Returns:
        "SCP", "RCP", "CloudFormation hook", "AWS Config rule", "Security Hub control" or "declarative policy"; the
        type itself, on one line, for any other.
    """
    if kind.startswith("AWS::Organizations::Policy::DECLARATIVE_POLICY"):
        return "declarative policy"
    return IMPLEMENTATIONS.get(kind, one_line(kind))


@lru_cache(maxsize=1024)
def _catalog_control(control_arn: str) -> _Control:
    """The Control Catalog's alias, name, behavior, severity and implementation of a control, kept for the process.

    Control metadata does not change. A failure raises, and lru_cache keeps no failure: the next call asks again.

    Args:
        control_arn: The control's ARN, as an enabled control's controlIdentifier gives it.

    Returns:
        The control as a _Control, with `named` True.

    Raises:
        Exception: Any error of GetControl; _control catches it.
    """
    control = boto3.client("controlcatalog", region_name=_vars.CONTROLTOWER_REGION).get_control(ControlArn=control_arn)
    aliases = control.get("Aliases") or [control_arn.rsplit("/", 1)[-1]]
    return _Control(one_line(aliases[0]), one_line(control.get("Name", "")),
                    str(control.get("Behavior") or "unknown").lower(), str(control.get("Severity") or "").lower(),
                    _implementation((control.get("Implementation") or {}).get("Type", "")), True)


def _control(control_arn: str) -> _Control:
    """A control's catalog names; if the catalog fails, its raw identifier, with the behavior counted as "unknown".

    Args:
        control_arn: The control's ARN; "" for none.

    Returns:
        The _Control from the catalog; when the catalog fails (logged) or there is no ARN, one named by the ARN's
        last part ("unknown control" for none), with `named` False.
    """
    if control_arn:
        try:
            return _catalog_control(control_arn)
        except Exception as e:
            print(f"[LTTM:ControlTower] The Control Catalog could not name {control_arn}: {type(e).__name__}: {e}",
                  flush=True)
    return _Control(one_line(control_arn.rsplit("/", 1)[-1] or "unknown control"), "", "unknown", "", "", False)


def _control_label(control_arn: str) -> str:
    """Label a control as "AWS-GR_RESTRICT_ROOT_USER — Disallow actions as a root user".

    Args:
        control_arn: The control's ARN.

    Returns:
        The alias and the name; the alias alone when the catalog gives no name; the raw identifier with a note
        when the catalog could not name the control.
    """
    control = _control(control_arn)
    if not control.named:
        return f"{control.alias} ({one_line(control_arn)}; the Control Catalog could not name it)"
    return f"{control.alias} — {control.name}" if control.name else control.alias


class _Names:
    """Labels for the accounts and OUs that Control Tower names, for one tool call.

    OU names come from AWS Organizations and are read once per call, never kept longer: an OU can be renamed, and a
    landing zone created again governs other OUs. A target of another organization is marked as such, the way
    Access Analyzer marks one: a landing zone can point at organizations that no longer exist.
    """

    def __init__(self):
        """Start with no OU read; the Organizations client is made on first use.

        Args:
            None.

        Returns:
            None. `_ous` maps an OU ID to its (name, ARN), ("", "") when it cannot be read.
        """
        self._ous: dict[str, tuple[str, str]] = {}
        self._client = None

    def org_id(self) -> str:
        """Give this organization's ID, read once per process, as Access Analyzer reads it.

        Args:
            None.

        Returns:
            The organization ID; "" when it cannot be read.
        """
        return _get_organization_id()

    def organizations(self):
        """Give the Organizations client of this call, made on first use.

        Args:
            None.

        Returns:
            A boto3 organizations client in GLOBAL_SERVICE_REGION.
        """
        if self._client is None:
            self._client = boto3.client("organizations", region_name=_vars.GLOBAL_SERVICE_REGION)
        return self._client

    def remember(self, ou_id: str, name: str, arn: str) -> None:
        """Keep an OU's name and ARN, read elsewhere in this call, so it is not asked for again.

        Args:
            ou_id: The OU's ID.
            name: The OU's name.
            arn: The OU's ARN.

        Returns:
            None.
        """
        self._ous[ou_id] = (one_line(name), arn)

    def ou(self, ou_id: str) -> tuple[str, str]:
        """Give an OU's (name, ARN) from DescribeOrganizationalUnit, read once per call.

        Args:
            ou_id: The OU's ID, such as "ou-ab12-cd34ef56".

        Returns:
            The OU's name, on one line, and its ARN; ("", "") when it cannot be read (logged, and not asked
            again in this call).
        """
        if ou_id not in self._ous:
            try:
                unit = self.organizations().describe_organizational_unit(
                    OrganizationalUnitId=ou_id)["OrganizationalUnit"]
                self.remember(ou_id, unit.get("Name", ""), unit.get("Arn", ""))
            except Exception as e:
                print(f"[LTTM:ControlTower] Could not read OU {ou_id}: {type(e).__name__}: {e}", flush=True)
                self._ous[ou_id] = ("", "")
        return self._ous[ou_id]

    def label(self, identifier: str) -> str:
        """Label an account or OU, given as an ARN or an ID: "222222222222 (dev)", "OU Workloads (ou-…)".

        An account or OU ARN is arn:aws:organizations::<management account>:account|ou/<organization>/<ID>. One
        of another organization is labelled as not this organization.

        Args:
            identifier: An account or OU ID or ARN; any other text is returned on one line.

        Returns:
            The label; "N/A" when `identifier` is empty.
        """
        identifier = one_line(identifier or "")
        match = _ORG_ARN.match(identifier)
        if match:
            kind, org, ident = match.groups()
            this = self.org_id()
            if this and org != this:
                own = _account_label(ident) if kind == "account" else f"OU {ident}"
                return f"{own} (not this organization, which is {this})"
            identifier = ident
        if _ACCOUNT_ID.match(identifier):
            return _account_label(identifier)
        if _OU_ID.match(identifier):
            name = self.ou(identifier)[0]
            return f"OU {name} ({identifier})" if name else f"OU {identifier}"
        return identifier or "N/A"


def _all(client, operation: str, key: str, page_size: int | None = PAGE_SIZE, **params) -> list[dict]:
    """Read every item of a list API, all pages followed.

    PAGE_SIZE is within every Control Tower list call's maxResults range (ListEnabledBaselines 5-100,
    ListBaselines 4-100, the others 1-100 or 1-200).

    Args:
        client: A boto3 client with a paginator for `operation`.
        operation: The paginator's name, such as "list_enabled_controls".
        key: The key of the items in each page, such as "enabledControls".
        page_size: The page size to ask for; None for the API's own page size.
        **params: The list call's own parameters.

    Returns:
        The items of every page, in order; an empty list when there are none.
    """
    config = {"PaginationConfig": {"PageSize": page_size}} if page_size else {}
    return [item for page in client.get_paginator(operation).paginate(**params, **config) for item in page.get(key, [])]


def _ou_tree(names: _Names) -> list[tuple[str, str, str]]:
    """List every OU of the organization, walked down from the root (OUs nest at most 5 deep).

    Args:
        names: The call's _Names; each OU found is remembered in it.

    Returns:
        (ID, ARN, name) per OU, level by level; an empty list when the organization has no OU.
    """
    client = names.organizations()
    parents = [root["Id"] for root in _all(client, "list_roots", "Roots", page_size=None)]
    found = []
    for _ in range(5):
        children = [ou for parent in parents
                    for ou in _all(client, "list_organizational_units_for_parent", "OrganizationalUnits",
                                   page_size=None, ParentId=parent)]
        for ou in children:
            names.remember(ou["Id"], ou.get("Name", ""), ou.get("Arn", ""))
            found.append((ou["Id"], ou.get("Arn", ""), one_line(ou.get("Name", ""))))
        parents = [ou["Id"] for ou in children]
        if not parents:
            break
    return found


def _targets(target: str, names: _Names) -> list[_Target]:
    """The accounts and OUs that `target` names, each once, in order; [] means the whole landing zone.

    An account is its ID or its label (main, dev, prod); an OU is its ID or its name; either may also be its ARN.

    Args:
        target: Accounts and OUs, separated by commas; "", "all" or "*" for the whole landing zone.
        names: The call's _Names, which reads the OUs and the organization ID.

    Returns:
        One _Target per account or OU, in order, each ARN once; [] for the whole landing zone.

    Raises:
        ValueError: A name that is no LTTM account and no OU of this organization, or no organization ID to build an
            account's ARN with.
    """
    by_label = {info["label"]: account for account, info in _vars.ACCOUNTS.items()}
    found, tree = [], None
    for item in [part.strip() for part in (target or "").split(",") if part.strip()]:
        if item.lower() in ("all", "*"):
            continue
        match = _ORG_ARN.match(item)
        if match:
            kind, _, ident = match.groups()
            found.append(_Target(kind, ident, item, names.label(item)))
            continue
        account = by_label.get(item.lower(), item)
        if account in _vars.ACCOUNTS:
            org = names.org_id()
            if not org:
                raise ValueError("the organization ID could not be read (organizations:DescribeOrganization), "
                                 "so an account's ARN cannot be built")
            found.append(_Target("account", account, f"arn:aws:organizations::{_vars.ACC1_ID}:account/{org}/{account}",
                                 _account_label(account)))
            continue
        ou_id = item.lower()
        if _OU_ID.match(ou_id):
            name, arn = names.ou(ou_id)
            if not arn:
                raise ValueError(f"OU {item} was not found in this organization")
            found.append(_Target("ou", ou_id, arn, f"OU {name} ({ou_id})" if name else f"OU {ou_id}"))
            continue
        wanted = re.sub(r"^ou\s+|\s+ou$", "", item, flags=re.IGNORECASE).strip().lower()
        tree = _ou_tree(names) if tree is None else tree
        hits = [(ou_id, arn, name) for ou_id, arn, name in tree if name.lower() == wanted]
        if not hits:
            known = ", ".join(f"{name} ({ou_id})" for ou_id, _, name in tree) or "none"
            raise ValueError(f"unknown target '{item}': name an account ({', '.join(by_label)} or its ID) or an OU "
                             f"(its ID or its name; the OUs are: {known})")
        found += [_Target("ou", ou_id, arn, f"OU {name} ({ou_id})") for ou_id, arn, name in hits]
    return list({t.arn: t for t in found}.values())


def _scope(targets: list[_Target]) -> str:
    """Say what a call covers: the whole landing zone, or the targets' labels.

    Args:
        targets: The call's targets; [] for the whole landing zone.

    Returns:
        The targets' labels separated by commas, or WHOLE.
    """
    return ", ".join(t.label for t in targets) if targets else WHOLE


def _with(**filters) -> str:
    """Name the filters given, as " with status FAILED and drift status DRIFTED".

    Args:
        **filters: Filter names and values; empty values are left out.

    Returns:
        The phrase, starting with a space; "" when no filter has a value.
    """
    given = [f"{name.replace('_', ' ')} {value}" for name, value in filters.items() if value]
    return " with " + " and ".join(given) if given else ""


def _one_of(value: str, allowed: tuple, name: str) -> str:
    """Turn a filter value given in any case into the API's upper-case value.

    Args:
        value: The value the model gave, in any case, with "-" or spaces for "_".
        allowed: The API's values, in lower case.
        name: The parameter's name, for the error message.

    Returns:
        The value in upper case, as the API takes it; "" when empty.

    Raises:
        ValueError: A value the API does not have, refused before any call: botocore checks no enum values.
    """
    if not str(value or "").strip():
        return ""
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if key not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}")
    return key.upper()


def _day(text: str, name: str) -> date | None:
    """Read a YYYY-MM-DD date.

    Args:
        text: The date as the model gave it; only its first 10 characters are read.
        name: The parameter's name, for the error message.

    Returns:
        The date; None when `text` is empty.

    Raises:
        ValueError: `text` is not a YYYY-MM-DD date.
    """
    if not str(text or "").strip():
        return None
    try:
        return date.fromisoformat(str(text).strip()[:10])
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DD, got '{text}'")


def _version(text: str) -> tuple:
    """Read a landing zone version, for comparing: "4.0" -> (4, 0).

    Args:
        text: The version as the API gives it.

    Returns:
        The version's numbers; (0,) when it cannot be read.
    """
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return (0,)


def _when(value) -> str:
    """Write a timestamp from boto3 (datetime) or text as UTC ISO 8601.

    Args:
        value: A datetime, a text timestamp, or None.

    Returns:
        "YYYY-MM-DDTHH:MM:SSZ" for a datetime; a text as it is; "N/A" when missing.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value) if value else "N/A"


def _day_of(value) -> date | None:
    """Give the UTC day of a timestamp.

    Args:
        value: A datetime, a text timestamp that starts with YYYY-MM-DD, or None.

    Returns:
        The day; None when there is no timestamp, or it cannot be read.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _landing_zone(ct) -> dict:
    """Find the landing zone with ListLandingZones, and read GetLandingZone's details.

    Found on every call, never by a stored ID: a landing zone that is decommissioned and created again has a new ID.

    Args:
        ct: The controltower client.

    Returns:
        GetLandingZone's landingZone, with its "arn" always set.

    Raises:
        _NoLandingZone: Control Tower has none in its home region.
        ClientError: Any other error of the two calls.
    """
    try:
        arns = [zone.get("arn", "") for zone in _all(ct, "list_landing_zones", "landingZones", page_size=None)]
        if not arns:
            raise _NoLandingZone()
        zone = ct.get_landing_zone(landingZoneIdentifier=arns[0])["landingZone"]
    except ClientError as e:
        if _code(e) == "ResourceNotFoundException":
            raise _NoLandingZone() from e
        raise
    return {**zone, "arn": zone.get("arn") or arns[0]}


def _integrations(zone: dict) -> tuple[str, str]:
    """Describe the landing zone's integrations in one line, and give the AWS Config integration's state.

    INTEGRATIONS holds each one's manifest key, its name, and whether it was on without a flag before landing
    zone 4.0.

    Args:
        zone: The landing zone from _landing_zone.

    Returns:
        (the line, such as "centralized logging (CloudTrail) on, account …; AWS Config …", the AWS Config state:
        "on", "off", or "" when the manifest does not say).
    """
    manifest = zone.get("manifest") if isinstance(zone.get("manifest"), dict) else {}
    before_4 = _version(zone.get("version", "")) < (4,)
    parts, config = [], ""
    for key, name, implicit in INTEGRATIONS:
        section = manifest.get(key)
        if isinstance(section, dict) and isinstance(section.get("enabled"), bool):
            state = "on" if section["enabled"] else "off"
        elif before_4 and implicit:
            state = "on (implicit before 4.0)"
        else:
            state = "not set"
        account = str(section.get("accountId") or "") if isinstance(section, dict) else ""
        if account and state.startswith("on"):
            state += f", account {_account_label(account)}"
        parts.append(f"{name} {state}")
        if key == "config":
            config = "on" if state.startswith("on") else "off" if state == "off" else ""
    return "; ".join(parts), config


@one_line_args
def _format_landing_zone(zone_id: str, arn: str, version: str, latest: str, status: str, drift: str, home: str,
                         regions: str, integrations: str, detective: str, remediation: str, organization: str) -> str:
    """Format the landing zone as a block; every value on one line (one_line_args).

    Args:
        zone_id: The landing zone's ID.
        arn: Its ARN.
        version: Its version.
        latest: The latest version available.
        status: Its status.
        drift: Its drift status.
        home: Control Tower's home region.
        regions: The governed regions, separated by commas.
        integrations: The integrations line from _integrations.
        detective: The line on detective controls; "" for none.
        remediation: The remediation types, separated by commas.
        organization: This organization's ID.

    Returns:
        The "--- Control Tower landing zone ---" block.
    """
    lines = ["--- Control Tower landing zone ---",
             f"Landing zone: {zone_id} ({arn})",
             f"Version: {version} (latest available: {latest})",
             f"Status: {status}",
             f"Drift: {drift}",
             f"Home region: {home}. Governed regions: {regions}",
             f"Integrations: {integrations}"]
    if detective:
        lines.append(detective)
    lines += [f"Remediation types: {remediation}", f"Organization: {organization}"]
    return "\n".join(lines)


def _landing_zone_block(zone: dict, names: _Names) -> str:
    """Run the landing_zone action: version, status, drift, regions, integrations and organization.

    Args:
        zone: The landing zone from _landing_zone.
        names: The call's _Names, for the organization ID.

    Returns:
        The landing zone's block.
    """
    integrations, config = _integrations(zone)
    manifest = zone.get("manifest") if isinstance(zone.get("manifest"), dict) else {}
    regions = manifest.get("governedRegions") or []
    return _format_landing_zone(
        zone["arn"].rsplit("/", 1)[-1], zone["arn"], zone.get("version", "N/A"),
        zone.get("latestAvailableVersion") or "N/A", zone.get("status") or "N/A",
        (zone.get("driftStatus") or {}).get("status") or "N/A", _vars.CONTROLTOWER_REGION,
        ", ".join(map(str, regions)) or "none listed", integrations, DETECTIVE.get(config, ""),
        ", ".join(zone.get("remediationTypes") or []) or "none", names.org_id() or "unknown")


def _control_rows(records: list[dict], names: _Names) -> list[dict]:
    """Make one row per enabled control: its record, its catalog names, its status, drift and target label.

    Args:
        records: Enabled controls, as ListEnabledControls or GetEnabledControl return them.
        names: The call's _Names, to label the targets.

    Returns:
        One dict per record, with record, control (a _Control), status, drift, inheritance, resource and target.
    """
    rows = []
    for record in records:
        drift = record.get("driftStatusSummary") or {}
        types = drift.get("types") or {}
        rows.append({"record": record, "control": _control(record.get("controlIdentifier", "")),
                     "status": (record.get("statusSummary") or {}).get("status", ""),
                     "drift": drift.get("driftStatus", ""),
                     "inheritance": (types.get("inheritance") or {}).get("status", ""),
                     "resource": (types.get("resource") or {}).get("status", ""),
                     "target": names.label(record.get("targetIdentifier", ""))})
    return rows


def _control_counts(rows: list[dict]) -> list[str]:
    """Make the count lines over every control: by behavior, status, drift and target.

    Args:
        rows: The control rows from _control_rows.

    Returns:
        Four lines: "Enabled controls: N (…)", "By status: …", "By drift: …" and "By target: …".
    """
    targets = [row["target"] for row in rows]
    behaviors = [row["control"].behavior for row in rows]
    return ["Enabled controls" + count_line(behaviors, BEHAVIOR_ORDER)[len("Findings"):],
            f"By status: {_breakdown([row['status'].lower() or 'not reported' for row in rows], STATUS_ORDER)}",
            f"By drift: {_breakdown([row['drift'].lower() or 'not reported' for row in rows], DRIFT_ORDER)}",
            f"By target: {_breakdown(targets, sorted(set(targets)))}"]


NOTE = "Note (written by code):"


def _drift_note(rows: list[dict]) -> str:
    """Say what the drift values NOT_CHECKING and UNKNOWN mean, in lines written by code after the results.

    Without them, the supervisor read "Drift: NOT_CHECKING (inheritance IN_SYNC)" as no drift detected. The meanings
    are the API's own (botocore's model of DriftStatusSummary.driftStatus). Why this landing zone does not check
    drift is not verified, so no reason is given: the supervisor explains only from what code says.

    Args:
        rows: Every counted control row, not only the listed ones.

    Returns:
        One "Note (written by code):" line for each of the two values that occurs; "" when every control's drift is
        checked.
    """
    lines = []
    for value, meaning in (("NOT_CHECKING", "Control Tower does not check drift for {them}"),
                           ("UNKNOWN", "Control Tower could not check the drift of {them}")):
        hit = [row for row in rows if (row["drift"] or "").upper() == value]
        if not hit:
            continue
        many = len(hit) > 1
        them = "these controls" if many else "this control"
        inheritance = sorted({row["inheritance"] for row in hit if row["inheritance"]})
        checked = (f" Only {'their' if many else 'its'} inheritance is checked: {', '.join(inheritance)}."
                   if inheritance else "")
        lines.append(f"{NOTE} Drift {value} ({len(hit)} enabled control{'s' if many else ''}): "
                     f"{meaning.format(them=them)}, so whether {'they' if many else 'it'} drifted is not known; "
                     f"it does not mean that nothing drifted.{checked}")
    return "\n".join(lines)


@one_line_args
def _format_control(control: str, behavior: str, target: str, status: str, drift: str, arn: str, inherited: str = "",
                    regions: str = "", parameters: str = "") -> str:
    """Format one enabled control as a block; every value on one line (one_line_args).

    Args:
        control: The control's label (_control_label).
        behavior: Its behavior, with its implementation and severity when known.
        target: The target's label.
        status: Its enablement status.
        drift: Its drift status, with the inheritance and resource parts.
        arn: The enabled control's ARN.
        inherited: What it is inherited from; "" for nothing.
        regions: Its target regions; "" to leave the line out.
        parameters: Its parameters; "" to leave the line out.

    Returns:
        The "--- Enabled control ---" block.
    """
    lines = ["--- Enabled control ---", f"Control: {control}", f"Behavior: {behavior}", f"Target: {target}",
             f"Status: {status}", f"Drift: {drift}"]
    if inherited:
        lines.append(f"Inherited from: {inherited}")
    if regions:
        lines.append(f"Target regions: {regions}")
    if parameters:
        lines.append(f"Parameters: {parameters}")
    lines.append(f"Enabled control: {arn}")
    return "\n".join(lines)


def _control_block(row: dict, details: bool = False) -> str:
    """Format a control row as a block; with `details`, GetEnabledControl's target regions and parameters too.

    Args:
        row: A control row from _control_rows.
        details: True when the row's record is from GetEnabledControl.

    Returns:
        The control's block.
    """
    record, control = row["record"], row["control"]
    behavior = "unknown"
    if control.named:
        how = f" ({control.implementation})" if control.implementation else ""
        behavior = f"{control.behavior}{how}" + (f", severity {control.severity}" if control.severity else "")
    split = ", ".join(f"{part} {row[part]}" for part in ("inheritance", "resource") if row[part])
    drift = (row["drift"] or "not reported") + (f" ({split})" if split else "")
    regions = parameters = ""
    if details:
        regions = ", ".join(r.get("name", "") for r in record.get("targetRegions") or []) or "none listed"
        parameters = "; ".join(f"{p.get('key')} = {json.dumps(p.get('value'), default=str)}"
                               for p in record.get("parameters") or []) or "none"
    return _format_control(_control_label(record.get("controlIdentifier", "")), behavior, row["target"],
                           row["status"] or "not reported", drift, record.get("arn", "N/A"),
                           record.get("parentIdentifier", ""), regions, parameters)


def _listed(rows: list, limit: int, noun: str) -> str:
    """Say how many of the counted items are listed below.

    Args:
        rows: Every counted item.
        limit: The most items listed.
        noun: What the items are, such as "enabled controls".

    Returns:
        "Listed below: L shown of N in total; …" when some are left out; "Listed below: all N <noun>." otherwise.
    """
    if len(rows) > limit:
        return f"Listed below: {limit} shown of {len(rows)} in total; the counts above cover all {len(rows)}."
    return f"Listed below: all {len(rows)} {noun}."


def _matches(row: dict, control: str) -> bool:
    """Tell whether a control row is the control asked for: its alias, its control ARN or its enabled-control ARN.

    Args:
        row: A control row from _control_rows.
        control: The control asked for, in any case.

    Returns:
        True when it equals one of the three, ignoring case; False otherwise.
    """
    wanted = control.strip().lower()
    record = row["record"]
    return wanted in (row["control"].alias.lower(), str(record.get("controlIdentifier", "")).lower(),
                      str(record.get("arn", "")).lower())


def _list_controls(ct, targets: list[_Target], status: str = "", drift: str = "") -> list[dict]:
    """ListEnabledControls for the whole landing zone (one call, no target) or once per target, merged.

    An account target adds includeChildren, so the controls it inherits from its OUs are listed too. A control
    listed for two targets counts once (by its enabled-control ARN).

    Args:
        ct: The controltower client.
        targets: The targets; [] for the whole landing zone.
        status: An enablement status in the API's form; "" for every status.
        drift: A drift status in the API's form; "" for every drift status.

    Returns:
        The enabled-control records, each once.
    """
    filters = {key: [value] for key, value in (("statuses", status), ("driftStatuses", drift)) if value}
    calls = [{}] if not targets else [
        {"targetIdentifier": t.arn, **({"includeChildren": True} if t.kind == "account" else {})} for t in targets]
    records = {}
    for call in calls:
        for record in _all(ct, "list_enabled_controls", "enabledControls", **call,
                           **({"filter": filters} if filters else {})):
            records.setdefault(record.get("arn") or (record.get("controlIdentifier"), record.get("targetIdentifier")),
                               record)
    return list(records.values())


def _sorted_rows(rows: list[dict]) -> list[dict]:
    """Sort control rows: drifted and failed ones first, then by target and control.

    Args:
        rows: Control rows from _control_rows.

    Returns:
        The rows, sorted.
    """
    return sorted(rows, key=lambda r: (r["drift"] != "DRIFTED", r["status"] != "FAILED", r["target"],
                                       r["control"].alias))


def _enabled_controls(ct, names: _Names, targets: list[_Target], status: str, drift: str, behavior: str,
                      control: str, limit: int) -> tuple[str, str]:
    """Run the enabled_controls action: count lines over every control, then one block per control.

    The drift note (_drift_note) comes after the results, and covers every counted control.

    Args:
        ct: The controltower client.
        names: The call's _Names.
        targets: The targets; [] for the whole landing zone.
        status: An enablement status in the API's form; "" for every status.
        drift: A drift status in the API's form; "" for every drift status.
        behavior: PREVENTIVE, PROACTIVE or DETECTIVE; "" for every behavior.
        control: One control's alias or ARN; "" for every control.
        limit: The most controls listed in detail.

    Returns:
        (the result's text, a short summary for the log and the status line).
    """
    rows = _control_rows(_list_controls(ct, targets, status, drift), names)
    notes = []
    if control:
        rows = [row for row in rows if _matches(row, control)]
    if behavior:
        unknown = sum(row["control"].behavior == "unknown" for row in rows)
        rows = [row for row in rows if row["control"].behavior == behavior.lower()]
        if unknown:
            notes.append(f"Left out: {unknown} enabled controls whose behavior the Control Catalog could not give.")
    asked = _with(control=control, status=status, drift_status=drift, behavior=behavior.lower())
    if not rows:
        return (f"Enabled controls: none. Control Tower returned no enabled controls for {_scope(targets)}{asked}."
                + "".join("\n" + note for note in notes)), "no enabled controls"
    inherited = " (an account's controls include those it inherits from its OUs)" if any(
        t.kind == "account" for t in targets) else ""
    tail = "; accounts inherit the OU-level controls." if not targets else f"{inherited}."
    lines = _control_counts(rows) + [f"Counted by code over all {len(rows)} enabled controls of {_scope(targets)}"
                                     f"{asked}{tail}"] + notes + [_listed(rows, limit, "enabled controls")]
    blocks = [_control_block(row) for row in _sorted_rows(rows)[:limit]]
    note = _drift_note(rows)
    return ("\n".join(lines) + "\n\n" + "\n\n".join(blocks) + (f"\n\n{note}" if note else ""),
            f"{len(rows)} enabled controls")


def _control_details(ct, names: _Names, targets: list[_Target], control: str, limit: int) -> tuple[str, str]:
    """Run the control_details action: GetEnabledControl for an enabled-control ARN, or for each control of an alias.

    Args:
        ct: The controltower client.
        names: The call's _Names.
        targets: The targets an alias is looked up in; [] for the whole landing zone.
        control: An enabled-control ARN, or a control's alias or ARN.
        limit: The most enabled controls read.

    Returns:
        (the result's text, a short summary for the log and the status line); the text says "Enabled controls:
        none." when no such control is enabled.

    Raises:
        ClientError: Any error of GetEnabledControl other than ResourceNotFoundException.
    """
    control = control.strip()
    if ":enabledcontrol/" in control:
        arns = [control]
    else:
        rows = [row for row in _control_rows(_list_controls(ct, targets), names) if _matches(row, control)]
        arns = [row["record"]["arn"] for row in _sorted_rows(rows) if row["record"].get("arn")][:limit]
    details = []
    for arn in arns:
        try:
            details.append(ct.get_enabled_control(enabledControlIdentifier=arn)["enabledControlDetails"])
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
    if not details:
        return (f"Enabled controls: none. Control Tower returned no enabled control {control} for "
                f"{_scope(targets)}."), "no enabled control"
    rows = _control_rows(details, names)
    lines = _control_counts(rows) + [f"Details of {len(rows)} enabled control{'s' if len(rows) > 1 else ''} "
                                     f"{control}, from GetEnabledControl."]
    note = _drift_note(rows)
    return ("\n".join(lines) + "\n\n" + "\n\n".join(_control_block(row, details=True) for row in rows)
            + (f"\n\n{note}" if note else ""), f"{len(rows)} enabled control details")


@one_line_args
def _format_baseline(baseline: str, target: str, status: str, drift: str, arn: str, inherited: str = "",
                     operation: str = "") -> str:
    """Format one enabled baseline as a block; every value on one line (one_line_args).

    Args:
        baseline: The baseline's name, with its version when known.
        target: The target's label.
        status: Its enablement status.
        drift: Its inheritance drift status.
        arn: The enabled baseline's ARN.
        inherited: What it is inherited from; "" for nothing.
        operation: The ID of its last operation; "" for none.

    Returns:
        The "--- Enabled baseline ---" block.
    """
    lines = ["--- Enabled baseline ---", f"Baseline: {baseline}", f"Target: {target}", f"Status: {status}",
             f"Drift: {drift}"]
    if inherited:
        lines.append(f"Inherited from: {inherited}")
    if operation:
        lines.append(f"Last operation: {operation}")
    lines.append(f"Enabled baseline: {arn}")
    return "\n".join(lines)


def _baseline_names(ct) -> dict[str, str]:
    """Read every baseline's name, from ListBaselines.

    Args:
        ct: The controltower client.

    Returns:
        Each baseline's ARN, mapped to its name on one line.
    """
    return {b.get("arn", ""): one_line(b.get("name", "")) for b in _all(ct, "list_baselines", "baselines")}


def _baseline_rows(records: list[dict], names: _Names, baseline_names: dict[str, str]) -> list[dict]:
    """Make one row per enabled baseline: its record, its baseline's name, its status, drift and target label.

    Args:
        records: Enabled baselines, as ListEnabledBaselines returns them.
        names: The call's _Names, to label the targets.
        baseline_names: The baselines' names by ARN (_baseline_names).

    Returns:
        One dict per record, with record, status, name, drift (inheritance drift) and target.
    """
    rows = []
    for record in records:
        identifier = record.get("baselineIdentifier", "")
        drift = ((record.get("driftStatusSummary") or {}).get("types") or {}).get("inheritance") or {}
        rows.append({"record": record, "status": (record.get("statusSummary") or {}).get("status", ""),
                     "name": baseline_names.get(identifier) or one_line(identifier.rsplit("/", 1)[-1] or "unknown"),
                     "drift": drift.get("status", ""), "target": names.label(record.get("targetIdentifier", ""))})
    return rows


def _baseline_counts(rows: list[dict]) -> list[str]:
    """Make the count lines over every enabled baseline: by baseline, status, drift and target.

    Args:
        rows: The baseline rows from _baseline_rows.

    Returns:
        Four lines: "Enabled baselines: N (…)", "By status: …", "By drift: …" and "By target: …".
    """
    targets = [row["target"] for row in rows]
    return [f"Enabled baselines: {len(rows)} ({_breakdown([row['name'] for row in rows])})",
            f"By status: {_breakdown([row['status'].lower() or 'not reported' for row in rows], STATUS_ORDER)}",
            f"By drift: {_breakdown([row['drift'].lower() or 'not reported' for row in rows], DRIFT_ORDER)}",
            f"By target: {_breakdown(targets, sorted(set(targets)))}"]


def _no_baselines(scope: str, asked: str, config: str) -> str:
    """Write the line for no enabled baselines; with the AWS Config integration off, why.

    Args:
        scope: What the call covers (_scope).
        asked: The filters given (_with).
        config: The AWS Config integration's state: "on", "off" or "".

    Returns:
        "Enabled baselines: none. …", followed by NO_CONFIG_BASELINES when `config` is "off".
    """
    return (f"Enabled baselines: none. Control Tower returned no enabled baselines for {scope}{asked}."
            + (NO_CONFIG_BASELINES if config == "off" else ""))


def _enabled_baselines(ct, names: _Names, targets: list[_Target], status: str, drift: str,
                       limit: int) -> tuple[str, str]:
    """Run the enabled_baselines action: count lines over every enabled baseline, then one block each.

    Baselines report inheritance drift only, so the drift filter is the inheritance drift's.

    Args:
        ct: The controltower client.
        names: The call's _Names.
        targets: The targets; [] for the whole landing zone.
        status: An enablement status in the API's form; "" for every status.
        drift: DRIFTED or IN_SYNC; "" for every drift status.
        limit: The most baselines listed in detail.

    Returns:
        (the result's text, a short summary for the log and the status line).
    """
    filters = {key: [value] for key, value in (("statuses", status), ("inheritanceDriftStatuses", drift)) if value}
    calls = [{"filter": filters} if filters else {}] if not targets else [
        {"filter": {"targetIdentifiers": [t.arn], **filters},
         **({"includeChildren": True} if t.kind == "account" else {})}
        for t in targets]
    records = {}
    for call in calls:
        for record in _all(ct, "list_enabled_baselines", "enabledBaselines", **call):
            records.setdefault(record.get("arn"), record)
    asked = _with(status=status, drift_status=drift)
    if not records:
        return _no_baselines(_scope(targets), asked, _integrations(_landing_zone(ct))[1]), "no enabled baselines"
    rows = _baseline_rows(list(records.values()), names, _baseline_names(ct))
    lines = _baseline_counts(rows) + [f"Counted by code over all {len(rows)} enabled baselines of {_scope(targets)}"
                                      f"{asked}.", _listed(rows, limit, "enabled baselines")]
    blocks = [_format_baseline(row["name"] + (f" (version {row['record']['baselineVersion']})"
                                              if row["record"].get("baselineVersion") else ""),
                               row["target"], row["status"] or "not reported",
                               f"{row['drift']} (inheritance)" if row["drift"] else "not reported",
                               row["record"].get("arn", "N/A"), row["record"].get("parentIdentifier", ""),
                               (row["record"].get("statusSummary") or {}).get("lastOperationIdentifier", ""))
              for row in sorted(rows, key=lambda r: (r["drift"] != "DRIFTED", r["status"] != "FAILED",
                                                     r["target"]))[:limit]]
    return "\n".join(lines) + "\n\n" + "\n\n".join(blocks), f"{len(rows)} enabled baselines"


@one_line_args
def _format_operation(kind: str, operation_type: str, operation_id: str, status: str, started: str, ended: str,
                      message: str = "", control: str = "", target: str = "", enabled_control: str = "") -> str:
    """Format one operation as a block; every value on one line (one_line_args).

    Args:
        kind: "landing zone", "control" or "baseline".
        operation_type: The operation's type, such as ENABLE_CONTROL.
        operation_id: Its ID.
        status: Its status.
        started: When it started (UTC ISO 8601).
        ended: When it ended.
        message: Its status message; "" for none.
        control: The control it changed, labelled; "" for none.
        target: The target's label; "" for none.
        enabled_control: The enabled control's ARN; "" for none.

    Returns:
        The "--- Control Tower operation ---" block.
    """
    lines = ["--- Control Tower operation ---", f"Operation: {kind} operation {operation_type}",
             f"Operation ID: {operation_id}", f"Status: {status}", f"Started: {started}", f"Ended: {ended}"]
    if message:
        lines.append(f"Message: {message}")
    if control:
        lines.append(f"Control: {control}")
    if target:
        lines.append(f"Target: {target}")
    if enabled_control:
        lines.append(f"Enabled control: {enabled_control}")
    return "\n".join(lines)


def _operation_row(kind: str, op: dict, names: _Names) -> dict:
    """Make a row of one operation of any kind: what it was, its status and times, and for a control what it changed.

    Args:
        kind: "landing zone", "control" or "baseline".
        op: The operation as a list or get call returns it.
        names: The call's _Names, to label the target.

    Returns:
        A dict with kind, type, id, status, start, end, message, label, control, target and enabled_control.
    """
    op_type = op.get("operationType", "")
    control = op.get("controlIdentifier", "")
    return {"kind": kind, "type": op_type, "id": op.get("operationIdentifier", ""), "status": op.get("status", ""),
            "start": op.get("startTime"), "end": op.get("endTime"), "message": op.get("statusMessage", ""),
            "label": f"landing zone {op_type.lower()}" if kind == "landing zone" else op_type.lower().replace("_", " "),
            "control": _control_label(control) if control else "",
            "target": names.label(op["targetIdentifier"]) if op.get("targetIdentifier") else "",
            "enabled_control": op.get("enabledControlIdentifier", "")}


def _operation_block(row: dict) -> str:
    """Format an operation row as a block.

    Args:
        row: An operation row from _operation_row.

    Returns:
        The operation's block.
    """
    return _format_operation(row["kind"], row["type"] or "N/A", row["id"] or "N/A", row["status"] or "N/A",
                             _when(row["start"]), _when(row["end"]), row["message"], row["control"], row["target"],
                             row["enabled_control"])


def _operations(ct, names: _Names, targets: list[_Target], status: str, start: date | None, end: date | None,
                limit: int) -> tuple[str, str]:
    """The operations action: landing zone and control operations, filtered by status and date, counted by code.

    ListLandingZoneOperations has no times, so GetLandingZoneOperation adds each one's start, end and message.
    With a target, only control operations count: a landing zone operation has no OU or account. Each kind gets
    its own count line of types and statuses: one status line over both kinds read as the landing zone's.

    Args:
        ct: The controltower client.
        names: The call's _Names.
        targets: The targets; [] for the whole landing zone.
        status: SUCCEEDED, FAILED or IN_PROGRESS; "" for every status.
        start: The first day, by the operation's start; None for as far back as the API keeps.
        end: The last day, inclusive; None for today.
        limit: The most operations listed in detail, newest first.

    Returns:
        (the result's text, a short summary for the log and the status line).
    """
    rows = []
    if not targets:
        for summary in _all(ct, "list_landing_zone_operations", "landingZoneOperations"):
            details = ct.get_landing_zone_operation(operationIdentifier=summary["operationIdentifier"])
            rows.append(_operation_row("landing zone", {**summary, **details.get("operationDetails", {})}, names))
    wanted = {t.arn for t in targets} | {t.ident for t in targets}
    for op in _all(ct, "list_control_operations", "controlOperations"):
        identifier = op.get("targetIdentifier", "")
        if not targets or identifier in wanted or identifier.rsplit("/", 1)[-1] in wanted:
            rows.append(_operation_row("control", op, names))
    if status:
        rows = [row for row in rows if row["status"] == status]
    if start or end:
        rows = [row for row in rows if _day_of(row["start"]) and (not start or _day_of(row["start"]) >= start)
                and (not end or _day_of(row["start"]) <= end)]
    dates = (f", {start.isoformat() if start else 'as far back as the API keeps'} to "
             f"{end.isoformat() if end else 'today'}") if start or end else ""
    scope = _scope(targets) + (" (control operations only: a landing zone operation has no OU or account)"
                               if targets else "")
    source = ("Landing zone operations: the API keeps the last 90 days. Baseline operations have no list API: "
              "ask for one by its operation ID.")
    asked = _with(status=status)
    if not rows:
        return f"Operations: none. Control Tower returned no operations for {scope}{dates}{asked}. {source}", \
            "no operations"
    lines = [f"Operations: {len(rows)} ({_breakdown([row['kind'] for row in rows], ['landing zone', 'control'])})"]
    for kind in ("landing zone", "control"):
        mine = [row for row in rows if row["kind"] == kind]
        if mine:
            lines.append(f"{kind.capitalize()} operations: {len(mine)} ({_breakdown([row['label'] for row in mine])}); "
                         f"by status: {_breakdown([row['status'].lower() or 'not reported' for row in mine], STATUS_ORDER)}")
    lines += [f"Counted by code over all {len(rows)} operations of {scope}{dates}{asked}. {source}",
              _listed(rows, limit, "operations")]
    newest = sorted(rows, key=lambda r: _when(r["start"]) if r["start"] else "", reverse=True)[:limit]
    return "\n".join(lines) + "\n\n" + "\n\n".join(_operation_block(row) for row in newest), f"{len(rows)} operations"


def _operation(ct, names: _Names, operation_id: str) -> tuple[str, str]:
    """Run the operation action: the landing zone, control or baseline operation with this ID.

    Baseline operations have no list API, so this is the only way to read one.

    Args:
        ct: The controltower client.
        names: The call's _Names.
        operation_id: The 36-character operation ID, in lower case.

    Returns:
        (the operation's block, a short summary for the log and the status line); a "No Control Tower operation
        with ID …" text when none of the three kinds has it.

    Raises:
        ClientError: Any error of the get calls other than ResourceNotFoundException.
    """
    for getter, key, kind in (("get_landing_zone_operation", "operationDetails", "landing zone"),
                              ("get_control_operation", "controlOperation", "control"),
                              ("get_baseline_operation", "baselineOperation", "baseline")):
        try:
            op = getattr(ct, getter)(operationIdentifier=operation_id)[key]
        except ClientError as e:
            if _code(e) == "ResourceNotFoundException":
                continue
            raise
        return _operation_block(_operation_row(kind, {"operationIdentifier": operation_id, **op}, names)), \
            f"1 {kind} operation"
    return (f"No Control Tower operation with ID {operation_id}: Control Tower returned no landing zone, control or "
            f"baseline operation with that ID."), "no operation"


def _overview(ct, names: _Names) -> tuple[str, str]:
    """Run the overview action: the landing zone's health and every count, without item blocks.

    The drift note (_drift_note) is added too: the overview's "By drift" line counts the controls.

    Args:
        ct: The controltower client.
        names: The call's _Names.

    Returns:
        (the result's text, a short summary for the log and the status line).

    Raises:
        _NoLandingZone: Control Tower has no landing zone in its home region.
    """
    zone = _landing_zone(ct)
    config = _integrations(zone)[1]
    lines = [f"Landing zone: {one_line(zone['arn'].rsplit('/', 1)[-1])} — version {zone.get('version', 'N/A')} "
             f"(latest available: {zone.get('latestAvailableVersion') or 'N/A'}), "
             f"status {zone.get('status') or 'N/A'}, "
             f"drift {(zone.get('driftStatus') or {}).get('status') or 'N/A'}"]
    rows = _control_rows(_list_controls(ct, []), names)
    lines += _control_counts(rows) if rows else [f"Enabled controls: none. Control Tower returned no enabled controls "
                                                 f"for {WHOLE}."]
    baselines = _all(ct, "list_enabled_baselines", "enabledBaselines")
    if baselines:
        lines += _baseline_counts(_baseline_rows(baselines, names, _baseline_names(ct)))
    else:
        lines.append(_no_baselines(WHOLE, "", config))
    operations = _all(ct, "list_landing_zone_operations", "landingZoneOperations")
    if operations:
        lines += [f"Landing zone operations in the last 90 days: {len(operations)} "
                  f"({_breakdown([str(op.get('operationType', '')).lower() for op in operations])})",
                  f"Landing zone operations by status: "
                  f"{_breakdown([str(op.get('status', '')).lower() or 'not reported' for op in operations],
                                STATUS_ORDER)}"]
    else:
        lines.append("Landing zone operations in the last 90 days: none. Control Tower returned no landing zone "
                     "operations.")
    lines.append(f"Counted by code over {WHOLE}; accounts inherit the OU-level controls.")
    done = f"landing zone {zone.get('version', '')} {zone.get('status', '')}, {len(rows)} enabled controls"
    note = _drift_note(rows)
    return "\n".join(lines) + (f"\n{note}" if note else ""), done


def _client_error(error: ClientError) -> str:
    """Turn a Control Tower or Organizations error into the message the sub-agent returns.

    An AccessDenied names the action the role lacks: the calls a target needs (_ORGANIZATIONS_CALLS) are AWS
    Organizations actions, and every other call this tool makes is a Control Tower action.

    Args:
        error: The ClientError.

    Returns:
        "Error querying Control Tower: …".
    """
    code, message = _code(error), error.response.get("Error", {}).get("Message", "")
    operation = getattr(error, "operation_name", "") or "a call"
    if code in ("AccessDeniedException", "AccessDenied"):
        service = "organizations" if operation in _ORGANIZATIONS_CALLS else "controltower"
        return (f"Error querying Control Tower: AccessDenied: the execution role lacks {service}:{operation} "
                f"(or an action it needs): {message}")
    return f"Error querying Control Tower: {code or type(error).__name__}: {message}"


@tool
def query_controltower_api(
    action: str = "overview",
    target: str = "",
    status: str = "",
    drift_status: str = "",
    behavior: str = "",
    control: str = "",
    operation_id: str = "",
    start_date: str = "",
    end_date: str = "",
    max_results: int = 50,
) -> str:
    """
    Read AWS Control Tower in the management account: the landing zone, its enabled controls and
    baselines, their drift, and its operations. One call covers every OU and account of the question;
    never make one call per account or OU.

    Args:
        action: overview (default: landing zone health and all counts), landing_zone (settings and
            integrations), enabled_controls, control_details, enabled_baselines, operations, or
            operation (one operation by operation_id).
        target: Empty (default) = the whole landing zone. Otherwise accounts (ID or main/dev/prod)
            and OUs (ou-xxxx-xxxxxxxx or the OU name), several separated by commas.
        status: enabled_controls/enabled_baselines: succeeded, failed or under_change;
            operations: succeeded, failed or in_progress. Empty = all.
        drift_status: drifted, in_sync, not_checking or unknown (controls); drifted or in_sync
            (baselines). Empty = all.
        behavior: enabled_controls only: preventive, proactive or detective. Empty = all.
        control: control_details: the enabled-control ARN, or a control alias such as
            AWS-GR_RESTRICT_ROOT_USER (add target when it is enabled on several OUs).
            enabled_controls: only this control. Empty = all.
        operation_id: The 36-character operation ID, for action=operation.
        start_date: operations only: first day, YYYY-MM-DD. Empty = as far back as the API keeps.
        end_date: operations only: last day, YYYY-MM-DD, inclusive. Empty = today.
        max_results: How many items to list in detail (default 50, at most 200); the counts cover all.

    Returns:
        Count lines computed by code ("Enabled controls: N (…)", "By drift: …", "By target: …"),
        then one block per item; or a message such as "No Control Tower landing zone in <home region>";
        or "Error querying Control Tower: …".
    """
    action = str(action or "overview").strip().lower()
    emit_status(f"Querying Control Tower ({action})...", source="controltower_tool")
    try:
        if action not in ACTIONS:
            raise ValueError(f"unknown action '{action}'; the actions are {', '.join(ACTIONS)}")
        used = USED[action]
        given = {"target": target, "status": status, "drift_status": drift_status, "behavior": behavior,
                 "control": control, "operation_id": operation_id, "start_date": start_date, "end_date": end_date}
        unused = [name for name, value in given.items() if str(value or "").strip() and name not in used]

        state = _one_of(status, OPERATION_STATUSES if action == "operations" else ENABLEMENT_STATUSES,
                        "status") if "status" in used else ""
        drift = _one_of(drift_status, BASELINE_DRIFT if action == "enabled_baselines" else CONTROL_DRIFT,
                        "drift_status") if "drift_status" in used else ""
        kind = _one_of(behavior, BEHAVIORS, "behavior") if "behavior" in used else ""
        start = _day(start_date, "start_date") if "start_date" in used else None
        end = _day(end_date, "end_date") if "end_date" in used else None
        if start and end and start > end:
            raise ValueError(f"start_date {start} is after end_date {end}")
        operation = str(operation_id or "").strip().lower()
        if action == "operation" and not _OPERATION_ID.match(operation):
            raise ValueError("operation_id must be the 36-character operation ID, "
                             "e.g. 0123abcd-4567-89ef-0123-456789abcdef")
        if action == "control_details" and not str(control or "").strip():
            raise ValueError("control_details needs control: the enabled-control ARN or a control alias")
        limit = min(max(int(max_results), 1), MAX_RESULTS)

        ct = boto3.client("controltower", region_name=_vars.CONTROLTOWER_REGION)
        names = _Names()
        targets = _targets(target, names) if "target" in used else []
        if action == "overview":
            text, done = _overview(ct, names)
        elif action == "landing_zone":
            zone = _landing_zone(ct)
            text, done = _landing_zone_block(zone, names), f"landing zone {zone.get('version', '')}"
        elif action == "enabled_controls":
            text, done = _enabled_controls(ct, names, targets, state, drift, kind, str(control or ""), limit)
        elif action == "control_details":
            text, done = _control_details(ct, names, targets, str(control), limit)
        elif action == "enabled_baselines":
            text, done = _enabled_baselines(ct, names, targets, state, drift, limit)
        elif action == "operations":
            text, done = _operations(ct, names, targets, state, start, end, limit)
        else:
            text, done = _operation(ct, names, operation)
        if unused:
            text += f"\n\nNot used by action={action}: {', '.join(unused)}."
        print(f"[LTTM:ControlTower] {action}: {done}", flush=True)
        emit_status(f"Control Tower query complete — {done}", source="controltower_tool")
        return text
    except _NoLandingZone:
        msg = (f"No Control Tower landing zone in {_vars.CONTROLTOWER_REGION}: Control Tower returned no landing "
               f"zone.")
    except ValueError as e:
        msg = f"Error querying Control Tower: {e}"
    except ClientError as e:
        msg = _client_error(e)
    except Exception as e:
        msg = f"Error querying Control Tower: {type(e).__name__}: {e}"
    print(f"[LTTM:ControlTower] {msg}", flush=True)
    return msg
