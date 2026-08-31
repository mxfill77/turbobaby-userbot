"""H1 — HQ context pack builder (code-green, pure, fixture-only).

A deterministic builder that assembles a minimal HQ *context pack* out of an
explicit allowlisted manifest of text project/task artifacts.

Design invariants (see docs/tasks/h1-hq-context-pack-code-green.md):

* Pure: no network, no subprocess, no environment mutation, no writes, no glob
  and no automatic repo/Brain scan. The only I/O is UTF-8 *reads* of the files
  that the caller named explicitly in ``source_manifest`` and that resolve
  inside ``root``.
* Fail-closed: a missing/unreadable *required* source yields a structured
  ``blocked`` outcome with no partial body; absence is never synthesised into a
  success or an "absent" verdict — it is recorded as an explicit ``unknown``.
* Reproducible: identical inputs return identical serialisable output. Hashes
  (manifest, per-source content, per-source excerpt, whole pack) are the
  evidence of source version; no timestamp, host or absolute root leaks into the
  canonical content.

Structural / security violations of the manifest raise :class:`ContextPackError`
(the whole call is invalid). Evidence-state fail-closed conditions
(missing required source, size cap) *return* a ``blocked`` dict.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

SCHEMA = "turbobaby.hq_context_pack/v1"

TASK_CLASSES = ("status", "read", "code_green", "architecture", "business_red", "owner_red")
SOURCE_LANES = ("pc", "vps", "hq", "shared", "none")
EVIDENCE_STATUSES = ("reported", "verified", "released", "blocked", "unknown")
ROUTE_TARGETS = ("pc", "vps", "hq", "none")
SOURCE_ROLES = ("objective", "evidence", "context", "constraint", "reference")

OMISSION_REASONS = ("missing", "unreadable", "context_limit")

OBJECTIVE_MAX_CHARS = 1000

# Hard denylist: any path *segment* that contains one of these tokens is denied,
# no matter how the caller spells the manifest. Note it is ``logs`` (not ``log``)
# to match the contract literally.
DENY_TOKENS = (".env", "secret", "token", "session", "cookie", "credentials", "logs")

_SEP_RE = re.compile(r"[\\/]+")


class ContextPackError(ValueError):
    """A manifest that is structurally or security-invalid. The whole call fails.

    ``reason`` is a stable machine-readable slug; ``detail`` carries context.
    """

    def __init__(self, reason, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _canonical(obj):
    """Deterministic JSON text used as the pre-image for every hash."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _segments(path):
    return [seg for seg in _SEP_RE.split(path) if seg not in ("", ".")]


def _norm_rel(path):
    """Normalise a manifest path to a comparable POSIX-style relative string.

    Drops ``.`` segments and unifies separators so ``a.txt`` and ``./a.txt``
    compare equal for duplicate detection.
    """
    return "/".join(_segments(path))


def _is_denylisted(path):
    lowered = path.lower()
    for seg in _SEP_RE.split(lowered):
        for token in DENY_TOKENS:
            if token in seg:
                return token
    return None


def _is_traversal_or_absolute(path):
    if not path or not path.strip():
        return "empty_path"
    if os.path.isabs(path):
        return "absolute_path"
    # Windows drive / UNC prefixes that os.path.isabs may miss on POSIX hosts.
    if re.match(r"^[A-Za-z]:", path) or path.startswith("\\\\") or path.startswith("//"):
        return "absolute_path"
    for seg in _SEP_RE.split(path):
        if seg == "..":
            return "path_traversal"
    return None


def _resolve_within_root(root, path):
    root_abs = os.path.abspath(root)
    full = os.path.normpath(os.path.join(root_abs, path))
    try:
        common = os.path.commonpath([root_abs, full])
    except ValueError:
        return None
    if common != root_abs:
        return None
    return full


def _validate_scalar(record, key, allowed, source_index):
    value = record.get(key)
    if value not in allowed:
        raise ContextPackError(
            "invalid_%s" % key,
            "source[%d] %s=%r not in %r" % (source_index, key, value, list(allowed)),
        )
    return value


