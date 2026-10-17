"""Supported blockchains and address helpers. To add a chain later, add one line to CHAINS."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    key: str
    name: str
    dexscreener_id: str  # chainId used by DexScreener
    goplus_id: str  # chain_id used by GoPlus (not used for Solana)
    kind: str = "evm"  # "evm" or "solana"


CHAINS: dict[str, Chain] = {
    "base": Chain("base", "Base", "base", "8453"),
    "bsc": Chain("bsc", "BNB Chain", "bsc", "56"),
    "ethereum": Chain("ethereum", "Ethereum", "ethereum", "1"),
    "solana": Chain("solana", "Solana", "solana", "", "solana"),
}

_ALIASES = {
    "base": "base",
    "bsc": "bsc", "bnb": "bsc", "bnbchain": "bsc", "binance": "bsc",
    "eth": "ethereum", "ethereum": "ethereum", "mainnet": "ethereum",
    "sol": "solana", "solana": "solana",
}

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_ADDRESS_IN_TEXT = re.compile(r"(?<![0-9a-zA-Z])0x[0-9a-fA-F]{40}(?![0-9a-zA-Z])")
_BASE58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_SOLANA_IN_TEXT = re.compile(r"(?<![0-9A-Za-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![0-9A-Za-z])")


def resolve_chain(text: str) -> str | None:
    return _ALIASES.get((text or "").strip().lower())


def address_kind(address: str) -> str | None:
    if _ADDRESS.fullmatch(address or ""):
        return "evm"
    if _BASE58.fullmatch(address or ""):
        return "solana"
    return None


def normalize_address(text: str) -> str | None:
    """EVM addresses are lowercased. Solana addresses are case-sensitive and kept as they are."""
    text = (text or "").strip()
    kind = address_kind(text)
    if kind == "evm":
        return text.lower()
    return text if kind == "solana" else None


def find_address(text: str) -> str | None:
    """Finds a token address inside a message. A 64-character hash does not count."""
    match = _ADDRESS_IN_TEXT.search(text or "")
    if match:
        return match.group(0).lower()
    match = _SOLANA_IN_TEXT.search(text or "")
    return match.group(0) if match else None


def chains_for(address: str) -> list[Chain]:
    kind = address_kind(address)
    return [c for c in CHAINS.values() if c.kind == kind]


def chain_names() -> str:
    return ", ".join(c.name for c in CHAINS.values())
