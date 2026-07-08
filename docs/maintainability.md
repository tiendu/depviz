# Maintainability rules

Depviz uses structural interfaces at subsystem boundaries and concrete data
classes for immutable values. Inheritance is reserved for exception taxonomy.

## Composition

`main.py` is the composition root. It selects concrete infrastructure and
passes it through `ApplicationServices` and `OperationContext`. Backend code
must not silently instantiate command runners or other process-wide services.
A missing required service is an explicit boundary error.

## Protocol boundaries

- `CommandRunner` isolates subprocess execution.
- `PluginCatalog` is the public, read-only application view of plugin lookup.
- Backend component protocols define loading, inspection, resolution, locking,
  candidate application, verification, and health checks.
- Tests should use small structural fakes rather than subclassing production
  implementations.

## Compatibility facades

The pre-0.5 modules remain thin, explicit re-export facades. They must contain
no behavior and must use explicit imports and `__all__`; wildcard re-exports
are prohibited because they make accidental API growth invisible.

## Dependency injection

Code that executes external commands calls `require_command_runner`. This keeps
process execution policy at the composition boundary and makes backends
deterministic under tests. Non-command operations may still use an
`OperationContext` without a runner.

## Cleanup policy

Remove private helpers when no source, test, documentation, or compatibility
facade references them. Public compatibility names require a deliberate
compatibility release before removal.

## Automated guardrails

`make check-release` runs strict typing, linting, formatting, high-confidence
dead-code detection, and the complete deterministic test suite. Architecture
tests prevent concrete command-runner construction outside the composition
root and prevent wildcard compatibility exports from returning.
