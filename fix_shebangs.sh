#!/usr/bin/env bash
# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# fix_shebangs.sh — Repair the launcher scripts in the cached AgentCore dependency bundle
#
# What it does:
#   When `agentcore launch` rebuilds agents/.bedrock_agentcore/<agent>/dependencies.zip
#   (every time agents/requirements.txt changes), `uv` writes the path of the local
#   venv Python into the launcher scripts in the bundle's bin/ folder, for example
#   '/path/to/project/.venv/bin/python'. AgentCore starts the agent through
#   bin/opentelemetry-instrument, so the runtime fails with
#   "Runtime initialization time exceeded. Please make sure that initialization
#   completes in 30s.".
#
#   This script rewrites those launchers to start a bare `python3` (found on the
#   runtime's PATH) and writes the zip back in place. It leaves dependencies.hash
#   alone, so the next `agentcore launch` reuses the fixed zip — and it checks that
#   this is really true (the hash must still match requirements.txt, uv.lock and the
#   runtime version, the same rule the deploy tool uses).
#
#   Launcher forms it repairs (first lines of each bin/ script):
#     1. uv's sh wrapper:  #!/bin/sh  +  '''exec' <interpreter> "$0" "$@"  +  ' '''
#        -> <interpreter> becomes 'python3' (any form: quoted path, relative path,
#           relocatable venv expression)
#     2. a plain shebang to a Python interpreter:   #!/abs/path/.venv/bin/python3
#     3. #!/usr/bin/env python3 — what the deploy tool's own repair writes; it fails
#        on the PYTHON_3_12 runtime, which has no /usr/bin/env
#        -> forms 2 and 3 become uv's sh wrapper with 'python3'
#   Everything else in the zip is copied unchanged (content, permissions, dates).
#   A bin/ script that still looks like a Python launcher in a form it does not
#   know stops the script with exit code 2 instead of a silent "OK".
#
# Recommended flow when agents/requirements.txt changes (one deploy, no broken version):
#   ./fix_shebangs.sh --prebuild             # build the bundle locally the way launch does, then repair it
#   cd agents && agentcore launch ...        # must print "Using cached dependencies (no changes detected)"
#
# How to run (from the folder that holds agents/, with the venv active):
#   ./fix_shebangs.sh                  # repair the default agent's cached bundle
#   ./fix_shebangs.sh --check          # report only; exit 1 if something needs doing
#   ./fix_shebangs.sh --prebuild       # rebuild the bundle locally if needed, then repair it
#   ./fix_shebangs.sh --agent NAME     # another agent in agents/.bedrock_agentcore/
#   ./fix_shebangs.sh --zip PATH       # any dependencies zip (overrides --agent)
#
# Exit codes: 0 = bundle is ready (fixed now or nothing to fix), 1 = --check found
#   work to do, 2 = error, unrecognised launcher, or a bundle the next launch would
#   rebuild anyway (nothing was changed).
#
# Prerequisites:
#   - bash; python3 on PATH (the project venv: --prebuild and the exact cache check
#     use the installed bedrock-agentcore-starter-toolkit; uv must be on PATH for --prebuild)
#
# Backup: before writing, the original zip is copied to <zip>.before-shebang-fix.
#   The deploy tool never packages the .bedrock_agentcore/ folder.

if [ -z "${BASH_VERSION:-}" ]; then
  echo "ERROR: run this script with bash: bash $0" >&2
  exit 2
fi

set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found on PATH (activate the venv: source .venv/bin/activate)" >&2
  exit 2
fi

SCRIPT_PATH="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
AGENTS_DIR="$SCRIPT_DIR/agents"
AGENTCORE_YAML="$AGENTS_DIR/.bedrock_agentcore.yaml"
FALLBACK_AGENT="lttm_supervisor_stream"

# Print the help: the blocks of the header comment from "How to run" to "Exit codes".
#
# Args:
#   None.
#
# Returns:
#   0. Prints the blocks on stdout, without the leading "# " of each line.
usage() {
  sed -n 's/^# \{0,1\}//; /^How to run/,/^Prerequisites/p' "$SCRIPT_PATH" | sed '$d'
}

