"""B站 API 长时间压力测试 - 10分钟"""

import asyncio
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_EP_ID = 733316
DURATION = 600  # 10 minutes
TEST_RATE = 35  # req/s

COOKIE = os.environ.get("ANIME_BILIBILI_COOKIE_TEST", "").strip()
if not COOKIE:
    print("Please set ANIME_BILIBILI_COOKIE_TEST env var")
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
    "Cookie": COOKIE,
}
URL = f"https://api.bilibili.com/pgc/season/episode/web/info?ep_id={TEST_EP_ID}"


async def main():
    print(f"Long-duration test: {TEST_RATE} req/s for {DURATION}s ({DURATION / 60:.0f}min)")
    print(f"EP={TEST_EP_ID}")
    print()

    sem = asyncio.Semaphore(int(TEST_RATE * 1.2))
    interval = 1.0 / TEST_RATE

    results = {"ok": 0, "429": 0, "412": 0, "err": 0, "other": 0}
    latencies = []
    last_report = time.monotonic()

    async with httpx.AsyncClient(headers=HEADERS, timeout=8, limits=httpx.Limits(max_connections=50, max_keepalive_connections=30)) as client:

        async def fetch():
            async with sem:
                start = time.monotonic()
                try:
                    resp = await client.get(URL)
                    elapsed = time.monotonic() - start
                    latencies.append(elapsed)
                    if resp.status_code == 429:
                        results["429"] += 1
                        return "429"
                    if resp.status_code == 412:
                        results["412"] += 1
                        return "412"
                    if 200 <= resp.status_code < 300:
                        results["ok"] += 1
                        return "ok"
                    results["other"] += 1
                    return f"http_{resp.status_code}"
                except Exception:
                    results["err"] += 1
                    return "err"

        tasks = []
        stop = False
        deadline = time.monotonic() + DURATION

        async def launcher():
            nonlocal stop
            while time.monotonic() < deadline and not stop:
                tasks.append(asyncio.create_task(fetch()))
                await asyncio.sleep(interval)

        async def reporter():
            nonlocal stop
            while time.monotonic() < deadline and not stop:
                await asyncio.sleep(30)
                elapsed = time.monotonic() - (deadline - DURATION)
                total = sum(results.values())
                avg_lat = sum(latencies[-100:]) / min(len(latencies[-100:]), 1) * 1000 if latencies else 0

                p429 = results["429"]
                if p429 > 0:
                    print(f"  [{elapsed / 60:4.1f}min] total={total} ok={results['ok']} 429={results['429']} 412={results['412']} err={results['err']} lat={avg_lat:.0f}ms  *** THROTTLED ***")
                else:
                    print(f"  [{elapsed / 60:4.1f}min] total={total} ok={results['ok']} 429={results['429']} 412={results['412']} err={results['err']} lat={avg_lat:.0f}ms")

        await launcher()

        total = sum(results.values())
        avg_lat = sum(latencies) / len(latencies) * 1000 if latencies else 0

        print(f"\n=== Final ({total} requests, {DURATION}s) ===")
        print(f"  OK:    {results['ok']}  ({results['ok'] / total * 100:.1f}%)")
        print(f"  429:   {results['429']}  ({results['429'] / total * 100:.1f}%)")
        print(f"  412:   {results['412']}")
        print(f"  Err:   {results['err']}")
        print(f"  Other: {results['other']}")
        print(f"  Avg latency: {avg_lat:.0f}ms")
        print(f"  Effective rate: {total / DURATION:.1f} req/s")

        if results["429"] > 0:
            print(f"\n*** RATE LIMIT HIT at {TEST_RATE} req/s after {DURATION}s ***")
        else:
            print(f"\n*** SAFE at {TEST_RATE} req/s for {DURATION}s ({DURATION / 60:.0f}min) ***")

        stop = True


if __name__ == "__main__":
    asyncio.run(main())
