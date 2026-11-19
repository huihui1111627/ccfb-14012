"""Demo: run the acceptance traffic against gateways that share one monitor,
then serve the web UI so every association's negotiation trace and transfer
progress can be browsed at http://127.0.0.1:8080

Run:  python -m dicom_gw.demo [--http 8080]
"""

import argparse
import time

from . import acceptance
from .gateway import Monitor
from .web import serve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=8080)
    args = ap.parse_args()

    monitor = Monitor()
    print("running acceptance traffic (this takes a moment)...")
    report, cells, services, aborts, _ = acceptance.run_all(
        verbose=False, monitor=monitor)
    ok = sum(1 for c in cells if c["ok"])
    print("matrix: %d/%d combos OK; services: %s; aborts: %s" % (
        ok, len(cells),
        "all OK" if all(v["ok"] for v in services.values()) else "FAILURES",
        "all OK" if all(v["ok"] for v in aborts.values()) else "FAILURES"))
    serve(monitor, args.http)
    print("monitor UI: http://127.0.0.1:%d  (%d associations recorded)"
          % (args.http, len(monitor.list())))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