MODE="fix"
AGENT=""
AGENT_GIVEN=0
ZIP_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|--prebuild)
      if [[ "$MODE" != "fix" ]]; then
        echo "ERROR: use only one of --check and --prebuild" >&2
        exit 2
      fi
      MODE="${1#--}"
      shift
      ;;
    --agent)
      [[ $# -ge 2 ]] || { echo "ERROR: --agent needs a name" >&2; exit 2; }
      AGENT="$2"
      AGENT_GIVEN=1
      shift 2
      ;;
    --zip)
      [[ $# -ge 2 ]] || { echo "ERROR: --zip needs a path" >&2; exit 2; }
      ZIP_PATH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$AGENT" ]]; then
  if [[ -f "$AGENTCORE_YAML" ]]; then
    AGENT="$(awk -F': *' '/^default_agent:/ {gsub(/["'\'' \r]/, "", $2); print $2; exit}' "$AGENTCORE_YAML")"
  fi
  AGENT="${AGENT:-$FALLBACK_AGENT}"
fi

if [[ -z "$ZIP_PATH" && ! -f "$AGENTCORE_YAML" ]]; then
  echo "ERROR: cannot find $AGENTCORE_YAML" >&2
  echo "       (the script expects to live next to agents/; pass --zip PATH otherwise)" >&2
  exit 2
fi

if [[ "$MODE" == "prebuild" && -n "$ZIP_PATH" ]]; then
  echo "ERROR: --prebuild works on the agent's own bundle; use --agent, not --zip" >&2
  exit 2
fi

CACHE_ZIP="$AGENTS_DIR/.bedrock_agentcore/$AGENT/dependencies.zip"
ZIP_PATH="${ZIP_PATH:-$CACHE_ZIP}"

RERUN="$0"
if [[ "$ZIP_PATH" != "$CACHE_ZIP" ]]; then
  RERUN="$RERUN --zip $ZIP_PATH"
elif [[ $AGENT_GIVEN -eq 1 ]]; then
  RERUN="$RERUN --agent $AGENT"
fi

if [[ "$MODE" != "prebuild" && ! -f "$ZIP_PATH" ]]; then
  echo "ERROR: no dependency bundle at $ZIP_PATH" >&2
  found=""
  for z in "$AGENTS_DIR"/.bedrock_agentcore/*/dependencies.zip; do
    if [[ -f "$z" ]]; then
      found="$found $(basename "$(dirname "$z")")"
    fi
  done
  if [[ -n "$found" ]]; then
    echo "       Agents with a cached bundle:$found (use --agent NAME)" >&2
  fi
  echo "       To build one locally without deploying: $0 --prebuild --agent $AGENT" >&2
  exit 2
fi

# Python exit codes: 0 = nothing to fix, 10 = fixed or prebuilt, 1 = --check found
# work, 2 = error / unrecognised launcher / stale cache
set +e
python3 - "$MODE" "$ZIP_PATH" "$AGENTS_DIR" "$AGENT" "$CACHE_ZIP" "$RERUN" "$AGENT_GIVEN" <<'PY'
import hashlib
import logging
import os
import re
import shutil
import signal
import stat
import sys
import zipfile
from pathlib import Path

mode, zip_arg, agents_dir, agent, cache_zip_arg, rerun, agent_given = sys.argv[1:8]
script = rerun.split(" --")[0]
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

zip_path = os.path.realpath(zip_arg)
is_cache = zip_path == os.path.realpath(cache_zip_arg)
config_path = Path(agents_dir) / ".bedrock_agentcore.yaml"

WRAPPER = "#!/bin/sh\n'''exec' 'python3' \"$0\" \"$@\"\n' '''\n"
EXEC_LINE = re.compile(r"""^'''exec' (?P<interp>.+) "\$0" "\$@"\s*$""")
PLAIN_SHEBANG = re.compile(r"^#!\s*(?:/\S*/)?(?:env\s+)?python[0-9.]*\s*$")
PYTHON_SHEBANG = re.compile(r"^#!.*\bpython")
PYTHON_PATH = re.compile(r"/[^\s'\"]*python")


class BundleError(Exception):
    """A bundle this script can't build, check or repair safely; the zip is left as it was."""

    pass


def check(condition, message):
    """Stop the run with an error when a condition does not hold.

    Args:
        condition: The value that must be true.
        message: What went wrong, for the error line.

    Returns:
        None.

    Raises:
        BundleError: When the condition is false.
    """
    if not condition:
        raise BundleError(message)


