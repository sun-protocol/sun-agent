import asyncio
import json
import logging
import os
import traceback
from typing import (
    Any,
    Dict,
    List,
    Optional,
    cast,
)

import aiohttp

from ._constants import LOGGER_NAME
from .agents._http_utils import fetch_url

logger = logging.getLogger(LOGGER_NAME)


class SunPumpService:
    DEFAULT_ERROR = "Service is busy"

    def __init__(self, host):
        self._host = host
        self._sunpump = os.getenv("SUNPUMP_HOST")

    async def query_launch_token_status_by_user(self, username: str) -> str:
        """
        a tool to query the status of token launch job submited by user today.
        inputs:
            - username: string, name of who submited a token launch job
        outputs:
            - string, status of launch job if success or an error message

        valid job status:
            - CREATED: the token launch job has been finished successfully.
            - UPLOADED: the token launch job is processing, but not finish yet.
            - NONE: user has not submitted token launch job today.
        """
        uri = "/pump-ops/pump-backend/token/queryByTwitter"
        params: Dict[str, str] = {"tweetUsername": username}
        data = await self._request("GET", uri, params=params)
        if isinstance(data, str):
            return data
        if data is None or "status" not in data:
            return "NONE"
        return data["status"]

    async def can_launch_new_token(self, username: str) -> str:
        """
        check whether user can launch new token today.
        inputs:
            - username: string, name of tweet author's
        outputs:
            - string, whether user can launch a new token at this moment.
        """
        uri = "/pump-ops/pump-backend/token/verifyCanCreate"
        params: Dict[str, str] = {"tweetUsername": username}
        data = await self._request("GET", uri, params=params)
        if isinstance(data, str):
            return data if len(data) > 0 else "OK"
        return "Unexpected Error"

    async def launch_new_token(
        self, name: str, symbol: str, description: str, image: str, tweet_id: str, username: str
    ) -> str:
        """
        a tool to launch a new token for user.
        inputs:
            - name: string, name of token to launch
            - symbol: string, symbol of token to launch
            - description: string, description of token to launch
            - image: string, base64 encoded image of token
            - tweet_id: string, the ID of tweet that describes this job
            - username: string, name of who submits this job
        outputs:
            - string, the url of created token if success or an error message
        """
        uri = "/pump-ops/pump-backend/token/create"
        params: Dict[str, str] = {
            "name": name.lstrip("$"),
            "symbol": symbol.lstrip("$"),
            "description": description,
            "imageBase64": image,
            "tweetId": tweet_id,
            "tweetUsername": username,
            "tokenFlag": "TWITTER_GEN_TOKEN",
        }
        data = await self._request("POST", uri=uri, data=params)
        if isinstance(data, str):
            return data
        if data is not None and "contractAddress" in data:
            return f"Token url: {self._sunpump}/token/{data['contractAddress']}"
        return self.DEFAULT_ERROR

    async def query_latest_tokens(self, num_of_tokens: int = 1) -> str:
        """
        a tool to query top tokens of SunPump platform over the past 24 hours
        inputs:
            - num_of_tokens: int, numbers of token to return
        outputs:
            - string, json array of tokens if success or an error message

        description to fields:
        - tweetUsername: twitter username of who created this token
        - name: name of token
        - symbol: symbol of token
        """
        uri = "/pump-api/token/search"
        params: Dict[str, str] = {"size": str(num_of_tokens), "sort": "twitterLaunch:DESC"}
        data = await self._request("GET", uri=uri, params=params)
        if isinstance(data, str):
            return data
        if "tokens" in data and isinstance(data["tokens"], List):
            FILTER_FIELDS = [
                "tweetUsername",
                "name",
                "symbol",
                "tokenCreatedInstant",
            ]
            results: List[Dict[str, Any]] = []
            for token in data["tokens"]:
                result: Dict[str, Any] = {}
                for key, value in token.items():
                    if key not in FILTER_FIELDS:
                        continue
                    result[key] = value
                results.append(result)
            return json.dumps(results)
        return self.DEFAULT_ERROR

    async def query_surge_tokens(self, num_of_tokens: int = 1) -> str:
        """
        a tool to query top tokens of SunPump platform over the past 24 hours
        inputs:
            - num_of_tokens: int, numbers of token to return
        outputs:
            - string, json array of tokens if success or an error message

        description to fields:
        - name: name of token
        - symbol: symbol of token
        - contractAddress: address of token
        - swapPoolAddress: SunSwap pool address of token/TRX
        - description: the description of token
        - priceInTrx: the price of token in TRX
        - volume24Hr: transaction amount over the past 24 hours
        - priceChange24Hr: rate of change in percentage over the past 24 hours
        """
        uri = "/pump-api/token/search"
        params: Dict[str, str] = {"size": str(num_of_tokens), "sort": "priceChange24Hr:DESC"}
        data = await self._request("GET", uri=uri, params=params)
        if isinstance(data, str):
            return data
        if "tokens" in data and isinstance(data["tokens"], List):
            FILTER_FIELDS = [
                "name",
                "symbol",
                "contractAddress",
                "swapPoolAddress",
                "description",
                "priceInTrx",
                "volume24Hr",
                "priceChange24Hr",
            ]
            results: List[Dict[str, Any]] = []
            for token in data["tokens"]:
                result: Dict[str, Any] = {}
                for key, value in token.items():
                    if key not in FILTER_FIELDS:
                        continue
                    result[key] = value
                results.append(result)
            return json.dumps(results)
        return self.DEFAULT_ERROR

    async def query_transaction_summary_by_date(self, from_date: str, to_date: str) -> str:
        """
        a tool to query transaction summary of SunPump platform during [{from_date}, {to_date}]
        inputs:
            - from_date: string, a date string in "YYYY-mm-dd"
            - to_date: string, a date string in "YYYY-mm-dd"
        outputs:
            - string, json array of daily summaries if success or an error message

        description to fields:
        - date: date string in "YYYY-mm-dd"
        - tokenCreated: numbers of token created in SunPump
        - tokenLaunched: numbers of token launched in SunSwap
        - txVirtual: numbers of transactions in SunPump
        - txSwap: numbers of transactions in SunSwap(OTC)
        - volumeUsdVirtual: transaction amount in USD in SunPump
        - volumeUsdSwap: transaction amount in USD in SunSwap(OTC)
        - volumeTrxVirtual: transaction amount in TRX in SunPump
        - volumeTrxSwap: transaction amount in TRX in SunSwap(OTC)
        """
        uri = "/pump-api/sunAgent/queryTranSummary"
        params: Dict[str, str] = {
            "fromDate": from_date,
            "toDate": to_date,
            "tokenFlag": "TWITTER_GEN_TOKEN",
        }
        data = await self._request("GET", uri=uri, params=params)
        if isinstance(data, str):
            return data
        elif isinstance(data, List):
            FILTER_FIELDS = [
                "date",
                "tokenCreated",
                "tokenLaunched",
                "txVirtual",
                "txSwap",
                "volumeUsdVirtual",
                "volumeUsdSwap",
                "volumeTrxVirtual",
                "volumeTrxSwap",
            ]
            results: List[Dict[str, Any]] = []
            for daily in cast(List[Dict[str, Any]], data):
                result: Dict[str, Any] = {}
                for key, value in daily.items():
                    if key not in FILTER_FIELDS:
                        continue
                    result[key] = value
                results.append(result)
            return json.dumps(results)
        return self.DEFAULT_ERROR

    async def _request(
        self, method: str, uri: str, params: Dict[str, str] = None, data: Dict[str, str] = None
    ) -> Optional[Dict[str, Any] | str]:
        try:
            url = f"{self._host}{uri}"
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, params=params, json=data) as response:
                    # response.raise_for_status()
                    result = await response.json()
                    logger.info(f"{method} {url} response:{response}")
                    if "code" in result and result["code"] != 0:
                        return result["msg"] if "msg" in result else self.DEFAULT_ERROR
                    return result["data"]
        except aiohttp.ClientError as e:
            logger.error(f"Error {method} {url}: {e}")
            return self.DEFAULT_ERROR
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"Unexpected error {method} {url}: {e}")
            return self.DEFAULT_ERROR
