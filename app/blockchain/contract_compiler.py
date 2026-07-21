"""
IntentChain — Contract Compiler

Compiles SupplyChainTraceability.sol server-side (via py-solc-x) so a
customer can deploy their own private instance with one click/prompt,
instead of copy-pasting the source into Remix. The compiled ABI + bytecode
are cached in-process after the first compile — solc itself only needs to
run once per server lifetime (or once per source change).

py-solc-x downloads the actual `solc` compiler binary the first time it's
used (from https://github.com/ethereum/solc-bin), which needs outbound
internet on whatever machine is running this backend. That's expected to be
available in a normal deployment even though it isn't in every sandboxed
environment — if the download fails (offline, firewalled), `compile()`
raises a clear error and the UI falls back to the manual "copy source into
Remix" path, which never needed this dependency in the first place.
"""
from __future__ import annotations

import os

SOLC_VERSION = "0.8.20"
_CONTRACT_PATH = os.path.join(os.path.dirname(__file__), "contracts", "SupplyChainTraceability.sol")
_CONTRACT_NAME = "SupplyChainTraceability"

_cache: dict | None = None  # {"abi": [...], "bytecode": "0x..."}


def _ensure_solc():
    import solcx
    installed = solcx.get_installed_solc_versions()
    if SOLC_VERSION not in [str(v) for v in installed]:
        solcx.install_solc(SOLC_VERSION)
    solcx.set_solc_version(SOLC_VERSION)


def compile_supply_chain_contract() -> dict:
    """Returns {"abi": [...], "bytecode": "0x..."}. Cached after first call."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        import solcx
    except ImportError as exc:
        raise RuntimeError(
            "py-solc-x isn't installed on the server. Run `pip install py-solc-x`, "
            "or deploy the contract manually via Remix instead (see the Supply Chain panel)."
        ) from exc

    try:
        _ensure_solc()
        with open(_CONTRACT_PATH, "r") as fh:
            source = fh.read()

        compiled = solcx.compile_source(
            source,
            output_values=["abi", "bin"],
            solc_version=SOLC_VERSION,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Couldn't compile the contract server-side ({exc}). "
            f"This usually means solc couldn't be downloaded (no internet on the server) — "
            f"deploy manually via Remix instead (see the Supply Chain panel)."
        ) from exc

    key = next((k for k in compiled if k.endswith(f":{_CONTRACT_NAME}")), None)
    if key is None:
        raise RuntimeError(f"Compiled output didn't contain contract '{_CONTRACT_NAME}' — check the .sol source.")

    bytecode = compiled[key]["bin"]
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    _cache = {"abi": compiled[key]["abi"], "bytecode": bytecode}
    return _cache