def _validate_objective(active_objective):
    if not isinstance(active_objective, str):
        raise ContextPackError("invalid_objective", "active_objective must be a str")
    stripped = active_objective.strip()
    if not stripped:
        raise ContextPackError("empty_objective", "active_objective must be non-empty")
    if len(active_objective) > OBJECTIVE_MAX_CHARS:
        raise ContextPackError(
            "oversized_objective",
            "active_objective length %d exceeds %d" % (len(active_objective), OBJECTIVE_MAX_CHARS),
        )
    return active_objective


def _validate_route(route):
    if route is None:
        return None
    if not isinstance(route, dict):
        # A free-text route (str) or any non-structured value is rejected: routing
        # is structured metadata, never parsed from prompt text.
        raise ContextPackError("invalid_route", "route must be structured metadata (dict), not %r" % type(route).__name__)
    target = route.get("target")
    if target not in ROUTE_TARGETS:
        raise ContextPackError("invalid_route_target", "route target %r not in %r" % (target, list(ROUTE_TARGETS)))
    # Preserve the structured route verbatim (sorted for determinism), separate
    # from the body. No effort/routing convention is inferred here.
    return {k: route[k] for k in sorted(route)}


def _validate_coverage_plan(coverage_plan):
    if coverage_plan is None:
        return None
    if not isinstance(coverage_plan, (list, tuple)):
        raise ContextPackError("invalid_coverage_plan", "coverage_plan must be a list of lanes")
    lanes = []
    for lane in coverage_plan:
        if lane not in SOURCE_LANES:
            raise ContextPackError("invalid_coverage_lane", "coverage lane %r not in %r" % (lane, list(SOURCE_LANES)))
        if lane not in lanes:
            lanes.append(lane)
    return lanes


def _validate_manifest(source_manifest, root):
    """Validate structure/security of every record. Returns validated records.

    Raises :class:`ContextPackError` on any structural or security violation.
    Denylisted sources are *not* raised here — they are returned flagged so the
    builder can record them in ``excluded`` and keep them out of the body.
    """
    if not isinstance(source_manifest, (list, tuple)):
        raise ContextPackError("invalid_manifest", "source_manifest must be a list")

    validated = []
    seen_paths = set()
    for i, record in enumerate(source_manifest):
        if not isinstance(record, dict):
            raise ContextPackError("invalid_source_record", "source[%d] must be a dict" % i)

        path = record.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ContextPackError("invalid_path", "source[%d] path must be a non-empty str" % i)

        bad = _is_traversal_or_absolute(path)
        if bad:
            raise ContextPackError(bad, "source[%d] path=%r" % (i, path))

        norm = _norm_rel(path)
        if norm in seen_paths:
            raise ContextPackError("duplicate_path", "source[%d] path=%r duplicates an earlier entry" % (i, path))
        seen_paths.add(norm)

        role = _validate_scalar(record, "role", SOURCE_ROLES, i)
        lane = _validate_scalar(record, "lane", SOURCE_LANES, i)
        evidence_status = _validate_scalar(record, "evidence_status", EVIDENCE_STATUSES, i)

        required = record.get("required")
        if not isinstance(required, bool):
            raise ContextPackError("invalid_required", "source[%d] required must be a bool" % i)

        start_line = record.get("start_line")
        end_line = record.get("end_line")
        if not isinstance(start_line, int) or isinstance(start_line, bool):
            raise ContextPackError("invalid_line_range", "source[%d] start_line must be an int" % i)
        if not isinstance(end_line, int) or isinstance(end_line, bool):
            raise ContextPackError("invalid_line_range", "source[%d] end_line must be an int" % i)
        if start_line < 1 or end_line < start_line:
            raise ContextPackError(
                "invalid_line_range",
                "source[%d] inclusive range %d-%d is not a positive ordered range" % (i, start_line, end_line),
            )

        deny_token = _is_denylisted(path)

        full = _resolve_within_root(root, path)
        if full is None:
            raise ContextPackError("outside_root", "source[%d] path=%r resolves outside root" % (i, path))

        validated.append(
            {
                "index": i,
                "path": norm,
                "role": role,
                "lane": lane,
                "evidence_status": evidence_status,
                "required": required,
                "start_line": start_line,
                "end_line": end_line,
                "full_path": full,
                "deny_token": deny_token,
            }
        )
    return validated


