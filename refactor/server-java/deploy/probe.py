#!/usr/bin/env python3
"""Read-only side-by-side readiness and HTTP contract probe for Java/Python."""
import argparse
import json
import sys
import urllib.error
import urllib.request


def request(base, method, path, body=None, headers=None, timeout=15):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data, method=method, headers=request_headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except Exception as error:  # Report connection and timeout failures as probe data.
        return "ERR", repr(error).encode("utf-8")


def normalized(raw):
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw[:200].decode("utf-8", errors="replace")

    def strip_volatile(item):
        if isinstance(item, dict):
            return {
                key: strip_volatile(value)
                for key, value in item.items()
                if key not in {"request_id", "duration_ms", "checked_at", "at"}
            }
        if isinstance(item, list):
            return [strip_volatile(value) for value in item]
        return item

    return strip_volatile(value)


def readiness(base, timeout):
    status, raw = request(base, "GET", "/health/ready", timeout=timeout)
    if status == "ERR":
        return False, status, raw.decode("utf-8", errors="replace"), []
    try:
        data = json.loads(raw)
        dependencies = data["dependencies"]
        if not isinstance(dependencies, list):
            raise ValueError("dependencies is not a list")
        by_name = {d["name"]: d for d in dependencies if isinstance(d, dict) and isinstance(d.get("name"), str)}
        if len(by_name) != len(dependencies):
            raise ValueError("dependency entries are missing names or duplicated")
        required_names = {"postgres", "keycloak_discovery"}
        ready = (
            status == 200
            and data.get("status") == "ready"
            and data.get("started") is True
            and required_names.issubset(by_name)
            and all(by_name[name].get("ready") is True and by_name[name].get("required") is True for name in required_names)
            and "email" in by_name
            and isinstance(by_name["email"].get("ready"), bool)
            and isinstance(by_name["email"].get("code"), str)
            and all(item.get("ready") is True for item in data["dependencies"] if isinstance(item, dict) and item.get("required"))
        )
        summary = {
            name: (by_name[name].get("ready"), by_name[name].get("code"))
            for name in sorted(required_names | {"email"}) if name in by_name
        }
        detail = None if ready else "readiness payload reports not ready"
        return ready, status, detail, summary
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, AttributeError, ValueError):
        return False, status, "malformed readiness response", []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", default="http://java-backend:8080")
    parser.add_argument("--python", default="http://backend:8000")
    parser.add_argument("--java-only", action="store_true", help="probe Java when Python is stopped")
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()

    bogus = {"Authorization": "Bearer mfn1_a_bogus"}
    forwarded_https = {"X-Forwarded-Proto": "https"}
    cases = [
        ("GET", "/health/live", None, None, 200),
        ("GET", "/health/startup", None, None, 200),
        ("GET", "/api/enterprise/auth/lifecycle/capabilities", None, None, 200),
        ("GET", "/api/enterprise/catalog/source-whitelist", None, bogus, 401),
        ("GET", "/api/enterprise/sync/v1/snapshot", None, bogus, 401),
        ("GET", "/api/enterprise/auth/me", None, bogus, 401),
        ("POST", "/api/enterprise/auth/native/refresh", {"refresh_token": "mfn1_r_bogus"}, forwarded_https, 401),
        ("POST", "/api/enterprise/account/closure/prepare", {}, bogus, 401),
        ("GET", "/api/enterprise/auth/admin/overview", None, bogus, 401),
    ]
    matches = 0
    for method, path, body, headers, expected_status in cases:
        java = request(args.java, method, path, body, headers, args.timeout)
        if args.java_only:
            same = java[0] == expected_status
        else:
            python = request(args.python, method, path, body, headers, args.timeout)
            same = (
                java[0] == expected_status
                and python[0] == expected_status
                and normalized(java[1]) == normalized(python[1])
            )
        matches += same
        suffix = f"java={java[0]}"
        if not args.java_only:
            suffix += f" python={python[0]}"
        print(f"{'OK' if same and args.java_only else 'SAME' if same else 'DIFF'} {method} {path} {suffix}")

    ready = {}
    email_state = {}
    targets = [("java", args.java)]
    if not args.java_only:
        targets.append(("python", args.python))
    for name, base in targets:
        ok, status, error, dependencies = readiness(base, args.timeout)
        ready[name] = ok
        if "email" in dependencies:
            email_state[name] = dependencies["email"]
        print(f"READY {name} status={status} ready={ok} dependencies={dependencies}")
        if error:
            print(f"READY {name} detail={error}")

    print(f"contract cases matched: {matches}/{len(cases)}")
    expected_email = (True, "EMAIL_TLS_AUTH_READY")
    email_matches = (
        email_state.get("java") == expected_email
        and (args.java_only or email_state.get("python") == expected_email)
        and (args.java_only or email_state.get("java") == email_state.get("python"))
    )
    print(f"email readiness matches Python: {email_matches}")
    return 0 if matches == len(cases) and all(ready.values()) and email_matches else 1


if __name__ == "__main__":
    sys.exit(main())
