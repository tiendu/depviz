from enum import StrEnum


class Capability(StrEnum):
    LOAD_MANIFEST = "load-manifest"
    INSPECT = "inspect"
    RESOLVE = "resolve"
    LOCK = "lock"
    APPLY = "apply"
    VERIFY = "verify"

    OFFLINE_RESOLUTION = "offline-resolution"
    CROSS_PLATFORM_RESOLUTION = "cross-platform-resolution"
    HASH_LOCKING = "hash-locking"
    TRANSACTIONAL_APPLY = "transactional-apply"
