
"""
IntentChain — Contract Compiler

Compiles SupplyChainTraceability.sol using py-solc-x.

Prefers the native Homebrew Solidity compiler on Apple Silicon.
Falls back to the py-solc-x managed compiler (0.8.20).

ABI and bytecode are cached after successful compilation.
"""

from __future__ import annotations

import os
import shutil

SOLC_VERSION = "0.8.20"

_CONTRACT_PATH = os.path.join(
    os.path.dirname(__file__),
    "contracts",
    "SupplyChainTraceability.sol",
)

_CONTRACT_NAME = "SupplyChainTraceability"

_cache: dict | None = None


def _ensure_solc():
    """
    Returns a native solc executable if available.
    Otherwise installs and selects the requested solcx version.
    """
    import solcx

    native_solc = shutil.which("solc")

    if native_solc:
        return native_solc

    installed = solcx.get_installed_solc_versions()

    if SOLC_VERSION not in [str(v) for v in installed]:
        solcx.install_solc(SOLC_VERSION)

    solcx.set_solc_version(SOLC_VERSION)

    return None


def compile_supply_chain_contract() -> dict:
    """
    Returns:
        {
            "abi": [...],
            "bytecode": "0x..."
        }

    Compilation result is cached after the first successful call.
    """
    global _cache

    if _cache is not None:
        return _cache

    try:
        import solcx
    except ImportError as exc:
        raise RuntimeError(
            "py-solc-x isn't installed. "
            "Run pip install py-solc-x."
        ) from exc

    try:
        if not os.path.exists(_CONTRACT_PATH):
            raise FileNotFoundError(
                f"Solidity contract not found: {_CONTRACT_PATH}"
            )

        # Detect the available compiler.
        solc_binary = _ensure_solc()

        # Read Solidity source.
        with open(_CONTRACT_PATH, "r", encoding="utf-8") as fh:
            source = fh.read()

        # Compile with the native Homebrew compiler when available.
        if solc_binary:
            compiled = solcx.compile_source(
                source,
                output_values=["abi", "bin"],
                solc_binary=solc_binary,
                optimize=True,
                optimize_runs=200,
            )

        # Otherwise use the py-solc-x managed compiler.
        else:
            compiled = solcx.compile_source(
                source,
                output_values=["abi", "bin"],
                solc_version=SOLC_VERSION,
                optimize=True,
                optimize_runs=200,
            )

        # Locate the requested contract.
        key = next(
            (
                k for k in compiled
                if k.endswith(f":{_CONTRACT_NAME}")
            ),
            None,
        )

        if key is None:
            raise RuntimeError(
                f"Compiled output didn't contain contract "
                f"'{_CONTRACT_NAME}'. Check the Solidity source."
            )

        bytecode = compiled[key]["bin"]

        if not bytecode:
            raise RuntimeError(
                "Compiler returned empty bytecode."
            )

        if not bytecode.startswith("0x"):
            bytecode = "0x" + bytecode

        _cache = {
            "abi": compiled[key]["abi"],
            "bytecode": bytecode,
        }

        return _cache

    except Exception as exc:
        raise RuntimeError(
            f"Couldn't compile the contract server-side ({exc}). "
            "Check your Solidity compiler installation and contract source. "
            "You can also deploy manually via Remix."
        ) from exc
