from __future__ import annotations

import argparse
import json
import logging
import shlex
from dataclasses import replace
from pathlib import Path

from depviz.api import (
    BackendIdentity,
    BackendPlugin,
    DependencyIntent,
    EnvironmentState,
    EnvironmentTarget,
    OperationContext,
    PolicyFinding,
    Resolution,
    Severity,
    Target,
    VerificationPolicy,
)
from depviz.api.errors import (
    ApplyFailed,
    BackendError,
    InspectionFailed,
    LockFailed,
    PlanningFailed,
    PluginError,
    PromotionFailed,
    ResolutionFailed,
    RollbackFailed,
    ToolUnavailable,
    VerificationFailed,
)
from depviz.builtin.manifests.common import ParseResult
from depviz.infrastructure.deployment import ManagedDeploymentStore
from depviz.infrastructure.storage import read_bytes_limited
from depviz.infrastructure.inspection_cache import (
    default_cache_path,
    load_inspection_cache,
    save_inspection_cache,
)
from depviz.cli.exit_codes import ExitCode
from depviz.cli.rendering import (
    print_apply_result,
    print_blast_radius,
    print_dependency_weight,
    print_deployment_status,
    print_deps,
    print_diagnostics,
    print_impact,
    print_inspection_status,
    print_plan_summary,
    print_plugins,
    print_promotion,
    print_resolution_summary,
    print_rollback,
    print_summary,
    print_tree,
    print_verification_report,
    print_why,
)
from depviz.cli.services import ApplicationServices
from depviz.core.application import apply_locked_environment
from depviz.core.doctor import run_doctor
from depviz.core.garbage_collection import collect_candidates
from depviz.core.inspection import inspect_dependency_graph
from depviz.core.locking import create_lock, read_lock, write_lock
from depviz.core.planning import build_change_plan, plan_to_json, write_plan_json
from depviz.core.promotion import deployment_status, promote_candidate, rollback_deployment
from depviz.core.resolution import (
    host_conda_platform,
    read_resolution_json,
    resolution_to_json,
    resolve_intent,
    write_resolution_json,
)
from depviz.core.verification import verify_candidate_environment
from depviz.models import GraphInspection, InspectionStatus

logger = logging.getLogger(__name__)


def run_command(args: argparse.Namespace, services: ApplicationServices) -> int:
    if args.list_plugins:
        print_plugins(services.registry.plugins())
        return ExitCode.OK
    if args.command is None:
        logger.error(
            "Choose a command: inspect, resolve, plan, lock, apply, verify, promote, "
            "rollback, or status."
        )
        return ExitCode.INVALID_INPUT
    if args.command == "inspect":
        return run_inspect(args, services)
    if args.command == "resolve":
        return run_resolve(args, services)
    if args.command == "plan":
        return run_plan(args, services)
    if args.command == "lock":
        return run_lock(args, services)
    if args.command == "apply":
        return run_apply(args, services)
    if args.command == "verify":
        return run_verify(args, services)
    if args.command == "promote":
        return run_promote(args, services)
    if args.command == "rollback":
        return run_rollback(args, services)
    if args.command == "status":
        return run_status(args)
    if args.command == "doctor":
        return run_doctor_command(args, services)
    if args.command == "gc":
        return run_gc(args)
    logger.error("Unknown command: %s", args.command)
    return ExitCode.INVALID_INPUT


