#!/usr/bin/env python3
"""Stage 6.0 — pool metadata + tokenId linkage (keyless RPC).

Two jobs, both needed before the Stage 6 virtual order book can be built:

1. Pool metadata -> pool_metadata.csv
   Read the pool contract once: fee() (hundredths of a bip -> gamma = fee/1e6), tickSpacing(),
   token0()/token1() and each token's decimals()/symbol(). One row, the single source of truth
   for decimals/symbols/gamma/tickSpacing that the rest of the pipeline threads through.

2. tokenId linkage -> mints_linked.csv, burns_linked.csv, positions.csv
   The pool's Mint/Burn events carry owner = NonfungiblePositionManager (NFPM) for every
   NFT-routed LP, so the pool keys positions by (owner, tickLower, tickUpper) and can't tell two
   LPs apart -> not true L3. But the NFPM emits IncreaseLiquidity / DecreaseLiquidity with an
   indexed `tokenId` in the SAME transaction as each pool Mint/Burn. We pull each mint/burn tx's
   receipt, find that NFPM log, and read the tokenId (cross-checked by liquidity == amount). The
   tokenId is the stable per-position identity and links every Add to its later Removes.

   Fallback: a direct-to-pool mint (owner != NFPM) has no tokenId; identity falls back to the
   owner address + range (which the pool already keeps distinct).

Usage:
  python link_positions.py                       # default ETH/USDC 0.3% pool
  python link_positions.py --pool 0x... --rpc https://...
"""
import argparse
import csv
import os

from web3 import Web3

from usage import UsageCounter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RPC = "https://ethereum-rpc.publicnode.com"
DEFAULT_POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
NFPM = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"  # NonfungiblePositionManager (mainnet)

