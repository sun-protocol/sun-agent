# kol_sdk.py
import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("sunagent-ext")

DEFAULT_TIMEOUT = 10  # 秒


class TweetHubClient:
    """
    轻量级 requests 封装，支持:
    1. 创建 KOL 列表
    2. 删除 KOL 列表
    3. 自动重试 + 超时
    4. 中文友好（ensure_ascii=False）
    """

    def __init__(self, base_url: str, agent_id: str = "hub", timeout: int = DEFAULT_TIMEOUT):
        """
        :param base_url: 不含尾巴 / ，例：http://127.0.0.1:8084/api/sun
        :param agent_id: 默认 agent_id
        :param timeout: 单次请求超时
        """
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.timeout = timeout
        self._session = requests.Session()

        # 重试策略：3 次、backoff、状态码 5xx/429
        retry = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"POST", "DELETE"},
        )
        self._session.mount("http://", HTTPAdapter(max_retries=retry))
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    # ---------- 内部方法 ----------
    def _request(self, method: str, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        # 自动注入 agent_id
        payload.setdefault("agent_id", self.agent_id)
        try:
            resp = self._session.request(
                method=method,
                url=url,
                json=payload,  # requests 会自动用 utf-8 编码
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except requests.exceptions.RequestException as e:
            logger.error(f"[KolClient] {method} {url} error: {e}")
            raise

    # ---------- 业务接口 ----------
    def create_kol(self, kol_list: List[str], agent_id: str | None = None) -> Dict[str, Any]:
        """
        批量创建/绑定 KOL
        :param kol_list: twitter_id 列表
        :param agent_id: 可选，不传使用实例默认值
        """
        payload = {"kol_list": kol_list, "agent_id": agent_id or self.agent_id}
        return self._request("POST", "/kol", payload)

    def delete_kol(self, kol_list: List[str], agent_id: str | None = None) -> Dict[str, Any]:
        """
        批量删除/解绑 KOL
        """
        payload = {"kol_list": kol_list, "agent_id": agent_id or self.agent_id}
        return self._request("DELETE", "/kol", payload)
