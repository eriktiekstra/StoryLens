#!/usr/bin/env python3
"""Local installed dependency metadata inventory, not legal clearance or an SPDX SBOM."""

import argparse
from collections import deque
import hashlib
import importlib.metadata as metadata
import json
from pathlib import PurePosixPath
import sys
from urllib.parse import urlsplit, urlunsplit

try:
    from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
except ImportError:
    sys.exit("License inventory requires packaging==26.3; install it explicitly in the inventory environment")


def public_url(value):
    """Exclude local files, credentials, query tokens and fragments from metadata URLs."""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        # Paths may contain embedded tokens; retain public origin only in this inventory.
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, "", "", ""))
    except ValueError:
        return None


def relative_reference(value):
    path = PurePosixPath(str(value))
    if path.is_absolute() or ".." in path.parts or "\\" in str(value) or ":" in str(value):
        return None
    return path.as_posix()


def license_details(distribution):
    info = distribution.metadata
    expression = info.get("License-Expression")
    unknowns = []
    normalized = None
    if expression:
        try:
            normalized = canonicalize_license_expression(expression)
        except InvalidLicenseExpression:
            unknowns.append("invalid_metadata_license_expression")
    legacy = info.get("License")
    if not normalized:
        unknowns.append("no_valid_spdx_license_expression_in_metadata")
    if not expression and not legacy:
        unknowns.append("no_license_metadata")
    if legacy and legacy.strip().lower() in {"unknown", "none", "n/a"}:
        unknowns.append("metadata_license_unspecified")
    files = {}
    listed = list(distribution.files or ())
    for item in listed:
        reference = relative_reference(item)
        if not reference:
            continue
        parts = PurePosixPath(reference).parts
        name = parts[-1].lower()
        if ("licenses" in parts or name.startswith(("license", "licence", "copying", "notice"))):
            file = distribution.locate_file(item)
            if file.is_file():
                files[reference] = {"reference": reference, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                                    "discovery": "installed_file_list"}
    declared = []
    for value in info.get_all("License-File", []):
        reference = relative_reference(value)
        declared.append(reference if reference else "[unsafe reference omitted]")
    if not files:
        unknowns.append("no_installed_license_file_identified")
    return {"spdx_expression_from_metadata": expression, "normalized_spdx_expression": normalized,
            "metadata_license": legacy,
            "license_classifiers_uninterpreted": [value for value in info.get_all("Classifier", []) if value.startswith("License ::")],
            "declared_license_files": declared, "installed_license_files": list(files.values()), "unknowns": unknowns}


def component(distribution, extras):
    info = distribution.metadata
    urls = []
    for value in info.get_all("Project-URL", []):
        label, separator, url = value.partition(",")
        origin = public_url(url if separator else label)
        if origin:
            urls.append({"label": label.strip() if separator else "project", "origin": origin})
    homepage = public_url(info.get("Home-page", ""))
    if homepage:
        urls.append({"label": "homepage", "origin": homepage})
    return {"name": info["Name"], "version": distribution.version,
            "purl": f"pkg:pypi/{canonicalize_name(info['Name'])}@{distribution.version}",
            "selected_extras": sorted(extras), "project_urls": urls,
            "license": license_details(distribution)}


def inventory(root, extras):
    environment = default_environment()
    pending = deque([(canonicalize_name(root), set(extras))])
    selected, distributions, processed, edges = {}, {}, {}, {}
    issues = []
    missing = set()
    while pending:
        name, requested = pending.popleft()
        selected.setdefault(name, set()).update(canonicalize_name(extra) for extra in requested)
        if name in processed and processed[name] == selected[name]:
            continue
        processed[name] = selected[name].copy()
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.add(name)
            continue
        distributions[name] = distribution
        supported = {canonicalize_name(extra) for extra in distribution.metadata.get_all("Provides-Extra", [])}
        for extra in sorted(selected[name] - supported):
            issue = {"package": name, "kind": "unknown_requested_extra", "extra": extra}
            if issue not in issues:
                issues.append(issue)
        contexts = {"", *selected[name]}
        for text in distribution.requires or []:
            try:
                requirement = Requirement(text)
            except InvalidRequirement:
                # Raw dependency metadata can contain private direct URLs: do not echo it.
                issues.append({"package": name, "kind": "invalid_requires_dist"})
                continue
            try:
                active = requirement.marker is None or any(requirement.marker.evaluate({**environment, "extra": extra}) for extra in contexts)
            except (ValueError, KeyError):
                issues.append({"package": name, "kind": "unevaluable_dependency_marker", "dependency": requirement.name})
                continue
            if not active:
                continue
            child = canonicalize_name(requirement.name)
            key = (name, child, str(requirement.specifier), tuple(sorted(requirement.extras)))
            edge = {"from": name, "to": child, "specifier": str(requirement.specifier),
                    "requested_extras": sorted(requirement.extras), "direct_url_dependency": requirement.url is not None}
            try:
                child_dist = metadata.distribution(child)
                if requirement.specifier and not requirement.specifier.contains(child_dist.version, prereleases=True):
                    issue = {"package": name, "dependency": child, "kind": "installed_version_mismatch",
                             "installed_version": child_dist.version, "specifier": str(requirement.specifier)}
                    if issue not in issues:
                        issues.append(issue)
            except metadata.PackageNotFoundError:
                missing.add(child)
            edges[key] = edge
            pending.append((child, set(requirement.extras)))
    for name in sorted(missing):
        issues.append({"package": name, "kind": "missing_active_dependency"})
    components = [component(distributions[name], selected[name]) for name in sorted(distributions)]
    return {"schema_version": "1.0", "format": "storylens-installed-license-inventory",
            "root": canonicalize_name(root), "requested_extras": sorted(extras),
            "scope": "installed active dependency closure on this interpreter/platform, including selected transitive extras",
            "marker_environment": environment,
            "components": components, "dependencies": sorted(edges.values(), key=lambda edge: (edge["from"], edge["to"], edge["specifier"], edge["requested_extras"])),
            "issues": issues, "closure_complete": not issues,
            "privacy": {"absolute_paths": False, "direct_install_urls": False, "project_urls": "public origins only; userinfo/path/query/fragment omitted"},
            "limitations": ["License data are upstream metadata assertions, not legal conclusions or classifier-based classifications.",
                            "License file discovery is heuristic; bundled native components and notices require artifact inspection.",
                            "Not a full optional ecosystem inventory, dependency lock, standardized SPDX/CycloneDX SBOM, or redistribution clearance.",
                            "External FFmpeg, drivers, models, weights, remote service terms, patents and uninstalled extras are not inventoried."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="storylens", help="Installed root distribution (default: storylens)")
    parser.add_argument("--extra", action="append", default=[], help="Explicit active root extra; repeat for several extras")
    parser.add_argument("--strict", action="store_true", help="Also fail if any package has unknown license evidence")
    args = parser.parse_args()
    report = inventory(args.root, args.extra)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["issues"] or (args.strict and any(item["license"]["unknowns"] for item in report["components"])):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        # Exception messages can include local install paths; do not leak them in inventory logs.
        print(f"License inventory failed ({type(error).__name__}); inspect installed metadata locally", file=sys.stderr)
        sys.exit(1)
