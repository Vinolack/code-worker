from importlib import util
from pathlib import Path


def _load_const_module():
    const_path = Path(__file__).resolve().parents[2] / "src" / "base" / "constants" / "const.py"
    spec = util.spec_from_file_location("const_module", const_path)

    assert spec is not None and spec.loader is not None

    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONST = _load_const_module()


def test_legacy_governed_prefixes_cover_critical_bare_keys():
    governed_prefixes = set(CONST.LEGACY_GOVERNED_KEY_PREFIXES)

    assert CONST.USER_API_KEY_SET_PREFIX in governed_prefixes
    assert CONST.USER_AI_MODEL_SET_PREFIX in governed_prefixes


def test_collision_risk_detection_flags_critical_legacy_bare_prefixes():
    candidate_prefixes = {
        CONST.USER_API_KEY_SET_PREFIX,
        CONST.USER_AI_MODEL_SET_PREFIX,
        f"prod{CONST.KEY_NAMESPACE_SEPARATOR}worker{CONST.KEY_NAMESPACE_SEPARATOR}{CONST.LIMITER_KEY_NAMESPACE_MODULE}",
        f"{CONST.LIMITER_KEY_PREFIX}{CONST.KEY_NAMESPACE_SEPARATOR}user-total",
    }

    collisions = candidate_prefixes.intersection(CONST.LEGACY_GOVERNED_KEY_PREFIXES)

    assert collisions == {CONST.USER_API_KEY_SET_PREFIX, CONST.USER_AI_MODEL_SET_PREFIX}


def test_limiter_prefixes_are_not_classified_as_legacy_governed_keys():
    governed_prefixes = set(CONST.LEGACY_GOVERNED_KEY_PREFIXES)
    limiter_namespaced_prefix = (
        f"prod{CONST.KEY_NAMESPACE_SEPARATOR}worker{CONST.KEY_NAMESPACE_SEPARATOR}{CONST.LIMITER_KEY_NAMESPACE_MODULE}"
    )

    assert CONST.LIMITER_KEY_PREFIX not in governed_prefixes
    assert limiter_namespaced_prefix not in governed_prefixes


def test_governed_legacy_key_prefix_contains_env_service_and_module():
    key_prefix = CONST.build_governed_key_prefix(
        prefix=CONST.USER_API_KEY_SET_PREFIX,
        env="prod",
        service="worker",
        module=CONST.LEGACY_KEY_NAMESPACE_MODULE,
    )

    assert key_prefix == "prod:worker:legacy:user-api-key"
