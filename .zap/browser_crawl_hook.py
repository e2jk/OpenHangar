# Loaded by ZAP's baseline script (--hook, zap-browser-crawl.yml). Records
# the URLs ZAP knows before and after its browser-based crawler (the Client
# Spider, run after the traditional one with -j), so the workflow can list
# what only the browser found.
import logging

logging.getLogger("urllib3").setLevel(logging.WARNING)

_zap = None
_before: set[str] = set()


def _write(name: str, urls: set[str]) -> None:
    with open(f"/zap/wrk/{name}", "w") as out:
        out.writelines(f"{url}\n" for url in sorted(urls))


def zap_client_spider(zap, target, max_time):  # before the browser crawl
    global _zap
    _zap = zap
    _before.update(zap.core.urls())
    _write("crawl-traditional.txt", _before)


def zap_client_spider_wrap(return_data):  # after it
    if _zap is not None:
        _write("crawl-browser-only.txt", set(_zap.core.urls()) - _before)
    return return_data