def run_inspect(args: argparse.Namespace, services: ApplicationServices) -> int:
    if args.from_cache and args.refresh_cache:
        logger.error("--from-cache and --refresh-cache cannot be used together.")
        return ExitCode.INVALID_INPUT
    if args.no_cache and args.from_cache:
        logger.error("--no-cache and --from-cache cannot be used together.")
        return ExitCode.INVALID_INPUT
    if args.depth < 0 or args.workers < 1:
        logger.error("--depth must be non-negative and --workers must be positive.")
        return ExitCode.INVALID_INPUT

    path = Path(args.manifest)
    if not path.exists():
        logger.error("File not found: %s", path)
        return ExitCode.INVALID_INPUT
    try:
        loader = services.registry.find_manifest_loader(path)
        intent = loader.load(path, OperationContext(working_directory=path.parent))
    except (PluginError, BackendError) as error:
        logger.error(str(error))
        return ExitCode.UNSUPPORTED_MANIFEST

    parse_result = ParseResult(intent)
    if intent.has_errors:
        print_diagnostics(intent.diagnostics)
        return ExitCode.UNSUPPORTED_MANIFEST
    if not intent.requirements:
        logger.error("No package requirements found in %s", path)
        return ExitCode.INVALID_INPUT

    cache_path = Path(args.cache_file) if args.cache_file else default_cache_path(path, args.depth)
    inspection: GraphInspection
    if args.from_cache or (not args.no_cache and not args.refresh_cache and cache_path.exists()):
        try:
            inspection = load_inspection_cache(cache_path)
        except (OSError, ValueError) as error:
            logger.error("Cannot load inspection cache: %s", error)
            return ExitCode.INSPECTION_FAILED
    else:
        inspection = inspect_dependency_graph(
            parse_result=parse_result,
            max_workers=args.workers,
            max_depth=args.depth,
        )
        if not args.no_cache:
            save_inspection_cache(inspection, parse_result, cache_path)

    print_summary(inspection.graph)
    print_inspection_status(inspection)
    print_diagnostics(inspection.diagnostics)
    if args.report in {"why", "impact", "deps", "tree"} and not args.package:
        logger.error("--package is required for report '%s'", args.report)
        return ExitCode.INVALID_INPUT
    if args.report in {"all", "blast"}:
        print_blast_radius(inspection.graph, args.limit)
    if args.report in {"all", "weight"}:
        print_dependency_weight(inspection.graph, args.limit)
    if args.report == "why":
        print_why(inspection.graph, args.package, args.limit)
    if args.report == "impact":
        print_impact(inspection.graph, args.package, args.limit)
    if args.report == "deps":
        print_deps(inspection.graph, args.package, args.limit)
    if args.report == "tree":
        print_tree(inspection.graph, args.package, args.depth)

    has_error = any(item.severity is Severity.ERROR for item in inspection.diagnostics)
    if args.require_complete and (
        inspection.status is InspectionStatus.INCOMPLETE or not inspection.complete or has_error
    ):
        return ExitCode.INCOMPLETE
    return ExitCode.OK


def run_resolve(args: argparse.Namespace, services: ApplicationServices) -> int:
    path = Path(args.manifest)
    validation = _validate_solver_args(path, args)
    if validation is not None:
        logger.error(validation)
        return ExitCode.INVALID_INPUT
    try:
        context = _solver_context(path, args, services)
        intent = _load_intent(path, services, context)
        resolver_name = _select_resolver_name(args.resolver, intent)
        plugin, resolver = services.registry.find_resolver_entry(resolver_name)
        resolution = resolve_intent(
            intent=intent,
            resolver=resolver,
            target=_initial_target(resolver_name, args.platform),
            current=None,
            context=context,
        )
        resolution = replace(
            resolution,
            backend=_backend_identity(plugin, resolver.name, resolution),
        )
    except RuntimeError as error:
        logger.error("%s; provide --platform explicitly.", error)
        return ExitCode.INVALID_INPUT
    except ToolUnavailable as error:
        _print_backend_error(error)
        return ExitCode.TOOL_UNAVAILABLE
    except (ResolutionFailed, PluginError, BackendError) as error:
        _print_backend_error(error) if isinstance(error, BackendError) else logger.error(str(error))
        return ExitCode.RESOLUTION_FAILED

    if args.output:
        try:
            write_resolution_json(Path(args.output), resolution)
        except OSError as error:
            logger.error("Cannot write resolution: %s", error)
            return ExitCode.INVALID_INPUT
    if args.json:
        print(resolution_to_json(resolution), end="")
    else:
        print_resolution_summary(resolution, args.limit)
    return ExitCode.OK