def _manifest_evidence(validated):
    """Canonical manifest hash pre-image (order-sensitive, path/role/range/etc)."""
    canonical_records = [
        {
            "path": v["path"],
            "role": v["role"],
            "lane": v["lane"],
            "evidence_status": v["evidence_status"],
            "required": v["required"],
            "start_line": v["start_line"],
            "end_line": v["end_line"],
        }
        for v in validated
    ]
    return _sha256_text(_canonical(canonical_records)), canonical_records


def _read_source(v):
    """Read one source. Returns (content, lines) or raises an OSError family error."""
    with open(v["full_path"], "r", encoding="utf-8") as fh:
        content = fh.read()
    return content


def _blocked(reason, case_id, task_class, detail, extra=None):
    pack = {
        "schema": SCHEMA,
        "status": "blocked",
        "reason": reason,
        "case_id": case_id,
        "task_class": task_class,
        "detail": detail,
    }
    if extra:
        pack.update(extra)
    return _finalize(pack)


def _finalize(pack):
    """Attach the reproducible whole-pack sha256 (over content minus sha256)."""
    content = {k: pack[k] for k in pack if k != "sha256"}
    pack["sha256"] = _sha256_text(_canonical(content))
    return pack


def build_context_pack(
    case_id,
    task_class,
    active_objective,
    source_manifest,
    *,
    root,
    coverage_plan=None,
    route=None,
    max_chars=12000,
):
    """Build a minimal, deterministic HQ context pack. See module docstring."""

    if task_class not in TASK_CLASSES:
        # Never silently normalise an unknown class.
        raise ContextPackError("invalid_task_class", "%r not in %r" % (task_class, list(TASK_CLASSES)))

    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise ContextPackError("invalid_max_chars", "max_chars must be a positive int, got %r" % (max_chars,))

    active_objective = _validate_objective(active_objective)
    route_out = _validate_route(route)
    coverage_lanes = _validate_coverage_plan(coverage_plan)

    if not isinstance(root, str) or not root:
        raise ContextPackError("invalid_root", "root must be a non-empty str path")

    validated = _validate_manifest(source_manifest, root)
    manifest_sha256, _canonical_records = _manifest_evidence(validated)

    excluded = []
    omitted = []
    accepted = []  # sources that were read successfully and are candidates for the body

    for v in validated:
        if v["deny_token"] is not None:
            # Hard-deny: recorded as an explicit denied input; never read, never
            # in body, never in sources. A caller cannot bypass by naming it.
            excluded.append(
                {
                    "path": v["path"],
                    "role": v["role"],
                    "lane": v["lane"],
                    "reason": "denylisted",
                    "matched_token": v["deny_token"],
                }
            )
            continue

        try:
            content = _read_source(v)
        except FileNotFoundError:
            if v["required"]:
                return _blocked(
                    "missing_required_source",
                    case_id,
                    task_class,
                    "required source %r is missing" % v["path"],
                    extra={"manifest_sha256": manifest_sha256, "source": v["path"], "lane": v["lane"], "role": v["role"]},
                )
            omitted.append({"path": v["path"], "role": v["role"], "lane": v["lane"], "reason": "missing"})
            continue
        except (UnicodeDecodeError, OSError):
            if v["required"]:
                return _blocked(
                    "missing_required_source",
                    case_id,
                    task_class,
                    "required source %r is unreadable" % v["path"],
                    extra={"manifest_sha256": manifest_sha256, "source": v["path"], "lane": v["lane"], "role": v["role"]},
                )
            omitted.append({"path": v["path"], "role": v["role"], "lane": v["lane"], "reason": "unreadable"})
            continue

        lines = content.splitlines(keepends=True)
        if v["end_line"] > len(lines):
            # An excerpt range beyond the file is a malformed manifest, not an
            # omission — fail closed rather than silently shrink the range.
            raise ContextPackError(
                "line_range_out_of_bounds",
                "source %r range %d-%d exceeds %d line(s)" % (v["path"], v["start_line"], v["end_line"], len(lines)),
            )

        excerpt = "".join(lines[v["start_line"] - 1 : v["end_line"]])
        accepted.append(
            {
                "path": v["path"],
                "role": v["role"],
                "lane": v["lane"],
                "evidence_status": v["evidence_status"],
                "required": v["required"],
                "start_line": v["start_line"],
                "end_line": v["end_line"],
                "source_sha256": _sha256_text(content),
                "excerpt_sha256": _sha256_text(excerpt),
                "excerpt_chars": len(excerpt),
                "_excerpt": excerpt,
            }
        )

    # ---- Deterministic size enforcement over the assembled body ----
    def _block_for(src):
        return (
            "--- source: %s | role=%s | lane=%s | evidence=%s | lines=%d-%d ---\n%s"
            % (
                src["path"],
                src["role"],
                src["lane"],
                src["evidence_status"],
                src["start_line"],
                src["end_line"],
                src["_excerpt"] if src["_excerpt"].endswith("\n") or src["_excerpt"] == "" else src["_excerpt"] + "\n",
            )
        )

    required_sources = [s for s in accepted if s["required"]]
    optional_sources = [s for s in accepted if not s["required"]]

    body_sources = []
    body_parts = []

    for src in required_sources:
        body_parts.append(_block_for(src))
        body_sources.append(src)

    required_body = "\n".join(body_parts)
    if len(required_body) > max_chars:
        return _blocked(
            "context_limit_exceeded",
            case_id,
            task_class,
            "required excerpts need %d chars, exceeding max_chars=%d" % (len(required_body), max_chars),
            extra={"manifest_sha256": manifest_sha256, "required_chars": len(required_body), "max_chars": max_chars},
        )

    for src in optional_sources:
        candidate = list(body_parts) + [_block_for(src)]
        if len("\n".join(candidate)) > max_chars:
            omitted.append({"path": src["path"], "role": src["role"], "lane": src["lane"], "reason": "context_limit"})
            continue
        body_parts.append(_block_for(src))
        body_sources.append(src)

    body = "\n".join(body_parts)

    accepted_by_order = body_sources
    accepted_lanes = {s["lane"] for s in accepted}

    # ---- Coverage & unknowns ----
    coverage = None
    unknowns = []
    if coverage_lanes is not None:
        coverage = []
        omitted_lanes = {o["lane"] for o in omitted}
        for lane in coverage_lanes:
            if lane in accepted_lanes:
                coverage.append({"lane": lane, "checked": True, "status": "checked", "reason": "accepted source present in lane"})
            else:
                if lane in omitted_lanes:
                    reason = "declared source in lane was omitted; evidence unresolved (unknown)"
                else:
                    reason = "no accepted source in lane; evidence unresolved (unknown)"
                coverage.append({"lane": lane, "checked": False, "status": "not_checked", "reason": reason})
                # Every absence is an explicit unknown/blind-spot, never an
                # "absent" verdict and never a synthesised "no traces".
                unknowns.append(
                    {
                        "source": lane,
                        "check": "lane_coverage",
                        "snapshot_ref": manifest_sha256,
                        "blind_spot": "lane %r not checked: %s" % (lane, reason),
                    }
                )

    sources_out = [
        {
            "path": s["path"],
            "role": s["role"],
            "lane": s["lane"],
            "evidence_status": s["evidence_status"],
            "required": s["required"],
            "start_line": s["start_line"],
            "end_line": s["end_line"],
            "source_sha256": s["source_sha256"],
            "excerpt_sha256": s["excerpt_sha256"],
            "excerpt_chars": s["excerpt_chars"],
        }
        for s in accepted_by_order
    ]

    pack = {
        "schema": SCHEMA,
        "status": "ok",
        "case_id": case_id,
        "task_class": task_class,
        "active_objective": active_objective,
        "route": route_out,
        "manifest_sha256": manifest_sha256,
        "max_chars": max_chars,
        "body_chars": len(body),
        "sources": sources_out,
        "body": body,
        "omitted": omitted,
        "excluded": excluded,
        "unknowns": unknowns,
    }
    if coverage is not None:
        pack["coverage"] = coverage

    return _finalize(pack)
