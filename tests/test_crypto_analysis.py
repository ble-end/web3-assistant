from datetime import timedelta

from assistant.tools.crypto.analysis import (
    build_report, choose_primary_chain, clean_text, clean_url, format_report, split_message,
)
from assistant.tools.crypto.chains import address_kind, chains_for, find_address, normalize_address, resolve_chain
from crypto_samples import ADDR, NOW, OTHER, SOL_ADDR, goplus, goplus_solana, pair, sol_pair


def report(pairs=None, security=None, chain="base", errors=None):
    pairs_by_chain = {chain: pairs if pairs is not None else [pair()]}
    return build_report(ADDR, NOW, pairs_by_chain, {chain: security}, errors)


def flag_texts(r, severity=None):
    return [f.text for f in r.flags if severity is None or f.severity == severity]


# ------------------------------------------------------------ addresses


def test_address_helpers():
    assert normalize_address(ADDR.upper().replace("0X", "0x")) == ADDR
    assert normalize_address("0x123") is None
    assert normalize_address(ADDR + "00") is None
    assert find_address(f"check {ADDR} please") == ADDR
    assert find_address("0x" + "a" * 64) is None  # a 64-character hash is not an address
    assert find_address("no address here") is None


def test_chain_aliases():
    assert resolve_chain("BNB") == "bsc" and resolve_chain("eth") == "ethereum"
    assert resolve_chain("SOL") == "solana" and resolve_chain("polygon") is None


# -------------------------------------------------------------- the report


def test_healthy_token_has_no_flags_but_is_never_called_safe():
    r = report(security=goplus())
    text = format_report(r)
    assert r.flags == []
    assert "Moon Token (MOON) on Base" in text
    assert "No flags triggered. That is NOT a safety guarantee." in text
    assert "not financial advice" in text.lower()
    assert "GoPlus Security" in text and "DexScreener" in text
    assert "UTC" in text
    for banned in ("safe to buy", "no risk", "guaranteed", "looks safe", "appears safe"):
        assert banned not in text.lower()


def test_market_numbers_are_shown():
    text = format_report(report(security=goplus()))
    assert "$250.00K" in text and "$120.00K" in text and "$0.000123" in text
    assert "340 buys / 280 sells" in text and "-12.3%" in text


def test_honeypot_and_taxes_and_hidden_owner_are_high_flags():
    r = report(security=goplus(is_honeypot="1", sell_tax="1", hidden_owner="1"))
    high = " ".join(flag_texts(r, "high"))
    assert "honeypot" in high and "Sell tax is reported at 100%" in high and "hidden owner" in high
    assert "test trade failed" in high


def test_closed_source_is_high_flag():
    r = report(security=goplus(is_open_source="0"))
    assert any("not open source" in t for t in flag_texts(r, "high"))


def test_thin_liquidity_flags():
    assert any("Very thin" in t for t in flag_texts(report([pair(liquidity=5_000)], goplus()), "high"))
    assert any("Low liquidity" in t for t in flag_texts(report([pair(liquidity=30_000)], goplus()), "medium"))


def test_new_pair_flag():
    r = report([pair(created_days_ago=0.2)], goplus())
    assert any("less than a day old" in t for t in flag_texts(r))


def test_owner_privileges_are_medium_flags():
    r = report(security=goplus(is_mintable="1", transfer_pausable="1", owner_address="0x" + "99" * 20))
    medium = " ".join(flag_texts(r, "medium"))
    assert "mint" in medium and "paused" in medium and "active owner" in medium


def test_burn_address_owner_counts_as_renounced():
    text = format_report(report(security=goplus(owner_address="0x000000000000000000000000000000000000dead")))
    assert "renounced" in text


def test_concentrated_holder_and_creator_flags():
    holders = [{"address": "0x" + "55" * 20, "tag": "", "is_contract": "0", "percent": "0.35", "is_locked": "0"}]
    r = report(security=goplus(holders=holders, creator_percent="0.25", holder_count="40"))
    high = " ".join(flag_texts(r, "high"))
    assert "35.0%" in high and "creator" in high.lower()
    assert any("Only 40 holders" in t for t in flag_texts(r))


def test_contract_and_burn_holders_do_not_count_as_a_whale_wallet():
    r = report(security=goplus())  # a 30% pool contract and a 10% burn address are present
    assert not any("non-contract wallet" in t for t in flag_texts(r))


def test_unlocked_liquidity_is_flagged_with_a_caveat():
    lp = [{"address": "0x" + "44" * 20, "tag": "", "is_contract": "0", "percent": "1", "is_locked": "0"}]
    r = report(security=goplus(lp_holders=lp))
    assert any("can understate" in t for t in flag_texts(r, "medium"))


