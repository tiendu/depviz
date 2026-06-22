from depviz import __version__
from depviz.api import BackendPlugin, Capability, PLUGIN_API_VERSION
from depviz.builtin.python.driver import PythonVenvDriver
from depviz.builtin.python.health import PythonHealthCheck
from depviz.builtin.python.inspector import PythonVenvInspector
from depviz.builtin.python.locking import PythonLockProvider
from depviz.builtin.python.resolver import UvResolver
from depviz.builtin.python.verifier import PythonVenvVerifier


def create_plugin() -> BackendPlugin:
    return BackendPlugin(
        name="depviz-python",
        plugin_version=__version__,
        api_version=PLUGIN_API_VERSION,
        health_checks=(PythonHealthCheck(),),
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
        inspectors=(PythonVenvInspector(),),
        resolvers=(UvResolver(),),
        lock_providers=(PythonLockProvider(),),
        environment_drivers=(PythonVenvDriver(),),
        verifiers=(PythonVenvVerifier(),),
    )
