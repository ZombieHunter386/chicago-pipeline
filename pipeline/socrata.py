# pipeline/socrata.py
from __future__ import annotations
import time
from typing import Iterator, Optional
import requests


class SocrataError(Exception):
    pass


class SocrataClient:
    """
    Minimal Socrata SODA REST client with pagination + retry + rate limiting.
    Works against both datacatalog.cookcountyil.gov and data.cityofchicago.org.
    """

    def __init__(
        self,
        domain: str,
        app_token: str = "",
        api_key_id: str = "",
        api_key_secret: str = "",
        max_retries: int = 5,
        retry_backoff: float = 1.0,
        rate_limit_sleep: float = 0.0,
        timeout: float = 60.0,
    ):
        self.domain = domain.rstrip("/")
        self.app_token = app_token
        self.api_key_id = api_key_id
        self.api_key_secret = api_key_secret
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.rate_limit_sleep = rate_limit_sleep
        self.timeout = timeout
        self.session = requests.Session()

    def fetch(
        self,
        dataset_id: str,
        where: Optional[str] = None,
        select: Optional[str] = None,
        order: Optional[str] = None,
        limit: int = 50000,
    ) -> Iterator[dict]:
        """Yield rows from a Socrata dataset, handling pagination."""
        url = f"https://{self.domain}/resource/{dataset_id}.json"
        offset = 0
        while True:
            params = {"$limit": limit, "$offset": offset}
            if where:
                params["$where"] = where
            if select:
                params["$select"] = select
            if order:
                params["$order"] = order

            page = self._get_with_retry(url, params)
            if not page:
                return
            for row in page:
                yield row
            if len(page) < limit:
                return
            offset += limit
            if self.rate_limit_sleep:
                time.sleep(self.rate_limit_sleep)

    def fetch_by_pins(
        self,
        dataset_id: str,
        pins,
        pin_field: str = "pin",
        chunk_size: int = 100,
        select: Optional[str] = None,
        order: Optional[str] = None,
        where: Optional[str] = None,
        limit: int = 50000,
    ) -> Iterator[dict]:
        """Yield rows whose pin_field matches any given pin, chunked to keep
        $where clause size safe. Intended for datasets without lat/lng that
        would otherwise require a full-dataset scan."""
        pins = list(pins)
        for i in range(0, len(pins), chunk_size):
            batch = pins[i:i + chunk_size]
            quoted = ",".join(f"'{p}'" for p in batch)
            pin_where = f"{pin_field} in ({quoted})"
            combined = f"({where}) AND ({pin_where})" if where else pin_where
            yield from self.fetch(
                dataset_id,
                where=combined,
                select=select,
                order=order,
                limit=limit,
            )

    def _get_with_retry(self, url: str, params: dict) -> list[dict]:
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        auth = (self.api_key_id, self.api_key_secret) if self.api_key_id and self.api_key_secret else None
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, headers=headers, auth=auth, timeout=self.timeout)
                if resp.status_code == 429:
                    sleep_for = self.retry_backoff * (2 ** attempt)
                    time.sleep(sleep_for)
                    continue
                if 500 <= resp.status_code < 600:
                    sleep_for = self.retry_backoff * (2 ** attempt)
                    time.sleep(sleep_for)
                    continue
                # 4xx (other than 429) are client errors — don't retry.
                if 400 <= resp.status_code < 500:
                    raise SocrataError(f"Client error {resp.status_code}: {url} {params} {resp.text[:200]}")
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(self.retry_backoff * (2 ** attempt))
        raise SocrataError(f"Failed after {self.max_retries} retries: {url} {params} ({last_err})")
