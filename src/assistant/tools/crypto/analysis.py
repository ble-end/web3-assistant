"""Turns raw provider data into a labelled report.

Pure functions with no network access, so they are easy to test. Rules followed:
- Provider data is shown as provider-reported, never as verified.
- A field a provider did not return is UNKNOWN, never treated as a pass.
- Nothing is ever called "safe". Flags come from simple rules of thumb and say so.
- Token names, symbols and links come from strangers, so they are cleaned before display.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from assistant.tools.crypto.chains import CHAINS, Chain

BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_URL = re.compile(r"https?://[^\s]{3,150}")

DISCLAIMER = (
    "Research only, not financial advice. Having no flags does NOT mean a token is safe: "
    "unknown items and risks these checks cannot see still apply."
)
ATTRIBUTION = "Market data: DexScreener. Contract security data: GoPlus Security."


# ------------------------------------------------------------------ cleaning


def clean_text(value, limit: int = 60) -> str:
    text = _UNSAFE_CHARS.sub(" ", "" if value is None else str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def clean_url(value) -> str | None:
    text = "" if value is None else str(value).strip()
    return text if _URL.fullmatch(text) else None


def num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def flag(data: dict, key: str) -> bool | None:
    """GoPlus gives '1' (yes) or '0' (no). Anything else, or missing, means unknown."""
    value = data.get(key)
    if value in ("1", 1, True):
        return True
    if value in ("0", 0, False):
        return False
    return None


def _dig(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


# ---------------------------------------------------------------- formatting


def usd(value: float | None) -> str:
    if value is None:
        return "unknown"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"${value / limit:.2f}{suffix}"
    return f"${value:,.2f}"


def price(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1:
        return f"${value:,.4f}"
    return "$" + f"{value:.10f}".rstrip("0").rstrip(".")


def pct(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"


def yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


# ------------------------------------------------------------------ report


@dataclass
class Flag:
    severity: str  # "high" or "medium"
    text: str


@dataclass
class TokenReport:
    address: str
    fetched_at: datetime
    chain: Chain | None = None
    name: str = ""
    symbol: str = ""
    market: dict | None = None
    security: dict | None = None
    other_chains: list = field(default_factory=list)  # (Chain, liquidity or None)
    websites: list = field(default_factory=list)
    socials: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.market is not None or self.security is not None


def base_pairs(address: str, pairs: list[dict]) -> list[dict]:
    """Pairs where this token is the base token (so the pair's price is this token's price)."""
    return [p for p in pairs if str(_dig(p, "baseToken", "address") or "").lower() == address.lower()]


def _liquidity(pair: dict) -> float | None:
    return num(_dig(pair, "liquidity", "usd"))


def _has_data(security) -> bool:
    return isinstance(security, dict) and any(
        security.get(k) not in (None, "", {}, [])
        for k in ("token_name", "token_symbol", "total_supply", "is_open_source", "metadata", "mintable")
    )


def choose_primary_chain(address: str, pairs_by_chain: dict[str, list[dict]]) -> str | None:
    """The chain where this token has the most liquidity."""
    best, best_liq = None, -1.0
    for key, pairs in pairs_by_chain.items():
        mine = base_pairs(address, pairs)
        if not mine:
            continue
        total = sum(_liquidity(p) or 0.0 for p in mine)
        if total > best_liq:
            best, best_liq = key, total
    return best


def _market(address: str, pairs: list[dict], now: datetime) -> dict | None:
    mine = base_pairs(address, pairs)
    if not mine:
        return None
    top = max(mine, key=lambda p: _liquidity(p) or 0.0)
    liquidities = [x for x in (_liquidity(p) for p in mine) if x is not None]
    volumes = [x for x in (num(_dig(p, "volume", "h24")) for p in mine) if x is not None]
    created = [x for x in (num(p.get("pairCreatedAt")) for p in mine) if x]
    age_days = None
    if created:
        age_days = max((now.timestamp() * 1000 - min(created)) / 86_400_000, 0.0)
    boosts = _dig(top, "boosts", "active")
    return {
        "price_usd": num(top.get("priceUsd")),
        "market_cap": num(top.get("marketCap")),
        "fdv": num(top.get("fdv")),
        "liquidity_usd": sum(liquidities) if liquidities else None,
        "volume_24h": sum(volumes) if volumes else None,
        "change_24h": num(_dig(top, "priceChange", "h24")),
        "buys_24h": num(_dig(top, "txns", "h24", "buys")),
        "sells_24h": num(_dig(top, "txns", "h24", "sells")),
        "pair_count": len(mine),
        "top_pair_url": clean_url(top.get("url")),
        "dex": clean_text(top.get("dexId"), 30),
        "quote_symbol": clean_text(_dig(top, "quoteToken", "symbol"), 20),
        "oldest_pair_age_days": age_days,
        "boosts": int(boosts) if num(boosts) is not None else None,
    }


def _links(address: str, pairs: list[dict]) -> tuple[list[str], list[str]]:
    websites: list[str] = []
    socials: list[str] = []
    for pair in sorted(base_pairs(address, pairs), key=lambda p: -(_liquidity(p) or 0.0)):
        info = pair.get("info")
        if not isinstance(info, dict):
            continue
        for site in info.get("websites") or []:
            url = clean_url(site.get("url") if isinstance(site, dict) else None)
            if url and url not in websites:
                websites.append(url)
        for social in info.get("socials") or []:
            if isinstance(social, dict):
                text = f"{clean_text(social.get('platform'), 20)}: {clean_text(social.get('handle'), 60)}"
                if text not in socials:
                    socials.append(text)
    return websites[:3], socials[:5]


def parse_security(raw: dict) -> dict:
    """Normalises GoPlus output. Missing fields stay None (unknown)."""
    owner_raw = raw.get("owner_address")
    if owner_raw is None:
        owner = None  # unknown
    elif str(owner_raw).strip() == "" or str(owner_raw).lower() in BURN_ADDRESSES:
        owner = "none"  # no owner / renounced, as reported by GoPlus
    else:
        owner = clean_text(owner_raw, 42)

    def holders(key):
        out = []
        for h in raw.get(key) or []:
            if not isinstance(h, dict):
                continue
            percent = num(h.get("percent"))
            out.append(
                {
                    "address": str(h.get("address") or "").lower(),
                    "tag": clean_text(h.get("tag"), 30),
                    "percent": None if percent is None else percent * 100,
                    "is_contract": flag(h, "is_contract"),
                    "locked": flag(h, "is_locked"),
                }
            )
        return out

    top_holders = holders("holders")
    lp_holders = holders("lp_holders")
    wallets = [
        h["percent"]
        for h in top_holders
        if h["percent"] is not None
        and h["is_contract"] is not True
        and h["address"] not in BURN_ADDRESSES
        and "burn" not in h["tag"].lower()
    ]
    lp_locked = None
    if lp_holders and any(h["percent"] is not None for h in lp_holders):
        lp_locked = sum(
            h["percent"] or 0.0 for h in lp_holders if h["locked"] is True or h["address"] in BURN_ADDRESSES
        )
    cex = raw.get("is_in_cex") if isinstance(raw.get("is_in_cex"), dict) else {}
    launchpad = raw.get("launchpad_token") if isinstance(raw.get("launchpad_token"), dict) else {}
    creator = num(raw.get("creator_percent"))
    return {
        "kind": "evm",
        "name": clean_text(raw.get("token_name"), 40),
        "symbol": clean_text(raw.get("token_symbol"), 20),
        "open_source": flag(raw, "is_open_source"),
        "proxy": flag(raw, "is_proxy"),
        "mintable": flag(raw, "is_mintable"),
        "honeypot": flag(raw, "is_honeypot"),
        "buy_tax": num(raw.get("buy_tax")),
        "sell_tax": num(raw.get("sell_tax")),
        "cannot_buy": flag(raw, "cannot_buy"),
        "cannot_sell_all": flag(raw, "cannot_sell_all"),
        "tax_modifiable": flag(raw, "slippage_modifiable"),
        "personal_tax_modifiable": flag(raw, "personal_slippage_modifiable"),
        "transfer_pausable": flag(raw, "transfer_pausable"),
        "blacklist": flag(raw, "is_blacklisted"),
        "hidden_owner": flag(raw, "hidden_owner"),
        "can_reclaim_ownership": flag(raw, "can_take_back_ownership"),
        "owner_can_edit_balances": flag(raw, "owner_change_balance"),
        "selfdestruct": flag(raw, "selfdestruct"),
        "owner": owner,
        "creator_pct": None if creator is None else creator * 100,
        "holder_count": num(raw.get("holder_count")),
        "top_wallet_pct": max(wallets) if wallets else None,
        "top10_pct": sum(h["percent"] for h in top_holders if h["percent"] is not None) if top_holders else None,
        "lp_locked_pct": lp_locked,
        "is_in_dex": flag(raw, "is_in_dex"),
        "airdrop_scam": flag(raw, "is_airdrop_scam"),
        "fake_token": _dig(raw, "fake_token", "value") in (1, "1"),
        "trust_list": flag(raw, "trust_list"),
        "cex": [clean_text(x, 30) for x in (cex.get("cex_list") or [])][:3] if flag(cex, "listed") else [],
        "launchpad": clean_text(launchpad.get("launchpad_name"), 30) if flag(launchpad, "is_launchpad_token") else "",
        "other_risks": clean_text(raw.get("other_potential_risks"), 200),
        "note": clean_text(raw.get("note"), 200),
    }


# Items we expect GoPlus to return for an open-source token. Absent = unknown.
_EXPECTED = [
    ("honeypot", "honeypot check"), ("buy_tax", "buy tax"), ("sell_tax", "sell tax"),
    ("owner", "owner status"), ("mintable", "mint function"), ("hidden_owner", "hidden owner check"),
    ("transfer_pausable", "trading pause check"), ("blacklist", "blacklist check"),
    ("tax_modifiable", "tax change check"),
]


# ---------------------------------------------------------------- Solana

_POOL_WORDS = ("pool", "raydium", "orca", "meteora", "pump", "vault", "amm", "burn", "lock", "bonding", "incinerator", "liquidity")
_EXPECTED_SOLANA = [
    ("mintable", "mint authority check"), ("freezable", "freeze authority check"),
    ("closable", "close authority check"), ("balance_mutable", "balance change check"),
    ("metadata_mutable", "metadata check"), ("hook", "transfer hook check"),
    ("non_transferable", "non-transferable check"),
]


def _status(raw: dict, key: str) -> bool | None:
    value = raw.get(key)
    return flag(value, "status") if isinstance(value, dict) else flag(raw, key)


def _entries(value) -> list[dict]:
    if isinstance(value, dict):
        value = [value]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _authority_entries(raw: dict, key: str) -> list[dict]:
    value = raw.get(key)
    if not isinstance(value, dict):
        return []
    return _entries(value.get("authority") or value.get("metadata_upgrade_authority"))


def parse_security_solana(raw: dict) -> dict:
    """Normalises GoPlus Solana output. Missing fields stay None (unknown)."""
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    total = num(raw.get("total_supply"))
    holders = []
    for h in _entries(raw.get("holders")):
        balance = num(h.get("balance"))
        share = None
        if total and total > 0 and balance is not None:
            share = balance / total * 100
            if share > 100.0001:  # numbers do not line up, so do not trust them
                share = None
        holders.append(
            {"tag": clean_text(h.get("tag"), 30), "share": share, "locked": flag(h, "is_locked")}
        )
    wallets = [
        h["share"] for h in holders
        if h["share"] is not None and h["locked"] is not True
        and not any(word in h["tag"].lower() for word in _POOL_WORDS)
    ]
    shares = [h["share"] for h in holders]
    fee_rate = num(_dig(raw, "transfer_fee", "current_fee_rate", "fee_rate"))

    hook_raw = raw.get("transfer_hook")
    hook = None if hook_raw is None else bool(_entries(hook_raw))

    authority_lists = [_authority_entries(raw, k) for k in (
        "mintable", "freezable", "closable", "balance_mutable_authority", "metadata_mutable",
        "transfer_fee_upgradable", "transfer_hook_upgradable", "default_account_state_upgradable",
    )]
    creators = _entries(raw.get("creator") or raw.get("creators"))
    malicious = any(
        flag(entry, "malicious_address") is True
        for entry in [e for lst in authority_lists for e in lst] + creators + _entries(hook_raw)
    )

    dexes = _entries(raw.get("dex"))
    tvls = [x for x in (num(d.get("tvl")) for d in dexes) if x is not None]
    lp = _entries(raw.get("lp_holders")) or (
        _entries(max(dexes, key=lambda d: num(d.get("tvl")) or 0.0).get("lp_holders")) if dexes else []
    )
    default_state = num(raw.get("default_account_state"))
    return {
        "kind": "solana",
        "name": clean_text(meta.get("name"), 40),
        "symbol": clean_text(meta.get("symbol"), 20),
        "mintable": _status(raw, "mintable"),
        "freezable": _status(raw, "freezable"),
        "closable": _status(raw, "closable"),
        "balance_mutable": _status(raw, "balance_mutable_authority"),
        "metadata_mutable": _status(raw, "metadata_mutable"),
        "transfer_fee_upgradable": _status(raw, "transfer_fee_upgradable"),
        "transfer_hook_upgradable": _status(raw, "transfer_hook_upgradable"),
        "default_state_upgradable": _status(raw, "default_account_state_upgradable"),
        "non_transferable": flag(raw, "non_transferable"),
        "default_state": None if default_state is None else int(default_state),
        "fee_pct": None if fee_rate is None else fee_rate / 100,  # GoPlus gives parts per ten thousand
        "hook": hook,
        "trusted": flag(raw, "trusted_token"),
        "any_malicious": malicious,
        "creator": clean_text(creators[0].get("address"), 50) if creators else "",
        "top_wallet_pct": max(wallets) if wallets else None,
        "top10_pct": sum(shares) if shares and all(x is not None for x in shares) else None,
        "dex_count": len(dexes),
        "dex_tvl": sum(tvls) if tvls else None,
        "lp_listed": len(lp),
        "lp_locked_count": sum(1 for h in lp if flag(h, "is_locked") is True),
    }


def _solana_flags(sec: dict) -> list[Flag]:
    flags: list[Flag] = []

    def add(severity: str, text: str) -> None:
        flags.append(Flag(severity, text))

    trusted = sec["trusted"] is True
    note = " (GoPlus lists this as a trusted token, where such functions are generally not treated as risks.)" if trusted else ""
    strong = "medium" if trusted else "high"
    if sec["any_malicious"]:
        add("high", "GoPlus marks at least one authority or creator address as malicious.")
    if sec["freezable"] is True:
        add(strong, "A freeze authority exists: the developer can freeze accounts and block trading." + note)
    if sec["balance_mutable"] is True:
        add(strong, "The developer can change users' token balances." + note)
    if sec["closable"] is True:
        add(strong, "The developer can close the token program, which would wipe out the assets." + note)
    if sec["mintable"] is True:
        add("medium", "A mint authority exists: new tokens can still be created." + note)
    if sec["non_transferable"] is True:
        add("high", "The token is non-transferable.")
    if sec["default_state"] == 2:
        add("high", "New token accounts start frozen by default.")
    if sec["hook"] is True:
        add("medium", "The token has a transfer hook, which can block or alter trades." + note)
    for key, text in (
        ("metadata_mutable", "The token name and image can be changed by an update authority."),
        ("transfer_fee_upgradable", "The transfer fee can be changed later."),
        ("transfer_hook_upgradable", "The transfer hook can be changed later."),
        ("default_state_upgradable", "The default account state can be changed later."),
    ):
        if sec[key] is True:
            add("medium", text)
    fee = sec["fee_pct"]
    if fee is not None:
        if fee >= 50:
            add("high", f"Transfer fee is {fee:.1f}%.")
        elif fee >= 10:
            add("medium", f"Transfer fee is {fee:.1f}%.")
    if sec["top_wallet_pct"] is not None and sec["top_wallet_pct"] >= 20:
        add(
            "medium",
            f"One holder not tagged as a pool, locker or burn address holds {pct(sec['top_wallet_pct'])} of supply "
            "(calculated from balances). It may be an untagged pool vault.",
        )
    return flags


def _solana_contract_lines(s: dict) -> list[str]:
    fee = "unknown" if s["fee_pct"] is None else f"{s['fee_pct']:.1f}%"
    state = {1: "initialized", 2: "FROZEN", 0: "uninitialized"}.get(s["default_state"], "unknown")
    lines = ["", "CONTRACT (GoPlus Security, Solana API is in beta)"]
    lines.append(f"Mint authority (can create tokens): {yes_no(s['mintable'])}")
    lines.append(f"Freeze authority (can freeze accounts): {yes_no(s['freezable'])}")
    lines.append(f"Can close the token program: {yes_no(s['closable'])}")
    lines.append(f"Developer can change balances: {yes_no(s['balance_mutable'])}")
    lines.append(f"Name and image can be changed: {yes_no(s['metadata_mutable'])}")
    lines.append(f"Transfer fee: {fee} | Transfer hook: {yes_no(s['hook'])}")
    lines.append(f"Non-transferable: {yes_no(s['non_transferable'])} | New account state: {state}")
    if s["top_wallet_pct"] is not None:
        lines.append(f"Largest holder not tagged as pool/locker/burn: {pct(s['top_wallet_pct'])} (calculated from balances)")
    if s["top10_pct"] is not None:
        lines.append(f"Top 10 holders together: {pct(s['top10_pct'])} (calculated; includes pools)")
    if s["dex_count"]:
        lines.append(f"Pools GoPlus lists: {s['dex_count']} (total value locked {usd(s['dex_tvl'])})")
    if s["lp_listed"]:
        lines.append(f"Top liquidity holders marked locked by GoPlus: {s['lp_locked_count']} of {s['lp_listed']} listed")
    if s["creator"]:
        lines.append(f"Creator: {s['creator']}")
    if s["trusted"]:
        lines.append("GoPlus notes: on its trusted-token list (provider-reported, not a safety guarantee)")
    return lines


def compute_flags(market: dict | None, sec: dict | None) -> list[Flag]:
    flags: list[Flag] = []

    def add(severity: str, text: str) -> None:
        flags.append(Flag(severity, text))

    if market:
        liq = market.get("liquidity_usd")
        if liq is not None and liq < 10_000:
            add("high", f"Very thin liquidity ({usd(liq)}). Small trades can move the price a lot and exiting may be hard.")
        elif liq is not None and liq < 50_000:
            add("medium", f"Low liquidity ({usd(liq)}).")
        age = market.get("oldest_pair_age_days")
        if age is not None and age < 1:
            add("medium", "The oldest trading pair is less than a day old.")

    if not sec:
        return flags
    if sec["kind"] == "solana":
        return flags + _solana_flags(sec)

    if sec["open_source"] is False:
        add("high", "The contract is not open source. GoPlus cannot check most risk items for it.")
    if sec["honeypot"] is True:
        add("high", "GoPlus flags this token as a possible honeypot (it may be impossible to sell).")
    for label, key in (("Sell", "sell_tax"), ("Buy", "buy_tax")):
        tax = sec[key]
        if tax is None:
            continue
        if tax >= 0.5:
            extra = " A value of 100% can also mean GoPlus's test trade failed." if tax >= 0.99 else ""
            add("high", f"{label} tax is reported at {tax * 100:.0f}%.{extra}")
        elif tax >= 0.10:
            add("medium", f"{label} tax is reported at {tax * 100:.0f}%.")
    if sec["hidden_owner"] is True:
        add("high", "GoPlus reports a hidden owner, so ownership may not really be given up.")
    if sec["can_reclaim_ownership"] is True:
        add("high", "GoPlus reports that ownership can be taken back.")
    if sec["owner_can_edit_balances"] is True:
        add("high", "GoPlus reports the owner can change holders' balances.")
    if sec["airdrop_scam"] is True:
        add("high", "GoPlus flags this as an airdrop scam token.")
    if sec["fake_token"]:
        add("high", "GoPlus flags this token as a copy of a well-known token.")
    if sec["cannot_sell_all"] is True:
        add("medium", "Holders cannot sell their whole balance in one go.")
    if sec["cannot_buy"] is True:
        add("medium", "GoPlus says the token cannot be bought (it may be a reward token, or the test failed).")
    for key, text in (
        ("mintable", "The contract can mint new tokens."),
        ("tax_modifiable", "The owner can change the buy/sell tax."),
        ("personal_tax_modifiable", "The owner can set different taxes for chosen addresses."),
        ("transfer_pausable", "Trading can be paused by the contract owner."),
        ("blacklist", "The contract has a blacklist function."),
        ("proxy", "It is a proxy contract, so its code may be changeable."),
        ("selfdestruct", "The contract can self-destruct."),
    ):
        if sec[key] is True:
            add("medium", text)
    if sec["owner"] not in (None, "none"):
        add("medium", f"The contract has an active owner ({sec['owner'][:10]}...). Owner powers may still apply.")

    if sec["top_wallet_pct"] is not None:
        if sec["top_wallet_pct"] >= 20:
            add("high", f"One non-contract wallet holds {pct(sec['top_wallet_pct'])} of the supply.")
        elif sec["top_wallet_pct"] >= 10:
            add("medium", f"One non-contract wallet holds {pct(sec['top_wallet_pct'])} of the supply.")
    if sec["creator_pct"] is not None:
        if sec["creator_pct"] >= 20:
            add("high", f"The creator address holds {pct(sec['creator_pct'])} of the supply.")
        elif sec["creator_pct"] >= 5:
            add("medium", f"The creator address holds {pct(sec['creator_pct'])} of the supply.")
    if sec["holder_count"] is not None and sec["holder_count"] < 100:
        add("medium", f"Only {int(sec['holder_count'])} holders.")
    if sec["lp_locked_pct"] is not None and sec["is_in_dex"] is not False and sec["lp_locked_pct"] < 50:
        add(
            "medium",
            f"Only {pct(sec['lp_locked_pct'])} of liquidity-pool tokens are in lockers or burn addresses "
            "GoPlus recognises. It only knows certain lockers, so this can understate real locking.",
        )
    return flags


def build_report(
    address: str,
    now: datetime,
    pairs_by_chain: dict[str, list[dict]],
    security_by_chain: dict[str, dict | None],
    errors: list[str] | None = None,
) -> TokenReport:
    report = TokenReport(address=address, fetched_at=now, errors=list(errors or []))
    primary = choose_primary_chain(address, pairs_by_chain)
    if primary is None:
        primary = next((k for k, s in security_by_chain.items() if _has_data(s)), None)
    if primary is None:
        return report

    report.chain = CHAINS[primary]
    pairs = pairs_by_chain.get(primary, [])
    report.market = _market(address, pairs, now)
    raw_security = security_by_chain.get(primary)
    if _has_data(raw_security):
        parser = parse_security_solana if CHAINS[primary].kind == "solana" else parse_security
        report.security = parser(raw_security)
    else:
        report.security = None
    report.websites, report.socials = _links(address, pairs)

    for key, other_pairs in pairs_by_chain.items():
        if key != primary and base_pairs(address, other_pairs):
            liq = sum(_liquidity(p) or 0.0 for p in base_pairs(address, other_pairs))
            report.other_chains.append((CHAINS[key], liq))

    mine = sorted(base_pairs(address, pairs), key=lambda p: -(_liquidity(p) or 0.0))
    top_name = clean_text(_dig(mine[0], "baseToken", "name"), 40) if mine else ""
    top_symbol = clean_text(_dig(mine[0], "baseToken", "symbol"), 20) if mine else ""
    report.name = top_name or (report.security or {}).get("name", "")
    report.symbol = top_symbol or (report.security or {}).get("symbol", "")
    report.flags = compute_flags(report.market, report.security)

    sec = report.security
    if report.market is None:
        report.missing.append("Price, liquidity and volume (no trading pair for this token was found on DexScreener)")
    elif report.market["price_usd"] is None:
        report.missing.append("Price (DexScreener did not provide one)")
    if sec is None:
        report.missing.append("All contract security checks (GoPlus returned nothing for this address)")
    else:
        expected = _EXPECTED_SOLANA if sec["kind"] == "solana" else _EXPECTED
        unknown = [label for key, label in expected if sec.get(key) is None]
        if unknown:
            report.missing.append("Not returned by GoPlus, so UNKNOWN (not safe): " + ", ".join(unknown))
        if sec["kind"] == "evm" and sec["lp_locked_pct"] is None:
            report.missing.append("Liquidity lock status")
    report.missing.append("Team identity, audits and roadmap (not checked)")
    report.missing.append("Direct on-chain verification (all figures come from the two providers above)")
    return report


def format_report(report: TokenReport) -> str:
    when = report.fetched_at.strftime("%Y-%m-%d %H:%M UTC")
    if not report.found:
        lines = [
            "TOKEN REPORT",
            f"Contract: {report.address}",
            f"Checked: {when}",
            "",
            f"I could not find this contract on {', '.join(c.name for c in CHAINS.values())} "
            "using DexScreener or GoPlus. It may be on another chain, not trading yet, or the address may be wrong.",
        ]
        if report.errors:
            lines += ["", "Problems during the check:"] + [f"- {e}" for e in report.errors]
        lines += ["", ATTRIBUTION]
        return "\n".join(lines)

    m, s = report.market, report.security
    title = f"{report.name or 'Unnamed token'} ({report.symbol or '?'})"
    lines = [
        "TOKEN REPORT",
        f"{title} on {report.chain.name}",
        f"Contract: {report.address}",
        f"Checked: {when}",
        "All figures are provider-reported and not independently verified.",
    ]
    if report.other_chains:
        others = ", ".join(f"{c.name} ({usd(liq)} liquidity)" for c, liq in report.other_chains)
        lines.append(f"Same address also trades on: {others}. Different tokens can share an address, so check you mean the right chain.")

    if m:
        lines += ["", "MARKET (DexScreener)"]
        lines.append(f"Price: {price(m['price_usd'])}")
        lines.append(f"Market cap: {usd(m['market_cap'])} | FDV: {usd(m['fdv'])} (provider-calculated)")
        lines.append(f"Liquidity: {usd(m['liquidity_usd'])} across {m['pair_count']} pair(s) (our sum)")
        lines.append(f"Volume 24h: {usd(m['volume_24h'])} (our sum)")
        if m["change_24h"] is not None:
            lines.append(f"Price change 24h: {m['change_24h']:+.1f}% (top pair)")
        if m["buys_24h"] is not None and m["sells_24h"] is not None:
            lines.append(f"Trades 24h: {int(m['buys_24h'])} buys / {int(m['sells_24h'])} sells (top pair)")
        pair_name = f"{report.symbol or '?'}/{m['quote_symbol'] or '?'} on {m['dex'] or '?'}"
        lines.append(f"Top pair: {pair_name}")
        if m["top_pair_url"]:
            lines.append(m["top_pair_url"])
        if m["oldest_pair_age_days"] is not None:
            lines.append(f"Oldest pair age: {m['oldest_pair_age_days']:.0f} day(s) (calculated)")
        if m["boosts"]:
            lines.append(f"Paid DexScreener boosts active: {m['boosts']} (promotion, not a quality signal)")

    if s and s.get("kind") == "solana":
        lines += _solana_contract_lines(s)
    elif s:
        lines += ["", "CONTRACT (GoPlus Security)"]
        lines.append(f"Open source: {yes_no(s['open_source'])}")
        honeypot = {True: "FLAGGED", False: "not flagged", None: "unknown"}[s["honeypot"]]
        lines.append(f"Honeypot check: {honeypot}")
        lines.append(f"Buy tax: {pct(None if s['buy_tax'] is None else s['buy_tax'] * 100)} | "
                     f"Sell tax: {pct(None if s['sell_tax'] is None else s['sell_tax'] * 100)}")
        owner = {None: "unknown", "none": "no owner / renounced (as reported)"}.get(s["owner"], s["owner"])
        lines.append(f"Owner: {owner}")
        for label, key in (
            ("Can mint new tokens", "mintable"), ("Upgradeable proxy", "proxy"),
            ("Owner can change taxes", "tax_modifiable"), ("Trading can be paused", "transfer_pausable"),
            ("Blacklist function", "blacklist"), ("Hidden owner", "hidden_owner"),
            ("Owner can reclaim ownership", "can_reclaim_ownership"),
            ("Owner can edit balances", "owner_can_edit_balances"),
        ):
            lines.append(f"{label}: {yes_no(s[key])}")
        if s["holder_count"] is not None:
            lines.append(f"Holders: {int(s['holder_count']):,}")
        if s["top_wallet_pct"] is not None:
            lines.append(f"Largest non-contract holder: {pct(s['top_wallet_pct'])}")
        if s["top10_pct"] is not None:
            lines.append(f"Top 10 holders together: {pct(s['top10_pct'])} (includes pools, contracts and burn addresses)")
        if s["creator_pct"] is not None:
            lines.append(f"Creator address holds: {pct(s['creator_pct'])}")
        if s["lp_locked_pct"] is not None:
            lines.append(f"Liquidity tokens in recognised lockers/burn: {pct(s['lp_locked_pct'])}")
        listed = []
        if s["trust_list"]:
            listed.append("on GoPlus's trusted list")
        if s["cex"]:
            listed.append("listed on " + ", ".join(s["cex"]))
        if s["launchpad"]:
            listed.append(f"launched via {s['launchpad']}")
        if listed:
            lines.append("GoPlus notes: " + "; ".join(listed) + " (provider-reported, not a safety guarantee)")
        if s["other_risks"]:
            lines.append(f"GoPlus other risks: {s['other_risks']}")
        if s["note"]:
            lines.append(f"GoPlus note: {s['note']}")

    lines += ["", "FLAGS (our rules of thumb applied to the data above; not a rating)"]
    if report.flags:
        order = {"high": 0, "medium": 1}
        for f in sorted(report.flags, key=lambda x: order[x.severity]):
            lines.append(f"{f.severity.upper()}: {f.text}")
    else:
        lines.append("No flags triggered. That is NOT a safety guarantee.")

    lines += ["", "UNKNOWN / NOT CHECKED"] + [f"- {item}" for item in report.missing]
    if report.errors:
        lines += ["", "PROBLEMS DURING THE CHECK (results may be incomplete)"] + [f"- {e}" for e in report.errors]
    if report.websites or report.socials:
        lines += ["", "Links the project lists on DexScreener (unverified, treat with care)"]
        lines += [f"- {w}" for w in report.websites] + [f"- {x}" for x in report.socials]
    lines += ["", DISCLAIMER, ATTRIBUTION]
    return "\n".join(lines)


def split_message(text: str, limit: int = 3900) -> list[str]:
    """Splits long text on line breaks so each part fits in one Telegram message."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts
