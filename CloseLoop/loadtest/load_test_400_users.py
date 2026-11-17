#!/usr/bin/env python3
"""400-virtual-user load test for CloseLoop.

Stdlib only (urllib + threads) so it runs anywhere Python 3 is installed,
no pip install needed.

Point this at your LOCAL dev stack (docker-compose.yml, default
http://localhost:8002) unless you've explicitly decided to accept the risk
of a real outage on production.

Usage:
    python3 loadtest/load_test_400_users.py
    python3 loadtest/load_test_400_users.py --users 400 --requests-per-user 5
    python3 loadtest/load_test_400_users.py --base-url https://closeloop.foursymmetrons.com
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

# Seeded demo accounts (see backend/seed.py). Reused round-robin across
# virtual users rather than creating 400 real accounts -- this still
# exercises real concurrent authenticated traffic against the same
# endpoints a real user session would hit.
DEMO_ACCOUNTS = [
    ("admin@acme.com", "admin123"),
    ("lead@acme.com", "lead123"),
    ("apil@acme.com", "member123"),
    ("miraj@acme.com", "member123"),
]

# team-workload / lead-workbench are 403 for TEAM_MEMBER by design (RBAC,
# not a bug) -- restrict the default endpoint mix to accounts that can hit
# every endpoint so failures below mean "the app broke", not "wrong role".
PRIVILEGED_ACCOUNTS = [("admin@acme.com", "admin123"), ("lead@acme.com", "lead123")]

# A realistic mix of read endpoints a logged-in user's dashboard actually
# fires on load (see LeadDashboard.jsx / AdminDashboardPage.jsx).
ENDPOINTS = [
    "/api/health",
    "/api/plans",
    "/api/team/dashboard",
    "/api/dashboards/team-workload",
    "/api/dashboards/me/lead-workbench",
]


def login(base_url, email, password, timeout):
    req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["access_token"]


def timed_get(base_url, path, token, timeout):
    req = urllib.request.Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return {"ok": resp.status < 400, "status": resp.status,
                     "error": None, "latency": time.perf_counter() - t0}
    except urllib.error.HTTPError as e:
        e.read()
        return {"ok": False, "status": e.code, "error": None,
                 "latency": time.perf_counter() - t0}
    except Exception as e:  # connection refused, timeout, reset, etc.
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}",
                 "latency": time.perf_counter() - t0}


def virtual_user(user_id, base_url, tokens, requests_per_user, timeout):
    token = tokens[user_id % len(tokens)]
    out = []
    for i in range(requests_per_user):
        path = ENDPOINTS[(user_id + i) % len(ENDPOINTS)]
        out.append(timed_get(base_url, path, token, timeout))
    return out


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p))
    return sorted_vals[idx]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8002",
                         help="Default: local dev backend (docker-compose.yml)")
    parser.add_argument("--users", type=int, default=400)
    parser.add_argument("--requests-per-user", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--include-member-accounts", action="store_true",
                         help="Also log in as TEAM_MEMBER demo accounts. "
                              "They'll correctly get 403 on lead-only "
                              "endpoints -- that's RBAC, not a failure.")
    args = parser.parse_args()

    accounts = DEMO_ACCOUNTS if args.include_member_accounts else PRIVILEGED_ACCOUNTS

    if "foursymmetrons.com" in args.base_url:
        print("!! --base-url points at PRODUCTION. This will send real load "
              "to real users. Ctrl+C now if that wasn't intended.\n")
        time.sleep(5)

    print(f"Logging in {len(accounts)} demo accounts against {args.base_url} ...")
    tokens = []
    for email, password in accounts:
        try:
            tokens.append(login(args.base_url, email, password, args.timeout))
            print(f"  OK    {email}")
        except Exception as e:
            print(f"  FAIL  {email}: {e}")

    if not tokens:
        print("\nNo accounts logged in -- is the stack up? "
              "(docker compose up --build db backend)")
        return

    total_requests = args.users * args.requests_per_user
    print(f"\nFiring {args.users} concurrent virtual users, "
          f"{args.requests_per_user} requests each "
          f"({total_requests} total requests) against {len(ENDPOINTS)} endpoints...\n")

    start = time.perf_counter()
    all_results = []
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [
            pool.submit(virtual_user, uid, args.base_url, tokens,
                        args.requests_per_user, args.timeout)
            for uid in range(args.users)
        ]
        for f in as_completed(futures):
            all_results.extend(f.result())
    wall = time.perf_counter() - start

    total = len(all_results)
    ok = [r for r in all_results if r["ok"]]
    failed = [r for r in all_results if not r["ok"]]
    latencies = sorted(r["latency"] for r in ok)

    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Wall time:          {wall:.2f}s")
    print(f"Total requests:     {total}")
    print(f"Succeeded:          {len(ok)} ({len(ok) / total * 100:.1f}%)")
    print(f"Failed:             {len(failed)} ({len(failed) / total * 100:.1f}%)")
    print(f"Throughput:         {total / wall:.1f} req/s")
    if latencies:
        print(f"Latency p50/p90/p95/p99 (ms): "
              f"{percentile(latencies, .50) * 1000:.0f} / "
              f"{percentile(latencies, .90) * 1000:.0f} / "
              f"{percentile(latencies, .95) * 1000:.0f} / "
              f"{percentile(latencies, .99) * 1000:.0f}")
        print(f"Latency min/max (ms): {latencies[0] * 1000:.0f} / {latencies[-1] * 1000:.0f}")

    if failed:
        print("\nFailure breakdown:")
        buckets = Counter((r["status"], r["error"]) for r in failed)
        for (status, error), count in buckets.most_common(10):
            label = f"HTTP {status}" if status else error
            print(f"  {count:>5}x  {label}")

    print()
    fail_rate = len(failed) / total if total else 1
    if not failed:
        print(f"No failures across {total} requests at {args.users} concurrent users.")
    elif fail_rate < 0.01:
        print("Under 1% failure rate -- likely transient, check the breakdown above.")
    else:
        print("Meaningful failure rate -- the app is struggling at this concurrency. "
              "Check `docker logs closeloop-backend` for crashes/timeouts/connection-pool errors.")


if __name__ == "__main__":
    main()
