#!/usr/bin/env python
"""Generate an RS256 keypair for JWT signing and emit the env vars (C.3).

Usage:
    python scripts/generate_jwt_keys.py [--kid KID]

Prints the three env vars to put in .env.prod:
  - AUTH_PRIVATE_KEY_PEM   (ISSUER ONLY — user-service; keep secret)
  - AUTH_ACTIVE_KID        (issuer)
  - AUTH_PUBLIC_KEYS_JSON  (ALL services — verification keyset)

Also set AUTH_ALGORITHM=RS256 on every service. To ROTATE, run again with a new --kid,
then MERGE the new public key into AUTH_PUBLIC_KEYS_JSON everywhere (keep the old kid until
old tokens expire) and point the issuer's AUTH_PRIVATE_KEY_PEM/AUTH_ACTIVE_KID at the new key.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from solace_security.keys import generate_rsa_keypair  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an RS256 JWT signing keypair.")
    parser.add_argument("--kid", default="solace-2026-07", help="Key id for the new key")
    args = parser.parse_args()

    private_pem, public_pem, kid = generate_rsa_keypair(args.kid)
    public_keys_json = json.dumps({kid: public_pem})

    print("# ---- issuer only (user-service) — KEEP SECRET ----")
    print(f"AUTH_ACTIVE_KID={kid}")
    # Single-line PEM for env files (escape newlines).
    print("AUTH_PRIVATE_KEY_PEM=" + private_pem.replace("\n", "\\n"))
    print()
    print("# ---- ALL services (verification keyset) ----")
    print("AUTH_ALGORITHM=RS256")
    print("AUTH_PUBLIC_KEYS_JSON=" + public_keys_json.replace("\n", "\\n"))


if __name__ == "__main__":
    main()