def run_plan(args: argparse.Namespace, services: ApplicationServices) -> int:
    manifest = Path(args.manifest)
    validation = _validate_solver_args(manifest, args)
    if validation is not None:
        logger.error(validation)
        return ExitCode.INVALID_INPUT
    if args.limit < 0:
        logger.error("--limit cannot be negative.")
        return ExitCode.INVALID_INPUT

    try:
        context = _solver_context(manifest, args, services)
        intent = _load_intent(manifest, services, context)
        resolver_name = _select_resolver_name(args.resolver, intent)
        resolver_plugin, resolver = services.registry.find_resolver_entry(resolver_name)

        if args.empty:
            desired = resolve_intent(
                intent=intent,
                resolver=resolver,
                target=_initial_target(resolver_name, args.platform),
                current=None,
                context=context,
            )
            current = EnvironmentState(packages=(), target=desired.target, complete=True)
        else:
            inspector_name = _select_inspector_name(args.inspector, resolver_name)
            inspector_plugin, inspector = services.registry.find_inspector_entry(inspector_name)
            inspector_context = context
            if args.platform:
                inspector_context = replace(
                    context,
                    configuration={**context.configuration, "conda.platform": args.platform},
                )
            current = inspector.inspect(
                EnvironmentTarget(path=Path(args.prefix), kind=inspector.name),
                inspector_context,
            )
            current = replace(
                current,
                backend=BackendIdentity(
                    component=inspector.name,
                    plugin=inspector_plugin.name,
                    plugin_version=inspector_plugin.plugin_version,
                    tool=_native_tool(current),
                    tool_version=_native_tool_version(current),
                ),
            )
            desired = resolve_intent(
                intent=intent,
                resolver=resolver,
                target=current.target,
                current=current,
                context=context,
            )

        desired = replace(
            desired,
            backend=_backend_identity(resolver_plugin, resolver.name, desired),
        )
        plan = build_change_plan(manifest=manifest, current=current, desired=desired)
    except RuntimeError as error:
        logger.error("%s; provide --platform explicitly.", error)
        return ExitCode.INVALID_INPUT
    except ToolUnavailable as error:
        _print_backend_error(error)
        return ExitCode.TOOL_UNAVAILABLE
    except InspectionFailed as error:
        _print_backend_error(error)
        return ExitCode.INSPECTION_FAILED
    except PlanningFailed as error:
        _print_backend_error(error)
        return ExitCode.PLAN_REJECTED
    except (ResolutionFailed, PluginError, BackendError) as error:
        _print_backend_error(error) if isinstance(error, BackendError) else logger.error(str(error))
        return ExitCode.RESOLUTION_FAILED

    if args.output:
        try:
            write_plan_json(Path(args.output), plan)
        except OSError as error:
            logger.error("Cannot write plan: %s", error)
            return ExitCode.INVALID_INPUT
    if args.json:
        print(plan_to_json(plan), end="")
    else:
        print_plan_summary(plan, args.limit)
    if _policy_rejected(plan.policy_findings, args.fail_on_policy):
        return ExitCode.PLAN_REJECTED
    return ExitCode.OK


def run_lock(args: argparse.Namespace, services: ApplicationServices) -> int:
    resolution_path = Path(args.resolution)
    if not resolution_path.exists():
        logger.error("Resolution file not found: %s", resolution_path)
        return ExitCode.INVALID_INPUT
    try:
        resolution = read_resolution_json(resolution_path)
        provider_name = _select_provider_for_resolution(args.provider, resolution)
        _plugin, provider = services.registry.find_lock_provider_entry(provider_name)
        lock_configuration: dict[str, str] = {}
        if args.allow_weak_checksum:
            lock_configuration["security.allow_weak_checksums"] = "true"
        artifact = create_lock(
            resolution=resolution,
            provider=provider,
            context=OperationContext(
                command_runner=services.command_runner,
                configuration=lock_configuration,
            ),
        )
        write_lock(Path(args.output), artifact)
    except ValueError as error:
        logger.error(str(error))
        return ExitCode.INVALID_INPUT
    except LockFailed as error:
        _print_backend_error(error)
        return ExitCode.LOCK_FAILED
    except (PluginError, BackendError) as error:
        logger.error(str(error))
        return ExitCode.LOCK_FAILED
    if args.json:
        print(artifact.content.decode("utf-8"), end="")
    else:
        print(f"Wrote {artifact.format}: {args.output}")
        if "lock_id" in artifact.metadata:
            print(f"Lock ID: {artifact.metadata['lock_id']}")
    return ExitCode.OK


