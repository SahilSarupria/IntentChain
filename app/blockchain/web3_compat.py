"""
IntentChain — web3.py version compatibility shim.

web3.py v6 renamed `Contract.encodeABI(fn_name=..., args=...)` to
`Contract.encode_abi(fn_name=..., args=...)` (and later versions may rename
again). Every place in this project that builds contract calldata goes
through `encode_fn()` here instead of calling either method directly, so a
version bump doesn't require hunting through multiple files.
"""


def encode_fn(contract, fn_name: str, args: list):
    """Encode a contract function call to calldata, working across web3.py
    versions that expose either `encode_abi` (v6+) or `encodeABI` (v5)."""
    if hasattr(contract, "encode_abi"):
        return contract.encode_abi(fn_name, args=args)
    if hasattr(contract, "encodeABI"):
        return contract.encodeABI(fn_name=fn_name, args=args)
    raise AttributeError(
        "This version of web3.py's Contract object exposes neither "
        "'encode_abi' nor 'encodeABI' — please check your installed web3.py version."
    )