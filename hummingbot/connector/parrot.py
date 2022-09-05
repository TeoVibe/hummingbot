import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List

import aiohttp

from hummingbot.core.utils.async_utils import safe_gather

PARROT_MINER_BASE_URL = "https://api.hummingbot.io/bounty/"

s_decimal_0 = Decimal("0")


@dataclass
class CampaignSummary:
    market_id: int = 0
    trading_pair: str = ""
    exchange_name: str = ""
    spread_max: Decimal = s_decimal_0
    payout_asset: str = ""
    liquidity: Decimal = s_decimal_0
    liquidity_usd: Decimal = s_decimal_0
    active_bots: int = 0
    reward_per_wk: Decimal = s_decimal_0
    apy: Decimal = s_decimal_0


def logger():
    return logging.getLogger(__name__)


async def get_campaign_summary(exchange: str, trading_pairs: List[str] = []) -> Dict[str, CampaignSummary]:
    results = {}
    try:
        campaigns = await get_active_campaigns(exchange, trading_pairs)
        tasks = [get_market_snapshots(m_id) for m_id in campaigns]
        snapshots = await safe_gather(*tasks, return_exceptions=True)
        for snapshot in snapshots:
            if isinstance(snapshot, Exception):
                raise snapshot
            if snapshot['status'] != "success":
                logger().warning(f"Snapshot info for {trading_pairs} is not available",
                                 "verify that this is a valid campaign pair for this exchange")
                continue
            if snapshot["market_snapshot"]:
                snapshot = snapshot["market_snapshot"]
                market_id = int(snapshot["market_id"])
                campaign = campaigns[market_id]
                campaign.apy = Decimal(snapshot["annualized_return"])
                oov = snapshot["summary_stats"]["open_volume"]
                campaign.liquidity = Decimal(oov["oov_eligible_ask"]) + Decimal(oov["oov_eligible_bid"])
                campaign.liquidity_usd = campaign.liquidity * Decimal(oov["base_asset_usd_rate"])
                campaign.active_bots = int(oov["bots"])
        results = {c.trading_pair: c for c in campaigns.values()}
    except asyncio.CancelledError:
        raise
    except Exception:
        logger().error("Unexpected error while requesting data from Hummingbot API.", exc_info=True)
    return results


async def get_market_snapshots(market_id: int, timestamp: int = 1657747860000):
    async with aiohttp.ClientSession() as client:
        url = f"{PARROT_MINER_BASE_URL}user/single_snapshot?client_id=00000000000000000000000000000000000000000&market_id={market_id}&timestamp={timestamp}&aggregate_period=60"
        resp = await client.get(url)
        resp_json = await resp.json()
    return resp_json


async def get_active_campaigns(exchange: str, trading_pairs=None) -> Dict[int, CampaignSummary]:
    if trading_pairs is None:
        trading_pairs = []

    campaigns = {}
    async with aiohttp.ClientSession() as client:
        campaigns_url = f"{PARROT_MINER_BASE_URL}campaigns"
        resp = await client.get(campaigns_url)
        resp_json = await resp.json()

    if not resp_json or resp_json['status'] == "error":
        logger().warning("Could not get active campaigns from Hummingbot API"
                         f" (returned response '{resp_json}').")
    else:
        for campaign_retval in resp_json["campaigns"]:
            for market in campaign_retval["markets"]:
                if not are_same_entity(exchange, market["exchange_name"]):
                    continue
                t_pair = f"{market['base_asset']}-{market['quote_asset']}"
                if trading_pairs and t_pair not in trading_pairs:
                    continue
                campaign = CampaignSummary()
                campaign.market_id = int(market["market_id"])
                campaign.trading_pair = t_pair
                campaign.exchange_name = exchange
                campaigns[campaign.market_id] = campaign

        campaigns = await get_active_markets(campaigns)

    return campaigns


async def get_active_markets(campaigns: Dict[int, CampaignSummary]) -> Dict[int, CampaignSummary]:
    async with aiohttp.ClientSession() as client:
        markets_url = f"{PARROT_MINER_BASE_URL}markets"
        resp = await client.get(markets_url)
        resp_json = await resp.json()

    if not resp_json or resp_json['status'] == "error":
        logger().warning("Could not get active markets from Hummingbot API"
                         f" (returned response '{resp_json}').")
    else:
        for markets_retval in resp_json["markets"]:
            market_id = int(markets_retval["market_id"])
            if market_id in campaigns:
                campaigns[market_id].active_bots = markets_retval["bots"]
                for bounty_period in markets_retval["active_bounty_periods"]:
                    campaigns[market_id].reward_per_wk = Decimal(str(bounty_period["budget"]["bid"])) + Decimal(str(bounty_period["budget"]["ask"]))
                    campaigns[market_id].spread_max = Decimal(str(bounty_period["spread_max"])) / Decimal("100")
                    campaigns[market_id].payout_asset = bounty_period["payout_asset"]

    return campaigns


def are_same_entity(hb_exchange_name: str, parrot_exchange_name: str) -> bool:
    return hb_exchange_name.replace("_", "") == parrot_exchange_name.replace("_", "")