def run_apply(args: argparse.Namespace, services: ApplicationServices) -> int:
    lock_path = Path(args.lock)
    validation = _validate_runtime_args(lock_path, args)
    if validation is not None:
        logger.error(validation)
        return ExitCode.INVALID_INPUT
    try:
        provider_name = _select_provider_for_lock(args.provider, lock_path)
        driver_name = _select_driver_name(args.driver, provider_name)
        _plugin, provider = services.registry.find_lock_provider_entry(provider_name)
        _driver_plugin, driver = services.registry.find_environment_driver_entry(driver_name)
        deployment = EnvironmentTarget(Path(args.deployment), driver.deployment_kind)
        runtime_context = _runtime_context(args, services, working_directory=lock_path.parent)
        locked = read_lock(
            path=lock_path,
            provider=provider,
            context=runtime_context,
        )
        result = apply_locked_environment(
            lock=locked,
            driver=driver,
            deployment=deployment,
            context=runtime_context,
            keep_failed=args.keep_failed,
            lock_timeout_seconds=args.lock_timeout,
        )
    except ToolUnavailable as error:
        _print_backend_error(error)
        return ExitCode.TOOL_UNAVAILABLE
    except LockFailed as error:
        _print_backend_error(error)
        return ExitCode.LOCK_FAILED
    except ApplyFailed as error:
        _print_backend_error(error)
        return ExitCode.APPLY_FAILED
    except (PluginError, BackendError, ValueError) as error:
        logger.error(str(error))
        return ExitCode.APPLY_FAILED

    if result.candidate is None or result.lock_id is None:
        logger.error("Apply completed without candidate or lock identity.")
        return ExitCode.APPLY_FAILED
    if args.json:
        print(
            json.dumps(
                {
                    "candidate_id": result.candidate.candidate_id,
                    "candidate_path": str(result.candidate.path),
                    "deployment": str(deployment.path),
                    "deployment_kind": deployment.kind,
                    "environment_kind": result.candidate.kind,
                    "lock_id": result.lock_id,
                    "changed": result.changed,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_apply_result(result)
    return ExitCode.OK


def run_verify(args: argparse.Namespace, services: ApplicationServices) -> int:
    lock_path = Path(args.lock)
    validation = _validate_runtime_args(lock_path, args)
    if validation is not None:
        logger.error(validation)
        return ExitCode.INVALID_INPUT
    try:
        commands = tuple(_parse_probe_command(item) for item in args.probe_command)
    except ValueError as error:
        logger.error(str(error))
        return ExitCode.INVALID_INPUT
    try:
        provider_name = _select_provider_for_lock(args.provider, lock_path)
        verifier_name = _select_verifier_name(args.verifier, provider_name)
        _provider_plugin, provider = services.registry.find_lock_provider_entry(provider_name)
        _verifier_plugin, verifier = services.registry.find_verifier_entry(verifier_name)
        deployment = EnvironmentTarget(Path(args.deployment), verifier.deployment_kind)
        runtime_context = _runtime_context(args, services, working_directory=lock_path.parent)
        locked = read_lock(
            path=lock_path,
            provider=provider,
            context=runtime_context,
        )
        report = verify_candidate_environment(
            lock=locked,
            verifier=verifier,
            deployment=deployment,
            candidate_id=args.candidate,
            policy=VerificationPolicy(
                load_packages=tuple(args.probe_import),
                commands=commands,
            ),
            context=runtime_context,
            lock_timeout_seconds=args.lock_timeout,
        )
    except LockFailed as error:
        _print_backend_error(error)
        return ExitCode.LOCK_FAILED
    except VerificationFailed as error:
        _print_backend_error(error)
        return ExitCode.VERIFICATION_FAILED
    except (PluginError, BackendError, ValueError) as error:
        logger.error(str(error))
        return ExitCode.VERIFICATION_FAILED

    if args.json:
        print(
            json.dumps(
                {
                    "candidate_id": args.candidate,
                    "passed": report.passed,
                    "expected_state_digest": report.expected_state_digest,
                    "observed_state_digest": report.observed_state_digest,
                    "diagnostics": [
                        {
                            "code": item.code,
                            "message": item.message,
                            "severity": item.severity.value,
                        }
                        for item in report.diagnostics
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_verification_report(args.candidate, report)
    return ExitCode.OK if report.passed else ExitCode.VERIFICATION_FAILED


def run_promote(args: argparse.Namespace, services: ApplicationServices) -> int:
    validation = _validate_deployment_runtime_args(args)
    if validation is not None:
        logger.error(validation)
        return ExitCode.INVALID_INPUT
    try:
        commands = tuple(_parse_probe_command(item) for item in args.probe_command)
    except ValueError as error:
        logger.error(str(error))
        return ExitCode.INVALID_INPUT
    try:
        provider_name = _select_provider_for_candidate(
            args.provider, Path(args.deployment), args.candidate
        )
        verifier_name = _select_verifier_name(args.verifier, provider_name)
        _provider_plugin, provider = services.registry.find_lock_provider_entry(provider_name)
        _verifier_plugin, verifier = services.registry.find_verifier_entry(verifier_name)
        deployment = EnvironmentTarget(Path(args.deployment), verifier.deployment_kind)
        record = promote_candidate(
            deployment=deployment,
            candidate_id=args.candidate,
            provider=provider,
            verifier=verifier,
            policy=VerificationPolicy(
                load_packages=tuple(args.probe_import),
                commands=commands,
            ),
            context=_runtime_context(args, services, working_directory=deployment.path),
            lock_timeout_seconds=args.lock_timeout,
        )
    except PromotionFailed as error:
        _print_backend_error(error)
        return ExitCode.PROMOTION_FAILED
    except (PluginError, BackendError, ValueError) as error:
        logger.error(str(error))
        return ExitCode.PROMOTION_FAILED
    if args.json:
        print(
            json.dumps(
                {
                    "deployment": str(deployment.path),
                    "current_candidate_id": record.current_candidate_id,
                    "previous_candidate_id": record.previous_candidate_id,
                    "changed": record.changed,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_promotion(record)
    return ExitCode.OK


def run_rollback(args: argparse.Namespace, services: ApplicationServices) -> int:
    validation = _validate_deployment_runtime_args(args)
    if validation is not None:
        logger.error(validation)
        return ExitCode.INVALID_INPUT
    try:
        commands = tuple(_parse_probe_command(item) for item in args.probe_command)
    except ValueError as error:
        logger.error(str(error))
        return ExitCode.INVALID_INPUT
    try:
        provider_name = _select_provider_for_current(args.provider, Path(args.deployment))
        verifier_name = _select_verifier_name(args.verifier, provider_name)
        _provider_plugin, provider = services.registry.find_lock_provider_entry(provider_name)
        _verifier_plugin, verifier = services.registry.find_verifier_entry(verifier_name)
        deployment = EnvironmentTarget(Path(args.deployment), verifier.deployment_kind)
        result = rollback_deployment(
            deployment=deployment,
            provider=provider,
            verifier=verifier,
            policy=VerificationPolicy(
                load_packages=tuple(args.probe_import),
                commands=commands,
            ),
            context=_runtime_context(args, services, working_directory=deployment.path),
            lock_timeout_seconds=args.lock_timeout,
        )
    except RollbackFailed as error:
        _print_backend_error(error)
        return ExitCode.ROLLBACK_FAILED
    except (PluginError, BackendError, ValueError) as error:
        logger.error(str(error))
        return ExitCode.ROLLBACK_FAILED
    if args.json:
        print(
            json.dumps(
                {
                    "deployment": str(deployment.path),
                    "current_candidate_id": result.current_candidate_id,
                    "replaced_candidate_id": result.replaced_candidate_id,
                    "changed": result.changed,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_rollback(result)
    return ExitCode.OK


def run_status(args: argparse.Namespace) -> int:
    deployment = EnvironmentTarget(Path(args.deployment), args.deployment_kind)
    try:
        current, history = deployment_status(deployment)
    except PromotionFailed as error:
        _print_backend_error(error)
        return ExitCode.PROMOTION_FAILED
    if args.json:
        print(
            json.dumps(
                {
                    "deployment": str(deployment.path),
                    "deployment_kind": deployment.kind,
                    "current_candidate_id": current,
                    "history": list(history),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_deployment_status(str(deployment.path), current, history)
    return ExitCode.OK


def run_doctor_command(args: argparse.Namespace, services: ApplicationServices) -> int:
    if args.lock_timeout < 0:
        logger.error("--lock-timeout cannot be negative.")
        return ExitCode.INVALID_INPUT
    if args.timeout <= 0 or args.output_limit < 1:
        logger.error("--timeout and --output-limit must be positive.")
        return ExitCode.INVALID_INPUT
    report = run_doctor(
        services.registry,
        context=_runtime_context(args, services, working_directory=Path.cwd()),
        plugin_names=tuple(args.plugin),
        deployment=Path(args.deployment) if args.deployment else None,
        lock_timeout_seconds=args.lock_timeout,
        strict_backends=args.strict_backends,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "passed": report.passed,
                    "findings": [
                        {
                            "code": item.code,
                            "message": item.message,
                            "severity": item.severity.value,
                        }
                        for item in report.findings
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("Depviz Doctor")
        print("=" * 40)
        for item in report.findings:
            print(f"{item.severity.value.upper()}: {item.code}: {item.message}")
    return ExitCode.OK if report.passed else ExitCode.MAINTENANCE_FAILED


def run_gc(args: argparse.Namespace) -> int:
    if args.keep < 0 or args.lock_timeout < 0:
        logger.error("--keep and --lock-timeout cannot be negative.")
        return ExitCode.INVALID_INPUT
    try:
        plan = collect_candidates(
            Path(args.deployment),
            keep=args.keep,
            execute=args.execute,
            lock_timeout_seconds=args.lock_timeout,
        )
    except (OSError, ValueError) as error:
        logger.error(str(error))
        return ExitCode.MAINTENANCE_FAILED
    payload = {
        "deployment": str(plan.deployment),
        "executed": args.execute,
        "protected": list(plan.protected),
        "retained": list(plan.retained),
        "removable": list(plan.removable),
        "already_removed": list(plan.already_removed),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Candidate Garbage Collection")
        print("=" * 40)
        print(f"Deployment: {plan.deployment}")
        print(f"Mode:       {'execute' if args.execute else 'dry-run'}")
        print(f"Protected:  {len(plan.protected)}")
        print(f"Retained:   {len(plan.retained)}")
        print(f"Removable:  {len(plan.removable)}")
        for candidate_id in plan.removable:
            print(f"- {candidate_id}")
    return ExitCode.OK


def _validate_runtime_args(path: Path, args: argparse.Namespace) -> str | None:
    if not path.exists():
        return f"Lock file not found: {path}"
    if args.timeout <= 0:
        return "--timeout must be positive."
    if args.output_limit < 1:
        return "--output-limit must be positive."
    if args.lock_timeout < 0:
        return "--lock-timeout cannot be negative."
    return None


def _validate_deployment_runtime_args(args: argparse.Namespace) -> str | None:
    if args.timeout <= 0:
        return "--timeout must be positive."
    if args.output_limit < 1:
        return "--output-limit must be positive."
    if args.lock_timeout < 0:
        return "--lock-timeout cannot be negative."
    return None


def _runtime_context(
    args: argparse.Namespace,
    services: ApplicationServices,
    *,
    working_directory: Path,
) -> OperationContext:
    configuration = _common_backend_configuration(args)
    return OperationContext(
        command_runner=services.command_runner,
        offline=args.offline,
        working_directory=working_directory,
        configuration=configuration,
    )


def _parse_probe_command(value: str) -> tuple[str, ...]:
    try:
        argv = tuple(shlex.split(value))
    except ValueError as error:
        raise ValueError(f"Invalid --probe-command {value!r}: {error}") from error
    if not argv:
        raise ValueError("--probe-command cannot be empty")
    return argv


def _validate_solver_args(path: Path, args: argparse.Namespace) -> str | None:
    if not path.exists():
        return f"File not found: {path}"
    if args.timeout <= 0:
        return "--timeout must be positive."
    if args.output_limit < 1:
        return "--output-limit must be positive."
    if hasattr(args, "limit") and args.limit < 0:
        return "--limit cannot be negative."
    return None


def _load_intent(
    path: Path,
    services: ApplicationServices,
    context: OperationContext,
) -> DependencyIntent:
    loader = services.registry.find_manifest_loader(path)
    return loader.load(path, context)


def _solver_context(
    path: Path,
    args: argparse.Namespace,
    services: ApplicationServices,
) -> OperationContext:
    configuration = _common_backend_configuration(args)
    if args.extra:
        configuration["python.extras"] = ",".join(args.extra)
    if args.group:
        configuration["python.groups"] = ",".join(args.group)
    return OperationContext(
        command_runner=services.command_runner,
        offline=args.offline,
        working_directory=path.parent,
        configuration=configuration,
    )


def _common_backend_configuration(args: argparse.Namespace) -> dict[str, str]:
    configuration = {
        "conda.tool": args.tool,
        "conda.timeout_seconds": str(args.timeout),
        "conda.output_limit": str(args.output_limit),
        "python.timeout_seconds": str(args.timeout),
        "python.output_limit": str(args.output_limit),
    }
    if args.executable:
        configuration["conda.executable"] = args.executable
    if args.solver:
        configuration["conda.solver"] = args.solver
    if args.python:
        configuration["python.interpreter"] = args.python
    if args.uv_executable:
        configuration["python.uv_executable"] = args.uv_executable
    if getattr(args, "allow_weak_checksum", False):
        configuration["security.allow_weak_checksums"] = "true"
    return configuration


def _initial_target(resolver_name: str, platform: str | None) -> Target:
    if resolver_name == "uv-lock":
        return Target(platform=platform or "python-host")
    return Target(platform=platform or host_conda_platform())


def _native_tool(state: EnvironmentState) -> str | None:
    if state.native_payload is None:
        return None
    raw = state.native_payload.data.get("tool")
    return raw if isinstance(raw, str) else None


def _native_tool_version(state: EnvironmentState) -> str | None:
    if state.native_payload is None:
        return None
    raw = state.native_payload.data.get("tool_version")
    return raw if isinstance(raw, str) else None


def _backend_identity(
    plugin: BackendPlugin,
    component: str,
    resolution: Resolution,
) -> BackendIdentity:
    tool: str | None = None
    tool_version: str | None = None
    if resolution.native_payload is not None:
        raw_tool = resolution.native_payload.data.get("tool")
        raw_version = resolution.native_payload.data.get("tool_version")
        tool = raw_tool if isinstance(raw_tool, str) else None
        tool_version = raw_version if isinstance(raw_version, str) else None
    return BackendIdentity(
        component=component,
        plugin=plugin.name,
        plugin_version=plugin.plugin_version,
        tool=tool,
        tool_version=tool_version,
    )


def _select_resolver_name(requested: str, intent: DependencyIntent) -> str:
    if requested != "auto":
        return requested
    ecosystems = {requirement.ecosystem for requirement in intent.requirements}
    if ecosystems == {"conda"}:
        return "conda-dry-run"
    if ecosystems == {"pypi"}:
        return "uv-lock"
    if ecosystems == {"conda", "pypi"}:
        return "conda-pip"
    names = ", ".join(sorted(ecosystems)) or "none"
    raise ResolutionFailed(
        backend="auto",
        operation="select resolver",
        message=f"No resolver supports manifest ecosystems: {names}",
    )


def _select_inspector_name(requested: str, resolver_name: str) -> str:
    if requested != "auto":
        return requested
    return {
        "conda-dry-run": "conda-prefix",
        "uv-lock": "python-venv",
        "conda-pip": "conda-pip-prefix",
    }.get(resolver_name, "conda-prefix")


def _select_provider_for_resolution(requested: str, resolution: Resolution) -> str:
    if requested != "auto":
        return requested
    ecosystems = {package.ecosystem for package in resolution.packages}
    if ecosystems == {"conda"}:
        return "conda-exact-lock"
    if ecosystems == {"pypi"}:
        return "python-exact-lock"
    if ecosystems == {"conda", "pypi"}:
        return "conda-pip-exact-lock"
    raise ValueError(f"Cannot select a lock provider for ecosystems: {sorted(ecosystems)}")


def _select_provider_for_lock(requested: str, path: Path) -> str:
    if requested != "auto":
        return requested
    try:
        value = json.loads(read_bytes_limited(path, label="exact lock"))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot identify exact lock format: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Cannot identify exact lock format: root is not an object")
    schema = value.get("schema")
    mapping = {
        "depviz.conda-lock": "conda-exact-lock",
        "depviz.python-lock": "python-exact-lock",
        "depviz.conda-pip-lock": "conda-pip-exact-lock",
    }
    if not isinstance(schema, str) or schema not in mapping:
        raise ValueError(f"Unknown exact lock schema: {schema!r}")
    return mapping[schema]


def _select_driver_name(requested: str, provider_name: str) -> str:
    if requested != "auto":
        return requested
    return {
        "conda-exact-lock": "conda-prefix-driver",
        "python-exact-lock": "python-venv-driver",
        "conda-pip-exact-lock": "conda-pip-prefix-driver",
    }[provider_name]


def _select_verifier_name(requested: str, provider_name: str) -> str:
    if requested != "auto":
        return requested
    return {
        "conda-exact-lock": "conda-prefix-verifier",
        "python-exact-lock": "python-venv-verifier",
        "conda-pip-exact-lock": "conda-pip-prefix-verifier",
    }[provider_name]


def _provider_for_lock_format(lock_format: str) -> str:
    mapping = {
        "depviz.conda-lock.v1": "conda-exact-lock",
        "depviz.python-lock.v1": "python-exact-lock",
        "depviz.conda-pip-lock.v1": "conda-pip-exact-lock",
    }
    try:
        return mapping[lock_format]
    except KeyError as error:
        raise ValueError(f"Unknown candidate lock format: {lock_format!r}") from error


def _select_provider_for_candidate(requested: str, deployment: Path, candidate_id: str) -> str:
    if requested != "auto":
        return requested
    record = ManagedDeploymentStore(deployment).read_candidate(candidate_id)
    return _provider_for_lock_format(record.lock_format)


def _select_provider_for_current(requested: str, deployment: Path) -> str:
    if requested != "auto":
        return requested
    store = ManagedDeploymentStore(deployment)
    state = store.read_state()
    if state.current_candidate_id is None:
        raise ValueError("Deployment has no current candidate")
    record = store.read_candidate(state.current_candidate_id)
    return _provider_for_lock_format(record.lock_format)


def _policy_rejected(findings: tuple[PolicyFinding, ...], threshold: str) -> bool:
    if threshold == "never":
        return False
    if threshold == "warning":
        return bool(findings)
    return any(item.severity is Severity.ERROR for item in findings)


def _print_backend_error(error: BackendError) -> None:
    logger.error(str(error))
    if error.diagnostics:
        print_diagnostics(error.diagnostics)