def test_missing_fields_are_unknown_not_safe():
    sparse = {"token_name": "X", "token_symbol": "X", "is_open_source": "1"}
    r = report(security=sparse)
    text = format_report(r)
    assert "UNKNOWN (not safe)" in " ".join(r.missing)
    assert "Honeypot check: unknown" in text and "Can mint new tokens: unknown" in text
    assert "Honeypot check: not flagged" not in text


def test_no_security_data_is_reported_as_missing():
    r = report(security=None)
    assert r.security is None and any("All contract security checks" in m for m in r.missing)
    assert "CONTRACT (GoPlus" not in format_report(r)


def test_security_only_when_no_trading_pairs():
    r = build_report(ADDR, NOW, {"base": []}, {"base": goplus()})
    assert r.market is None and r.security is not None and r.chain.key == "base"
    assert any("Price, liquidity and volume" in m for m in r.missing)


def test_not_found_message_and_errors():
    r = build_report(ADDR, NOW, {"base": []}, {"base": None}, ["DexScreener took too long to answer"])
    text = format_report(r)
    assert not r.found and "could not find this contract" in text and "took too long" in text


def test_token_only_as_quote_side_has_no_price():
    quote_only = pair(address=OTHER)  # this token is not the base token of the pair
    r = build_report(ADDR, NOW, {"base": [quote_only]}, {"base": None})
    assert not r.found


def test_primary_chain_is_the_one_with_most_liquidity_and_others_are_listed():
    pairs = {"base": [pair(liquidity=10_000)], "bsc": [pair(liquidity=900_000)]}
    assert choose_primary_chain(ADDR, pairs) == "bsc"
    r = build_report(ADDR, NOW, pairs, {"bsc": goplus()})
    assert r.chain.key == "bsc"
    assert "Same address also trades on: Base" in format_report(r)


def test_liquidity_is_summed_across_pairs():
    r = report([pair(liquidity=100_000), pair(liquidity=50_000)], goplus())
    assert r.market["liquidity_usd"] == 150_000 and r.market["pair_count"] == 2


# ------------------------------------------------- untrusted provider text


def test_hostile_token_names_are_cleaned():
    evil = "Free\nGift\u202e <b>CLICK</b>\x00" + "A" * 200
    r = report([pair(name=evil, symbol="X\nY")], goplus())
    text = format_report(r)
    assert "\u202e" not in text and "\x00" not in text
    assert "Free Gift" in text and len(r.name) <= 40 and "\n" not in r.symbol


def test_clean_helpers():
    assert clean_text("a\n\n b\t c") == "a b c"
    assert clean_text(None) == ""
    assert clean_url("https://ok.example/x") == "https://ok.example/x"
    assert clean_url("javascript:alert(1)") is None
    assert clean_url("https://has space.example") is None


def test_hostile_links_are_dropped():
    bad = pair(info={"websites": [{"url": "javascript:alert(1)"}, {"url": "https://ok.example"}],
                     "socials": [{"platform": "x\ny", "handle": "h" * 200}]})
    r = report([bad], goplus())
    assert r.websites == ["https://ok.example"] and len(r.socials[0]) < 100


def test_garbage_provider_values_do_not_crash():
    junk = pair(priceUsd="abc", liquidity={"usd": "NaN"}, txns="x", volume=None, priceChange=[], pairCreatedAt="soon")
    r = report([junk], goplus(buy_tax="??", holders="nope", lp_holders=[None, 5]))
    assert "TOKEN REPORT" in format_report(r)


