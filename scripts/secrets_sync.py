#!/usr/bin/env python3
"""Sync secrets between a local .env file and Google Secret Manager.

This is the team workflow for sharing API keys without emailing .env files:

  # one person seeds Secret Manager from their working .env
  python scripts/secrets_sync.py push --env-file .env

  # teammates pull the shared secrets into their own .env
  python scripts/secrets_sync.py pull --env-file .env

  # see what's stored
  python scripts/secrets_sync.py list

The GCP project comes from --project or GCP_PROJECT_ID. Auth uses Application
Default Credentials (`gcloud auth application-default login`).

Secret ids equal the env-var names (optionally namespaced with --prefix).
Only non-empty values are pushed. Config-style vars (booleans, URLs, numbers)
can be skipped with --skip so only real secrets go to Secret Manager.
"""

import argparse
import os
import sys

# Make `packages` importable when run as a script from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packages.core.secrets import SecretManager, resolve_project_id  # noqa: E402

# Non-secret config knobs that live in .env but don't belong in Secret Manager.
DEFAULT_SKIP = {
    "FILTER_SELF_SPEAKER",
    "MIC_SAMPLE_RATE",
    "NOISE_SUPPRESSION",
    "ZERO_CRM_BASE_URL",
    "UNIFY_GTM_BASE_URL",
    "GOOGLE_MCP_SERVER_URL",
    "GCP_REGION",
    "PUBSUB_TOPIC",
    "COMMUNITY_GROUPS_ENABLED",
    "COMMUNITY_PERMISSIONS",
    "USER_ROLE",
    "TEAM_SIZE",
    "COMPANY_STAGE",
    "HF_MODEL_PATH",
    "GCP_PROJECT_ID",
    "WARMTH_DEMO_USER_EMAIL",
}


def parse_env_file(path: str) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (ignores comments/blank lines)."""
    values: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            values[key] = value
    return values


def cmd_push(args: argparse.Namespace) -> int:
    manager = SecretManager(project_id=args.project, prefix=args.prefix)
    values = parse_env_file(args.env_file)
    skip = DEFAULT_SKIP if not args.no_skip_defaults else set()
    skip |= set(args.skip or [])

    pushed, skipped = [], []
    for key, value in values.items():
        if key in skip or not value:
            skipped.append(key)
            continue
        if args.dry_run:
            print(f"  would push {args.prefix}{key}")
        else:
            manager.set_secret(key, value)
            print(f"  pushed {args.prefix}{key}")
        pushed.append(key)

    print(f"\nPushed {len(pushed)} secret(s); skipped {len(skipped)} "
          f"(empty or config): {', '.join(skipped)}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    manager = SecretManager(project_id=args.project, prefix=args.prefix)
    secret_ids = manager.list_secret_ids()
    if not secret_ids:
        print("No secrets found in project.")
        return 0

    pairs: dict[str, str] = {}
    for secret_id in secret_ids:
        env_name = manager._env_name_for(secret_id)
        value = manager.get_secret(env_name)
        if value is not None:
            pairs[env_name] = value

    if args.stdout or not args.env_file:
        for k, v in pairs.items():
            print(f"{k}={v}")
        return 0

    # Merge into existing .env, preserving non-secret lines.
    existing = parse_env_file(args.env_file) if os.path.exists(args.env_file) else {}
    existing.update(pairs)
    with open(args.env_file, "w", encoding="utf-8") as fh:
        for k, v in existing.items():
            fh.write(f"{k}={v}\n")
    print(f"Wrote {len(pairs)} secret(s) into {args.env_file}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    manager = SecretManager(project_id=args.project, prefix=args.prefix)
    ids = manager.list_secret_ids()
    print(f"{len(ids)} secret(s) in project {manager.project_id}:")
    for secret_id in ids:
        print(f"  - {secret_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=None,
        help="GCP project id (defaults to GCP_PROJECT_ID env var)",
    )
    parser.add_argument("--prefix", default="", help="Secret id prefix/namespace")
    sub = parser.add_subparsers(dest="command", required=True)

    p_push = sub.add_parser("push", help="Push .env secrets to Secret Manager")
    p_push.add_argument("--env-file", default=".env")
    p_push.add_argument("--skip", nargs="*", help="Extra keys to skip")
    p_push.add_argument("--no-skip-defaults", action="store_true",
                        help="Also push the default config knobs")
    p_push.add_argument("--dry-run", action="store_true")
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", help="Pull secrets from Secret Manager")
    p_pull.add_argument("--env-file", default=".env")
    p_pull.add_argument("--stdout", action="store_true",
                        help="Print to stdout instead of writing the .env file")
    p_pull.set_defaults(func=cmd_pull)

    p_list = sub.add_parser("list", help="List secret ids")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()

    if not resolve_project_id(args.project):
        parser.error("No GCP project: pass --project or set GCP_PROJECT_ID")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
