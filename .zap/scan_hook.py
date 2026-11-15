# Loaded by ZAP's scan script (--hook, zap-full-scan.yml). With -d it logs
# "Spider/Active Scan progress %" — and every HTTP poll behind it; keep the
# progress, drop the HTTP library's per-request lines.
# It also lets the workflow choose where the browser crawl (-j) starts and
# how long it runs: zap-full-scan.yml writes zap-client-spider.json into the
# mounted work folder; without that file the scan script's defaults apply
# (the target, and the -m minutes).
from __future__ import annotations

import json
import logging
from typing import Any

logging.getLogger("urllib3").setLevel(logging.WARNING)

CLIENT_SPIDER_SETTINGS = "/zap/wrk/zap-client-spider.json"


def zap_client_spider(
    zap: Any, target: str, max_time: int
) -> tuple[Any, str, int] | None:
    try:
        with open(CLIENT_SPIDER_SETTINGS, encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError:
        return None
    url = settings.get("url", target)
    minutes = settings.get("minutes", max_time)
    logging.getLogger(__name__).info("Client Spider from %s for %s min", url, minutes)
    return zap, url, minutes
