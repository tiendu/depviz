from depviz import __version__
from depviz.api import BackendPlugin, Capability, PLUGIN_API_VERSION
from depviz.builtin.conda.driver import CondaPrefixDriver
from depviz.builtin.conda.health import CondaHealthCheck
from depviz.builtin.conda.inspector import CondaPrefixInspector
from depviz.builtin.conda.locking import CondaLockProvider
from depviz.builtin.conda.resolver import CondaDryRunResolver
from depviz.builtin.conda.verifier import CondaPrefixVerifier


def create_plugin() -> BackendPlugin:
    return BackendPlugin(
        name="depviz-conda",
        plugin_version=__version__,
        api_version=PLUGIN_API_VERSION,
        health_checks=(CondaHealthCheck(),),
        capabilities=frozenset(
            {
                Capability.INSPECT,
                Capability.RESOLVE,
                Capability.LOCK,
                Capability.HASH_LOCKING,
                Capability.APPLY,
                Capability.VERIFY,
            }
        ),
        inspectors=(CondaPrefixInspector(),),
        resolvers=(CondaDryRunResolver(),),
        lock_providers=(CondaLockProvider(),),
        environment_drivers=(CondaPrefixDriver(),),
        verifiers=(CondaPrefixVerifier(),),
    )