POOL_META_ABI = [
    {"name": n, "inputs": [], "outputs": [{"type": t}], "stateMutability": "view", "type": "function"}
    for n, t in (("token0", "address"), ("token1", "address"), ("fee", "uint24"),
                 ("tickSpacing", "int24"))
]
ERC20_ABI = [
    {"name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"name": "symbol", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]


def _topic(sig):
    return _norm_hex(Web3.keccak(text=sig).hex())


def _norm_hex(h):
    """Lower-case, 0x-prefixed hex string (HexBytes.hex() may or may not include 0x)."""
    h = h.hex() if hasattr(h, "hex") else str(h)
    h = h.lower()
    return h if h.startswith("0x") else "0x" + h


INC_TOPIC = _topic("IncreaseLiquidity(uint256,uint128,uint256,uint256)")
DEC_TOPIC = _topic("DecreaseLiquidity(uint256,uint128,uint256,uint256)")


# --- pure functions (unit-tested) --------------------------------------------

def gamma_from_fee(fee):
    """Pool fee (hundredths of a bip, e.g. 3000) -> swap fee rate gamma (e.g. 0.003)."""
    return int(fee) / 1_000_000


def classify_nfpm_log(address, topic0, token_id_topic, data, *, nfpm=NFPM):
    """Classify one raw receipt log; return (kind, tokenId, liquidity) or None.

    `kind` is "increase" or "decrease". A log qualifies only if it was emitted by the NFPM and
    carries an Increase/DecreaseLiquidity topic. tokenId is the indexed topic; liquidity is the
    first 32-byte word of the data (both events: liquidity, amount0, amount1).
    """
    if address.lower() != nfpm.lower():
        return None
    t0 = _norm_hex(topic0)
    if t0 == INC_TOPIC:
        kind = "increase"
    elif t0 == DEC_TOPIC:
        kind = "decrease"
    else:
        return None
    token_id = int(_norm_hex(token_id_topic), 16)
    body = _norm_hex(data)[2:]
    liquidity = int(body[:64], 16) if body else 0
    return (kind, token_id, liquidity)


def match_tokenid(nfpm_events, kind, amount):
    """tokenId of the NFPM event of `kind` whose liquidity == amount, else None.

    The pool Mint/Burn `amount` (uint128 liquidity) equals the NFPM event's `liquidity` exactly,
    so an exact match is the reliable join key — it also disambiguates a tx with several mints.
    A pure collect / poke that emits no Increase/Decrease simply yields None (no liquidity change
    to attribute); the row's range is still known from the pool event.
    """
    for ev in nfpm_events:
        if ev["kind"] == kind and ev["liquidity"] == int(amount):
            return ev["tokenId"]
    return None


def identity_of(token_id, owner, lo, up):
    """Stable position identity: tokenId when linked, else owner+range (pool's own key)."""
    return str(token_id) if token_id is not None else f"{owner}:{lo}:{up}"


def build_positions(mint_rows, burn_rows):
    """Per-position summary from linked mint/burn rows (pure).

    Groups by `identity`, nets liquidity (+ mints, - burns), records first mint time and the
    add/remove counts that make each Add<->Remove linkage explicit. Zero-amount events (pure
    collect / fee 'poke' burns) change no liquidity and carry no DecreaseLiquidity to link, so
    they are skipped here rather than forming phantom net-zero positions.
    """
    pos = {}

    def touch(r, sign):
        if int(r["amount"]) == 0:
            return
        ident = r["identity"]
        p = pos.setdefault(ident, {
            "tokenId": r.get("tokenId", ""), "identity": ident,
            "tickLower": int(r["tickLower"]), "tickUpper": int(r["tickUpper"]),
            "first_mint_ts": None, "net_L_in_window": 0, "add_count": 0, "remove_count": 0})
        p["net_L_in_window"] += sign * int(r["amount"])
        if sign > 0:
            p["add_count"] += 1
            ts = int(r["timestamp"])
            p["first_mint_ts"] = ts if p["first_mint_ts"] is None else min(p["first_mint_ts"], ts)
        else:
            p["remove_count"] += 1

    for r in mint_rows:
        touch(r, +1)
    for r in burn_rows:
        touch(r, -1)
    return sorted(pos.values(),
                  key=lambda p: (p["first_mint_ts"] is None, p["first_mint_ts"] or 0, p["identity"]))


# --- io ----------------------------------------------------------------------

def read_csv(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(name, rows, fieldnames):
    with open(os.path.join(HERE, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):>4} rows -> {name}")


# --- network -----------------------------------------------------------------

def fetch_pool_metadata(w3, pool):
    pc = w3.eth.contract(address=pool, abi=POOL_META_ABI)
    t0a, t1a = pc.functions.token0().call(), pc.functions.token1().call()
    fee, spacing = pc.functions.fee().call(), pc.functions.tickSpacing().call()
    t0 = w3.eth.contract(address=t0a, abi=ERC20_ABI)
    t1 = w3.eth.contract(address=t1a, abi=ERC20_ABI)
    return {"pool": pool, "token0": t0a, "token1": t1a,
            "symbol0": t0.functions.symbol().call(), "symbol1": t1.functions.symbol().call(),
            "decimals0": t0.functions.decimals().call(), "decimals1": t1.functions.decimals().call(),
            "fee": fee, "gamma": gamma_from_fee(fee), "tickSpacing": spacing}


def parse_receipt_logs(logs):
    """[{kind, tokenId, liquidity}] for the NFPM Increase/Decrease logs in one tx receipt."""
    out = []
    for lg in logs:
        topics = lg["topics"]
        if not topics:
            continue
        tid_topic = topics[1] if len(topics) > 1 else "0x0"
        res = classify_nfpm_log(lg["address"], topics[0], tid_topic, lg["data"])
        if res:
            kind, token_id, liquidity = res
            out.append({"kind": kind, "tokenId": token_id, "liquidity": liquidity})
    return out


def link_rows(w3, rows, kind):
    """Annotate each pool event row with tokenId/identity via its tx receipt's NFPM log."""
    out, linked = [], 0
    for r in rows:
        tx = r["tx"] if r["tx"].startswith("0x") else "0x" + r["tx"]
        evs = parse_receipt_logs(w3.eth.get_transaction_receipt(tx)["logs"])
        tid = match_tokenid(evs, kind, r["amount"])
        r = dict(r)
        r["tokenId"] = "" if tid is None else str(tid)
        r["identity"] = identity_of(tid, r.get("owner", ""), r["tickLower"], r["tickUpper"])
        linked += tid is not None
        out.append(r)
    print(f"  linked {linked}/{len(rows)} {kind} rows to a tokenId")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    args = ap.parse_args()

    mints, burns = read_csv("mints.csv"), read_csv("burns.csv")
    if not mints and not burns:
        raise SystemExit("No mints/burns — run Stage 1 (uniswap_v3_pool_download_rpc.py) first.")

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        raise SystemExit(f"Could not reach RPC {args.rpc}")
    uc = UsageCounter().attach(w3)
    pool = Web3.to_checksum_address(args.pool)

    # 1) pool metadata --------------------------------------------------------
    meta = fetch_pool_metadata(w3, pool)
    print(f"pool {meta['symbol0']}/{meta['symbol1']}  fee={meta['fee']} (gamma={meta['gamma']})  "
          f"tickSpacing={meta['tickSpacing']}  decimals=({meta['decimals0']},{meta['decimals1']})")
    write_csv("pool_metadata.csv", [meta],
              ["pool", "token0", "token1", "symbol0", "symbol1",
               "decimals0", "decimals1", "fee", "gamma", "tickSpacing"])

    # 2) tokenId linkage ------------------------------------------------------
    mints_l = link_rows(w3, mints, "increase")
    burns_l = link_rows(w3, burns, "decrease")
    if mints_l:
        write_csv("mints_linked.csv", mints_l, list(mints[0].keys()) + ["tokenId", "identity"])
    if burns_l:
        write_csv("burns_linked.csv", burns_l, list(burns[0].keys()) + ["tokenId", "identity"])

    positions = build_positions(mints_l, burns_l)
    write_csv("positions.csv", positions,
              ["tokenId", "identity", "tickLower", "tickUpper", "first_mint_ts",
               "net_L_in_window", "add_count", "remove_count"])
    print(f"positions(distinct)={len(positions)}  "
          f"with-tokenId={sum(1 for p in positions if p['tokenId'])}")
    print(uc.summary())
    uc.dump(label=f"link_positions pool={args.pool}")
    print("Done.")


if __name__ == "__main__":
    main()
