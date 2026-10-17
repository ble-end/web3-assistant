"""Sample provider replies shaped like the official DexScreener and GoPlus documentation."""
from datetime import datetime, timezone

ADDR = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
DAY_MS = 86_400_000


def pair(address=ADDR, liquidity=250_000.0, created_days_ago=30, name="Moon Token", symbol="MOON", **over):
    created = int(NOW.timestamp() * 1000 - created_days_ago * DAY_MS)
    base = {
        "chainId": "base",
        "dexId": "uniswap",
        "url": "https://dexscreener.com/base/0xpair",
        "pairAddress": "0xpair",
        "baseToken": {"address": address, "name": name, "symbol": symbol},
        "quoteToken": {"address": "0xweth", "name": "Wrapped Ether", "symbol": "WETH"},
        "priceNative": "0.0000001",
        "priceUsd": "0.000123",
        "txns": {"h24": {"buys": 340, "sells": 280}},
        "volume": {"h24": 120_000.0},
        "priceChange": {"h24": -12.3},
        "liquidity": {"usd": liquidity, "base": 1, "quote": 1},
        "fdv": 3_400_000.0,
        "marketCap": 1_200_000.0,
        "pairCreatedAt": created,
        "info": {
            "websites": [{"url": "https://moon.example"}],
            "socials": [{"platform": "twitter", "handle": "moontoken"}],
        },
        "boosts": {"active": 0},
    }
    base.update(over)
    return base


def goplus(**over):
    """A tidy open-source token with nothing alarming reported."""
    data = {
        "token_name": "Moon Token",
        "token_symbol": "MOON",
        "total_supply": "1000000000",
        "is_open_source": "1",
        "is_proxy": "0",
        "is_mintable": "0",
        "owner_address": "",
        "can_take_back_ownership": "0",
        "owner_change_balance": "0",
        "hidden_owner": "0",
        "selfdestruct": "0",
        "is_in_dex": "1",
        "buy_tax": "0",
        "sell_tax": "0",
        "cannot_buy": "0",
        "cannot_sell_all": "0",
        "slippage_modifiable": "0",
        "personal_slippage_modifiable": "0",
        "is_honeypot": "0",
        "transfer_pausable": "0",
        "is_blacklisted": "0",
        "holder_count": "4342",
        "creator_percent": "0.01",
        "holders": [
            {"address": "0x" + "11" * 20, "tag": "Uniswap pool", "is_contract": "1", "percent": "0.30", "is_locked": "0"},
            {"address": "0x" + "22" * 20, "tag": "", "is_contract": "0", "percent": "0.03", "is_locked": "0"},
            {"address": "0x000000000000000000000000000000000000dead", "tag": "Burn Address", "is_contract": "0", "percent": "0.10", "is_locked": "1"},
        ],
        "lp_holders": [
            {"address": "0x" + "33" * 20, "tag": "UNCX", "is_contract": "1", "percent": "0.9", "is_locked": "1"},
            {"address": "0x" + "44" * 20, "tag": "", "is_contract": "0", "percent": "0.1", "is_locked": "0"},
        ],
    }
    data.update(over)
    return data


SOL_ADDR = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # example-shaped Solana mint address


def sol_pair(liquidity=250_000.0, name="Sol Moon", symbol="SMOON", **over):
    return pair(address=SOL_ADDR, liquidity=liquidity, name=name, symbol=symbol, chainId="solana", **over)


def _off():
    return {"status": "0", "authority": []}


def goplus_solana(**over):
    """A tidy Solana token with no authorities and nothing alarming reported."""
    data = {
        "metadata": {"name": "Sol Moon", "symbol": "SMOON", "description": "x", "uri": "https://example.com/m.json"},
        "total_supply": "1000000000",
        "default_account_state": "1",
        "non_transferable": "0",
        "trusted_token": 0,
        "mintable": _off(),
        "freezable": _off(),
        "closable": _off(),
        "balance_mutable_authority": _off(),
        "metadata_mutable": {"status": "0", "metadata_upgrade_authority": []},
        "transfer_fee_upgradable": _off(),
        "transfer_hook_upgradable": _off(),
        "default_account_state_upgradable": _off(),
        "transfer_fee": {},
        "transfer_hook": [],
        "creator": [{"address": "CreatorAddr111111111111111111111111111111", "malicious_address": 0}],
        "holders": [
            {"token_account": "PoolVault1", "tag": "Raydium Pool", "balance": "300000000", "percent": "0.3", "is_locked": 0},
            {"token_account": "Wallet1", "tag": "", "balance": "30000000", "percent": "0.03", "is_locked": 0},
        ],
        "dex": [
            {"dexname": "Raydium", "type": "standard", "id": "poolid", "tvl": "250000", "lp_amount": "1000",
             "lp_holders": [
                 {"token_account": "lp1", "tag": "", "balance": "900", "percent": "0.9", "is_locked": 1},
                 {"token_account": "lp2", "tag": "", "balance": "100", "percent": "0.1", "is_locked": 0},
             ]},
        ],
    }
    data.update(over)
    return data