def inspect_launcher(data):
    """Find out whether one bin/ file is a broken launcher, and make its repaired form.

    The forms it knows are the three in the header comment: uv's sh wrapper (form 1), and a plain
    shebang to a Python interpreter, direct or through /usr/bin/env (forms 2 and 3). A file that
    isn't text, has no shebang, or is an ordinary shell script is left alone.

    Args:
        data: The file's bytes, as read from the zip.

    Returns:
        A tuple (fixed_bytes, problem). fixed_bytes is the repaired file, or None when nothing
        changes (already repaired, or not a Python launcher). problem names a Python launcher form
        this script doesn't know, or is None.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    if text.startswith("\ufeff"):
        if PYTHON_SHEBANG.match(text[1:].split("\n", 1)[0]):
            return None, "byte-order mark before the shebang"
        return None, None
    if not text.startswith("#!"):
        return None, None
    lines = text.split("\n")
    first = lines[0].rstrip("\r")
    head = "\n".join(lines[:3])

    if first.strip() == "#!/bin/sh" and len(lines) > 1:
        match = EXEC_LINE.match(lines[1].rstrip("\r"))
        if match:
            if match.group("interp") == "'python3'":
                return None, None
            new = [first, "'''exec' 'python3' \"$0\" \"$@\""] + lines[2:]
            return "\n".join(new).encode("utf-8"), None
        if "'''exec'" in head:
            return None, "sh wrapper with an unknown exec line"
        if PYTHON_PATH.search(head):
            return None, "shell script that starts a Python by path"
        return None, None

    if "'''exec'" in head:
        return None, "exec wrapper without #!/bin/sh"
    if PYTHON_SHEBANG.match(first):
        if PLAIN_SHEBANG.match(first):
            return (WRAPPER + "\n".join(lines[1:])).encode("utf-8"), None
        return None, "Python shebang with extra arguments"
    return None, None


def launchers(zf):
    """List the launcher scripts of a bundle: the regular files directly in bin/.

    Args:
        zf: The open dependencies zip.

    Returns:
        A generator of the ZipInfo of each such file, in zip order; folders, files in subfolders
        of bin/ and symlinks are left out. Yields nothing when the zip has no bin/ files.
    """
    for info in zf.infolist():
        name = info.filename
        if (name.startswith("bin/") and not info.is_dir()
                and "/" not in name[len("bin/"):]
                and not stat.S_ISLNK(info.external_attr >> 16)):
            yield info


def toolkit():
    """Import the parts of the deploy tool (bedrock-agentcore-starter-toolkit) this script uses.

    Args:
        None.

    Returns:
        The tuple (config, entrypoint, package) of the toolkit modules, or None when the toolkit
        isn't installed, for example outside the venv.
    """
    try:
        from bedrock_agentcore_starter_toolkit.utils.runtime import config, entrypoint, package
    except ImportError:
        return None
    # The deploy tool sets its logger to INFO on import; keep its launch messages out of this output
    logging.getLogger("bedrock_agentcore_starter_toolkit").setLevel(logging.WARNING)
    return config, entrypoint, package


def agent_settings():
    """Find the agent's build settings as `agentcore launch` sees them.

    With the toolkit, they come from the toolkit's own reading of .bedrock_agentcore.yaml.
    Without it, requirements.txt (or pyproject.toml) is taken from agents/, runtime_type from the
    yaml, and the cache folder is the one that holds the zip.

    Args:
        None.

    Returns:
        A tuple (source_dir, cache_dir, requirements_file, runtime_type). requirements_file is None
        when agents/ has neither file; runtime_type is None when the yaml doesn't name one.
    """
    tk = toolkit()
    if tk:
        config, entrypoint, _ = tk
        os.chdir(agents_dir)                                 # launch runs from agents/
        agent_config = config.load_config(config_path, autofill_missing_aws=False).get_agent_config(agent)
        source_dir = Path(agent_config.source_path) if agent_config.source_path else config_path.parent
        cache_dir = config.get_agentcore_directory(config_path.parent, agent_config.name, agent_config.source_path)
        dep_info = entrypoint.detect_dependencies(source_dir)
        req = Path(dep_info.resolved_path) if dep_info.found else None
        return source_dir, cache_dir, req, agent_config.runtime_type
    text = config_path.read_text()
    block = re.search(r"^  %s:\s*$(.*?)(?=^  \S|\Z)" % re.escape(agent), text, re.M | re.S)
    runtime = re.search(r"^\s+runtime_type:\s*['\"]?([A-Za-z0-9_]+)", block.group(1) if block else "", re.M)
    req = next((p for p in (Path(agents_dir) / "requirements.txt", Path(agents_dir) / "pyproject.toml") if p.exists()), None)
    return Path(agents_dir), Path(zip_path).parent, req, runtime.group(1) if runtime else None


def cache_is_current():
    """Check whether the next `agentcore launch` would reuse the cached zip instead of rebuilding it.

    Args:
        None.

    Returns:
        A tuple (current, reason). current is True when the launch would reuse the zip, and reason
        is then empty. Otherwise current is False, and reason says why the launch would rebuild:
        no requirements file, a missing zip or hash, or a hash that doesn't match.
    """
    source_dir, cache_dir, req, runtime = agent_settings()
    if req is None:
        return False, "no requirements.txt or pyproject.toml found"
    lock = source_dir / "uv.lock"
    lock = lock if lock.exists() else None
    cache_zip, hash_file = Path(cache_dir) / "dependencies.zip", Path(cache_dir) / "dependencies.hash"
    tk = toolkit()
    if tk:
        current = not tk[2].PackageCache(Path(cache_dir)).should_rebuild_dependencies(req, lock, False, runtime)
    else:
        # Same rule as the deploy tool (0.3.4): zip and hash exist, and the hash is
        # sha256 of "sha256(requirements)[:sha256(uv.lock)][:runtime_type]"
        parts = [hashlib.sha256(req.read_bytes()).hexdigest()]
        if lock:
            parts.append(hashlib.sha256(lock.read_bytes()).hexdigest())
        if runtime:
            parts.append(runtime)
        expected = hashlib.sha256(":".join(parts).encode()).hexdigest()
        current = (cache_zip.exists() and hash_file.exists()
                   and hash_file.read_text().strip() == expected)
    if current:
        return True, ""
    if not cache_zip.exists():
        return False, "dependencies.zip is missing"
    if not hash_file.exists():
        return False, "dependencies.hash is missing"
    return False, "dependencies.hash does not match %s%s and %s" % (req.name, " + uv.lock" if lock else "", runtime)


def prebuild():
    """Build dependencies.zip exactly like `agentcore launch` would, without deploying.

    Args:
        None.

    Returns:
        True when it built a new bundle, False when the cached one already matches the current
        requirements. Writes the zip and dependencies.hash to the agent's cache folder.

    Raises:
        BundleError: When the toolkit or uv is missing, there is no requirements file, or the
            build produced no zip.
    """
    tk = toolkit()
    check(tk is not None, "--prebuild needs bedrock-agentcore-starter-toolkit (activate the venv)")
    check(shutil.which("uv") is not None, "--prebuild needs uv on PATH")
    _, _, package = tk
    source_dir, cache_dir, req, runtime = agent_settings()
    check(req is not None, "no requirements.txt or pyproject.toml found in " + str(source_dir))
    current, _ = cache_is_current()
    if current:
        print("Bundle is already built for the current requirements: " + zip_path)
        return False
    print("Building the dependency bundle locally (uv, Linux ARM64, %s) — about a minute ..." % runtime)
    deployment_zip, _ = package.CodeZipPackager().create_deployment_package(
        source_dir=source_dir, agent_name=agent, cache_dir=cache_dir,
        runtime_version=runtime, requirements_file=req, force_rebuild_deps=False)
    shutil.rmtree(deployment_zip.parent, ignore_errors=True)    # launch would upload this one; only the cached zip is kept
    check(Path(zip_path).exists(), "the build did not produce " + zip_path)
    print("Built: " + zip_path)
    return True


backup_path = zip_path + ".before-shebang-fix"
backup_tmp = backup_path + ".tmp"
tmp_path = zip_path + ".tmp-shebang-fix"
built = fixed = False
try:
    if mode == "prebuild":
        built = prebuild()

    stale_detail = ""
    if is_cache:
        current, stale_detail = cache_is_current()
        if not current:
            if mode == "check":
                print("STALE: the next 'agentcore launch' will rebuild %s (%s) and bring the broken launchers back."
                      % (zip_path, stale_detail))
            elif mode == "fix":
                print("ERROR: the next 'agentcore launch' will rebuild %s anyway (%s), so fixing it now would not last."
                      % (zip_path, stale_detail), file=sys.stderr)
                print("       Run: %s --prebuild%s" % (script, " --agent " + agent if agent_given == "1" else ""),
                      file=sys.stderr)
                sys.exit(2)
            else:
                raise BundleError("after the build the cache still does not match (%s)" % stale_detail)

    with zipfile.ZipFile(zip_path) as zin:
        fixes, unrecognised = {}, []
        for info in launchers(zin):
            new, problem = inspect_launcher(zin.read(info))
            if new is not None:
                fixes[info.filename] = new
            elif problem:
                unrecognised.append("%s (%s)" % (info.filename, problem))

        if unrecognised:
            print("ERROR: launcher(s) in a form this script does not know:", file=sys.stderr)
            for item in sorted(unrecognised):
                print("  " + item, file=sys.stderr)
            print("No launcher was changed. Fix them by hand: make each one start a bare 'python3', and keep dependencies.hash unchanged.", file=sys.stderr)
            if is_cache and not stale_detail:
                print("Do not launch before that: the next launch reuses this bundle as it is.", file=sys.stderr)
            sys.exit(2)

        names = sorted(fixes)
        if mode == "check":
            if names:
                print("NEEDS FIX: %d launcher script(s) in %s:" % (len(names), zip_path))
                for name in names:
                    print("  " + name)
            if names or stale_detail:
                hint = script + " --prebuild" + (" --agent " + agent if agent_given == "1" else "") \
                    if stale_detail else rerun
                print("Run %s to repair it." % hint)
                sys.exit(1)
            print("OK: no launcher in %s needs fixing%s." % (
                zip_path, "; the next launch reuses this bundle" if is_cache else ""))
            sys.exit(0)

        if not names:
            print("OK: no launcher in %s needs fixing." % zip_path)
        else:
            with zipfile.ZipFile(tmp_path, "w") as zout:
                zout.comment = zin.comment
                for info in zin.infolist():
                    data = fixes.get(info.filename)
                    if data is None:
                        data = zin.read(info)
                    zout.writestr(info, data, compress_type=info.compress_type)

    if mode != "check" and names:
        with zipfile.ZipFile(zip_path) as old, zipfile.ZipFile(tmp_path) as new:
            old_infos, new_infos = old.infolist(), new.infolist()
            check([i.filename for i in old_infos] == [i.filename for i in new_infos],
                  "the list of files changed")
            check(new.testzip() is None, "the new zip has a corrupt entry")
            for o, n in zip(old_infos, new_infos):
                check(o.external_attr == n.external_attr, "permissions changed: " + o.filename)
                if o.filename in fixes:
                    data = new.read(n)
                    check(data == fixes[o.filename], "fix not written: " + o.filename)
                    check(inspect_launcher(data) == (None, None), "still broken: " + o.filename)
                else:
                    check(o.CRC == n.CRC and o.file_size == n.file_size,
                          "content changed: " + o.filename)
        shutil.copy2(zip_path, backup_tmp)                   # no half-written backup if interrupted
        os.replace(backup_tmp, backup_path)
        os.replace(tmp_path, zip_path)
        fixed = True
        print("FIXED: %d launcher script(s) in %s:" % (len(names), zip_path))
        for name in names:
            print("  " + name)
        print("Backup of the original: " + backup_path)
except KeyboardInterrupt:
    if mode == "prebuild":
        print("Interrupted. Run %s --check before the next launch." % script, file=sys.stderr)
    else:
        print("Interrupted — the bundle was not changed.", file=sys.stderr)
    sys.exit(130)
except (BundleError, OSError, zipfile.BadZipFile, ValueError) as exc:
    print("ERROR: %s — %s" % (exc, "nothing was deployed" if mode == "prebuild" else "the bundle was not changed."),
          file=sys.stderr)
    sys.exit(2)
except Exception as exc:
    print("ERROR: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
    sys.exit(2)
finally:
    for leftover in (tmp_path, backup_tmp):
        if os.path.exists(leftover):
            os.remove(leftover)

if is_cache and (fixed or mode == "prebuild"):
    print()
    print("dependencies.hash matches the current requirements, so the next launch reuses this bundle.")
    print("Next, from agents/ (keeps the guardrail):")
    print("  agentcore launch --auto-update-on-conflict%s \\" % (" --agent " + agent if agent_given == "1" else ""))
    print("    --env LTTM_GUARDRAIL_ID=$(terraform -chdir=../terraform output -raw guardrail_id) \\")
    print("    --env LTTM_GUARDRAIL_VERSION=$(terraform -chdir=../terraform output -raw guardrail_version)")
    print("The launch must print: Using cached dependencies (no changes detected)")
sys.exit(10 if (built or fixed) else 0)
PY
RC=$?
set -e

[[ $RC -eq 10 ]] && RC=0
exit $RC