def test_split_message_keeps_every_part_under_the_limit():
    long_text = "\n".join(f"line {i} " + "x" * 50 for i in range(300))
    parts = split_message(long_text, 1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == long_text
    assert split_message("short") == ["short"]
    assert all(len(p) <= 100 for p in split_message("y" * 450, 100))


def test_pair_age_is_calculated_from_now():
    r = report([pair(created_days_ago=10)], goplus())
    assert 9.9 < r.market["oldest_pair_age_days"] < 10.1
    assert (NOW + timedelta(days=1)) > r.fetched_at


# ----------------------------------------------------------------- Solana


def sol_report(pairs=None, security=None):
    return build_report(SOL_ADDR, NOW, {"solana": pairs if pairs is not None else [sol_pair()]}, {"solana": security})


def test_solana_address_helpers_keep_case():
    assert address_kind(SOL_ADDR) == "solana" and address_kind(ADDR) == "evm"
    assert address_kind("hello") is None and address_kind("0x" + "a" * 64) is None
    assert normalize_address(SOL_ADDR) == SOL_ADDR
    assert find_address(f"look at {SOL_ADDR} thanks") == SOL_ADDR
    assert find_address("just some normal words in a sentence here") is None
    assert [c.key for c in chains_for(SOL_ADDR)] == ["solana"]
    assert [c.key for c in chains_for(ADDR)] == ["base", "bsc", "ethereum"]


def test_healthy_solana_token_has_no_flags_and_is_not_called_safe():
    r = sol_report(security=goplus_solana())
    text = format_report(r)
    assert r.chain.key == "solana" and r.flags == []
    assert "Sol Moon (SMOON) on Solana" in text and "Mint authority (can create tokens): no" in text
    assert "NOT a safety guarantee" in text and "Solana API is in beta" in text
    assert "looks safe" not in text.lower() and "safe to buy" not in text.lower()


def test_solana_dangerous_authorities_are_flagged():
    bad = goplus_solana(
        freezable={"status": "1", "authority": [{"address": "A", "malicious_address": 0}]},
        mintable={"status": "1", "authority": []},
        closable={"status": "1", "authority": []},
        balance_mutable_authority={"status": "1", "authority": []},
    )
    r = sol_report(security=bad)
    high = " ".join(flag_texts(r, "high"))
    assert "freeze authority" in high and "change users' token balances" in high and "close the token program" in high
    assert any("mint authority" in t for t in flag_texts(r, "medium"))


def test_trusted_solana_token_gets_softer_flags_with_a_caveat():
    r = sol_report(security=goplus_solana(trusted_token=1, mintable={"status": "1", "authority": []},
                                          freezable={"status": "1", "authority": []}))
    assert not flag_texts(r, "high")
    assert all("trusted token" in t for t in flag_texts(r))


def test_malicious_authority_is_high():
    sec = goplus_solana(mintable={"status": "1", "authority": [{"address": "A", "malicious_address": "1"}]})
    assert any("malicious" in t for t in flag_texts(sol_report(security=sec), "high"))


def test_solana_holder_concentration_is_calculated_and_pools_are_ignored():
    # the 30% holder is tagged as a pool, so only the untagged 35% wallet counts
    holders = [
        {"token_account": "P", "tag": "Raydium Pool", "balance": "300000000", "percent": "0.3", "is_locked": 0},
        {"token_account": "W", "tag": "", "balance": "350000000", "percent": "0.35", "is_locked": 0},
    ]
    r = sol_report(security=goplus_solana(holders=holders))
    assert r.security["top_wallet_pct"] == 35.0
    assert any("35.0%" in t and "calculated from balances" in t for t in flag_texts(r, "medium"))
    assert r.security["top10_pct"] == 65.0


def test_solana_holder_numbers_that_do_not_add_up_are_ignored():
    holders = [{"token_account": "W", "tag": "", "balance": "9999999999999", "percent": "0.9", "is_locked": 0}]
    r = sol_report(security=goplus_solana(holders=holders))
    assert r.security["top_wallet_pct"] is None and r.security["top10_pct"] is None
    assert not any("holds" in t for t in flag_texts(r))


def test_solana_transfer_fee_and_frozen_default():
    fee = lambda rate: {"current_fee_rate": {"fee_rate": str(rate), "maximum_fee": "1"}}  # noqa: E731
    assert any("15.0%" in t for t in flag_texts(sol_report(security=goplus_solana(transfer_fee=fee(1500))), "medium"))
    assert any("60.0%" in t for t in flag_texts(sol_report(security=goplus_solana(transfer_fee=fee(6000))), "high"))
    assert not flag_texts(sol_report(security=goplus_solana(transfer_fee=fee(200))))
    assert any("start frozen" in t for t in flag_texts(sol_report(security=goplus_solana(default_account_state="2")), "high"))


def test_solana_hook_and_mutable_metadata_flags():
    sec = goplus_solana(transfer_hook=[{"address": "H", "malicious_address": 0}],
                        metadata_mutable={"status": "1", "metadata_upgrade_authority": []})
    medium = " ".join(flag_texts(sol_report(security=sec), "medium"))
    assert "transfer hook" in medium and "name and image" in medium


def test_solana_missing_fields_are_unknown_not_safe():
    sparse = {"metadata": {"name": "X", "symbol": "X"}, "total_supply": "100"}
    r = sol_report(security=sparse)
    text = format_report(r)
    assert "UNKNOWN (not safe)" in " ".join(r.missing)
    assert "Mint authority (can create tokens): unknown" in text and "Freeze authority (can freeze accounts): unknown" in text


def test_solana_lp_lock_info_is_reported_without_guessing():
    text = format_report(sol_report(security=goplus_solana()))
    assert "marked locked by GoPlus: 1 of 2 listed" in text and "total value locked $250.00K" in text


def test_solana_hostile_metadata_is_cleaned():
    sec = goplus_solana(metadata={"name": "Free\nGift\u202e" + "A" * 200, "symbol": "X\nY"})
    r = sol_report([sol_pair(name="N" * 300)], sec)
    assert len(r.name) <= 40 and "\u202e" not in format_report(r)
