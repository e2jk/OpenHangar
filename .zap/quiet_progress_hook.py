# Loaded by ZAP's scan script (--hook, zap-full-scan.yml). With -d it logs
# "Spider/Active Scan progress %" — and every HTTP poll behind it; keep the
# progress, drop the HTTP library's per-request lines.
import logging

logging.getLogger("urllib3").setLevel(logging.WARNING)
