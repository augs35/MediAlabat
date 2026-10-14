"""
Pretends to be the ESP32 + SIM800L gateway, so you can test the MediAlabat side before wiring any hardware.

    python tools/fake_gateway.py --key YOUR_SMS_DEVICE_KEY
    python tools/fake_gateway.py --key YOUR_SMS_DEVICE_KEY --server http://192.168.1.50:5000 --fail-every 3

It polls /api/sms/next exactly like the real board does, "sends" each text (prints it), and reports the result.
--fail-every N makes every Nth message fail, to watch the retry logic work.
"""
import argparse
import time

import requests


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:5000")
    ap.add_argument("--key", required=True, help="must equal SMS_DEVICE_KEY on the server")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between polls")
    ap.add_argument("--fail-every", type=int, default=0, help="make every Nth message fail (0 = never)")
    ap.add_argument("--once", action="store_true", help="exit as soon as the queue is empty")
    args = ap.parse_args()

    headers = {"X-Device-Key": args.key}
    seen = 0
    print(f"fake gateway polling {args.server} every {args.interval}s (Ctrl+C to stop)")
    while True:
        try:
            r = requests.get(f"{args.server}/api/sms/next", params={"rssi": 20}, headers=headers, timeout=8)
        except requests.RequestException as e:
            print("poll failed:", e)
            time.sleep(args.interval)
            continue

        if r.status_code == 403:
            raise SystemExit("403 forbidden: --key doesn't match SMS_DEVICE_KEY on the server (or it isn't set there)")
        if r.status_code == 204:
            if args.once:
                print("queue empty")
                return
            time.sleep(args.interval)
            continue
        if r.status_code != 200:
            print("unexpected status", r.status_code)
            time.sleep(args.interval)
            continue

        sms_id, number, text = r.text.strip().split("\t", 2)
        seen += 1
        fail = args.fail_every and seen % args.fail_every == 0
        print(f"[#{sms_id}] -> {number}: {text}  ...", "FAIL" if fail else "sent")
        data = {"ok": "0", "error": "simulated failure"} if fail else {"ok": "1"}
        requests.post(f"{args.server}/api/sms/{sms_id}/result", data=data, headers=headers, timeout=8)


if __name__ == "__main__":
    main()
