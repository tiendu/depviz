from depviz import __version__
from depviz.api import BackendPlugin, Capability, PLUGIN_API_VERSION
from depviz.builtin.mixed.driver import CondaPipPrefixDriver
from depviz.builtin.mixed.inspector import CondaPipPrefixInspector
from depviz.builtin.mixed.locking import CondaPipLockProvider
from depviz.builtin.mixed.resolver import CondaPipResolver
from depviz.builtin.mixed.verifier import CondaPipPrefixVerifier


def create_plugin() -> BackendPlugin:
    return BackendPlugin(
        name="depviz-conda-pip",
        plugin_version=__version__,
        api_version=PLUGIN_API_VERSION,
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
        inspectors=(CondaPipPrefixInspector(),),
        resolvers=(CondaPipResolver(),),
        lock_providers=(CondaPipLockProvider(),),
        environment_drivers=(CondaPipPrefixDriver(),),
        verifiers=(CondaPipPrefixVerifier(),),
    )
