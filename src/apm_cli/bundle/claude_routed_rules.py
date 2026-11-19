"""Claude rules a pre-#3939 install deployed, for ``apm pack``.

Claude routing (#3939) compiles always-on and directory instructions into
``AGENTS.md`` instead of deploying ``.claude/rules/`` files. A bundle ships
deployed files and its consumers do not compile, so the pack renders those
rules through the install projection (``InstructionIntegrator``) and both the
APM and plugin bundles keep the bytes a pre-#3939 pack shipped. Claude is
active when the lockfile records a Claude deployment, else when the producer's
install resolves it (``targets:`` in ``apm.yml``, else harness detection),
never from the pack target alone. Ambiguous detection skips the rules with a
warning instead of guessing.
"""

from __future__ import annotations

from pathlib import Path


def _packs_claude(effective_target: object) -> bool:
    targets = effective_target if isinstance(effective_target, list) else [effective_target]
    return any(str(name) in ("claude", "all") for name in targets)


def _recorded_claude(lockfile) -> bool:
    """Return whether the lockfile records any Claude deployment (recorded wins)."""
    from ..core.deployment_ledger import DeploymentLedgerCodec

    ledger = DeploymentLedgerCodec.from_lockfile(lockfile)
    if any(record.locator.target == "claude" for record in ledger.records.values()):
        return True
    return any(
        path.startswith(".claude/")
        for dep in lockfile.get_all_dependencies()
        for path in dep.deployed_files
    )


def _producer_deploys_claude(project_root: Path) -> bool | None:
    """Return whether the producer's install resolves Claude; ``None`` if ambiguous."""
    from ..core.errors import AmbiguousHarnessError, NoHarnessError
    from ..core.target_detection import resolve_targets
    from ..models.apm_package import APMPackage, package_target_declaration

    manifest = project_root / "apm.yml"
    declared = (
        package_target_declaration(APMPackage.from_apm_yml(manifest)).restriction()
        if manifest.is_file()
        else None
    )
    try:
        return "claude" in resolve_targets(project_root, yaml_targets=declared).targets
    except AmbiguousHarnessError:
        return None
    except NoHarnessError:
        return False


def routed_claude_rules(
    lockfile, project_root: Path, effective_target: object = None
) -> dict[str, dict[str, str]]:
    """Return ``{dependency key: {rule path: content}}`` the pack must add.

    *effective_target* is the APM-format pack filter; the plugin format,
    which ships every deployed file, passes ``None``.
    """
    if lockfile is None:
        return {}
    if effective_target is not None and not _packs_claude(effective_target):
        return {}
    active = True if _recorded_claude(lockfile) else _producer_deploys_claude(project_root)
    if active is False:
        return {}
    from ..install.package_resolution import effective_dependency_selection
    from ..integration.instruction_integrator import InstructionIntegrator
    from ..models.apm_package import APMPackage, PackageInfo

    modules = project_root / "apm_modules"
    rules: dict[str, dict[str, str]] = {}
    for dep in lockfile.get_all_dependencies():
        if dep.source == "local":
            continue
        dep_ref = dep.to_dependency_ref()
        subset = effective_dependency_selection(dep_ref).target_subset
        if subset is not None and "claude" not in subset:
            continue
        install_path = dep_ref.get_install_path(modules)
        if not install_path.is_dir():
            continue
        manifest = install_path / "apm.yml"
        package = (
            APMPackage.from_apm_yml(manifest)
            if manifest.is_file()
            else APMPackage(name=dep.name or install_path.name, version=dep.version or "0.0.0")
        )
        package_info = PackageInfo(
            package=package, install_path=install_path, dependency_ref=dep_ref
        )
        rendered = InstructionIntegrator().render_agents_md_routed_rules(package_info, project_root)
        deployed = set(dep.deployed_files)
        missing = {path: text for path, text in rendered.items() if path not in deployed}
        if missing:
            rules[dep.get_unique_key()] = missing
    if active is None and rules:
        from ..utils.console import _rich_warning

        _rich_warning(
            f"Skipped {sum(map(len, rules.values()))} Claude rule(s) routed to AGENTS.md: "
            "this project's targets are ambiguous, so pack cannot tell whether install "
            "used Claude. Declare 'targets:' in apm.yml and pack again.",
            symbol="warning",
        )
        return {}
    return rules


def write_routed_claude_rules(bundle_dir: Path, rendered: dict[str, str]) -> None:
    """Write rendered rules inside *bundle_dir* (LF, contained)."""
    from ..utils.atomic_io import write_text_lf

    bundle_root = bundle_dir.resolve()
    for rel_path, text in rendered.items():
        dest = bundle_dir / rel_path
        if not dest.resolve().is_relative_to(bundle_root):
            raise ValueError(f"Refusing to write outside bundle directory: {rel_path!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(dest, text)
