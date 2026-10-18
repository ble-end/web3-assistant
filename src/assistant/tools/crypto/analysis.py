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
    "Research only, not financial advice. Having no flags does NOT mean a token is safe."
)
ATTRIBUTION = "Market data: DexScreener. Security: GoPlus Security."


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
    severity: str
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
    other_chains: list = field(default_factory=list)
    websites: list = field(default_factory=list)
    socials: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.market is not None or self.security is not None


def base_pairs(address: str, pairs: list[dict]) -> list[dict]:
    return [p for p in pairs if str(_dig(p, "baseToken", "address") or "").lower() == address.lower()]


def _liquidity(pair: dict) -> float | None:
    return num(_dig(pair, "liquidity", "usd"))


def _has_data(security) -> bool:
    return isinstance(security, dict) and any(
        security.get(k) not in (None, "", {}, [])
        for k in ("token_name", "token_symbol", "total_supply", "is_open_source", "metadata", "mintable")
    )


def choose_primary_chain(address: str, pairs_by_chain: dict[str, list[dict]]) -> str | None:
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
    age_hours = None
    if created:
        age_hours = max((now.timestamp() * 1000 - min(created)) / 3_600_000, 0.0)
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
        "age_hours": age_hours,
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
    owner_raw = raw.get("owner_address")
    if owner_raw is None:
        owner = None
    elif str(owner_raw).strip() == "" or str(owner_raw).lower() in BURN_ADDRESSES:
        owner = "none"
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
    wallets = [
        h["percent"]
        for h in top_holders
        if h["percent"] is not None
        and h["is_contract"] is not True
        and h["address"] not in BURN_ADDRESSES
        and "burn" not in h["tag"].lower()
    ]
    return {
        "kind": "evm",
        "name": clean_text(raw.get("token_name"), 40),
        "symbol": clean_text(raw.get("token_symbol"), 20),
        "open_source": flag(raw, "is_open_source"),
        "honeypot": flag(raw, "is_honeypot"),
        "buy_tax": num(raw.get("buy_tax")),
        "sell_tax": num(raw.get("sell_tax")),
        "holder_count": num(raw.get("holder_count")),
        "top_wallet_pct": max(wallets) if wallets else None,
        "owner": owner,
    }


def parse_security_solana(raw: dict) -> dict:
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    total = num(raw.get("total_supply"))
    holders = []
    for h in (_entries(raw.get("holders"))):
        balance = num(h.get("balance"))
        share = None
        if total and total > 0 and balance is not None:
            share = balance / total * 100
            if share > 100.0001:
                share = None
        holders.append(
            {"tag": clean_text(h.get("tag"), 30), "share": share, "locked": flag(h, "is_locked")}
        )
    wallets = [
        h["share"] for h in holders
        if h["share"] is not None and h["locked"] is not True
    ]
    return {
        "kind": "solana",
        "name": clean_text(meta.get("name"), 40),
        "symbol": clean_text(meta.get("symbol"), 20),
        "mintable": _status(raw, "mintable"),
        "freezable": _status(raw, "freezable"),
        "top_wallet_pct": max(wallets) if wallets else None,
    }


def _status(raw: dict, key: str) -> bool | None:
    value = raw.get(key)
    return flag(value, "status") if isinstance(value, dict) else flag(raw, key)


def _entries(value) -> list[dict]:
    if isinstance(value, dict):
        value = [value]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def compute_flags(market: dict | None, sec: dict | None) -> list[Flag]:
    flags: list[Flag] = []
    if market:
        liq = market.get("liquidity_usd")
        if liq is not None and liq < 10_000:
            flags.append(Flag("high", f"Thin liquidity ({usd(liq)})."))
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

    mine = sorted(base_pairs(address, pairs), key=lambda p: -(_liquidity(p) or 0.0))
    top_name = clean_text(_dig(mine[0], "baseToken", "name"), 40) if mine else ""
    top_symbol = clean_text(_dig(mine[0], "baseToken", "symbol"), 20) if mine else ""
    report.name = top_name or (report.security or {}).get("name", "")
    report.symbol = top_symbol or (report.security or {}).get("symbol", "")
    report.flags = compute_flags(report.market, report.security)
    return report


def format_report(report: TokenReport) -> str:
    if not report.found:
        return f"Token not found for contract: {report.address}"

    m, s = report.market, report.security
    name = report.name or 'Unknown'
    symbol = report.symbol or '?'
    
    # Clean compact header style
    lines = [f"🪙 {name} ({symbol})"]
    
    if m:
        age_str = f"{m['age_hours']:.0f}h" if m['age_hours'] is not None else "?"
        change_str = f"({m['change_24h']:+.0f}%)" if m['change_24h'] is not None else ""
        lines.append(f" └ 🌐 {m['dex'] or 'DEX'} | 🌱 {age_str}")
        lines.append("")
        lines.append("📊 Stats")
        lines.append(f" ├ USD   {price(m['price_usd'])} {change_str}")
        lines.append(f" ├ MC    {usd(m['market_cap'])}")
        lines.append(f" ├ Vol   {usd(m['volume_24h'])}")
        lines.append(f" ├ LP    {usd(m['liquidity_usd'])}")
        if m['top_pair_url']:
            lines.append(f" └ Link  {m['top_pair_url']}")

    if s:
        lines.append("")
        lines.append("🔒 Security")
        if s.get("kind") == "solana":
            lines.append(f" ├ Mintable: {yes_no(s.get('mintable'))}")
            lines.append(f" └ Freezable: {yes_no(s.get('freezable'))}")
        else:
            honeypot_status = {True: "YES", False: "No", None: "Unknown"}.get(s.get("honeypot"))
            lines.append(f" ├ Honeypot: {honeypot_status}")
            lines.append(f" ├ Open Source: {yes_no(s.get('open_source'))}")
            lines.append(f" └ Owner: {s.get('owner', 'unknown')}")

    if report.socials or report.websites:
        lines.append("")
        lines.append("🌐 Socials & Links")
        for web in report.websites:
            lines.append(f" ├ Web: {web}")
        for soc in report.socials:
            lines.append(f" └ {soc}")

    lines.append("")
    lines.append(f"Contract: {report.address}")
    return "\n".join(lines)


def split_message(text: str, limit: int = 3900) -> list[str]:
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