#!/usr/bin/env python3
"""
Lightweight checker for remote plugin bundles and manifest entries.

Logs in, fetches the designer manifest, prints the selected plugin entry,
and attempts to download the remoteEntry.js via the public plugins endpoint.
Useful for validating scope/module resolution issues without opening the UI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, Iterable, Optional

import httpx


DEFAULT_API_BASE = os.getenv("BRAINDRIVE_API_BASE", "http://10.1.2.149:8205")
DEFAULT_EMAIL = os.getenv("BRAINDRIVE_EMAIL", "aaaa@gmail.com")
DEFAULT_PASSWORD = os.getenv("BRAINDRIVE_PASSWORD", "10012002")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Validate remote plugin bundle loading")
  parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="API base URL (default: %(default)s)")
  parser.add_argument("--email", default=DEFAULT_EMAIL, help="Login email (default: %(default)s)")
  parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Login password (default: %(default)s)")
  parser.add_argument("--plugin-id", default="BrainDriveRAGCommunity", help="Plugin id or slug to validate")
  parser.add_argument("--bundle-path", default=None, help="Optional override for bundle location (e.g., dist/remoteEntry.js)")
  parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
  parser.add_argument("--verbose", action="store_true", help="Print extra diagnostics")
  return parser.parse_args(argv)


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
  resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
  resp.raise_for_status()
  token = resp.json().get("access_token")
  if not token:
    raise RuntimeError("Login succeeded but no access_token returned")
  return token


async def fetch_manifest(client: httpx.AsyncClient, token: str) -> Dict[str, Any]:
  resp = await client.get("/api/v1/plugins/manifest/designer", headers={"Authorization": f"Bearer {token}"})
  resp.raise_for_status()
  payload = resp.json()
  if isinstance(payload, dict):
    return payload
  raise RuntimeError(f"Unexpected manifest shape: {type(payload)}")


def select_plugin(manifest: Dict[str, Any], plugin_id: str) -> Optional[Dict[str, Any]]:
  values = list(manifest.values())
  slug_guess = plugin_id.split("_", 1)[-1] if "_" in plugin_id else plugin_id
  for entry in values:
    if not isinstance(entry, dict):
      continue
    if entry.get("id") in (plugin_id, slug_guess) or entry.get("plugin_slug") in (plugin_id, slug_guess) or entry.get("scope") in (plugin_id, slug_guess):
      return entry
  return None


async def fetch_bundle(client: httpx.AsyncClient, plugin: Dict[str, Any], bundle_path_override: Optional[str], verbose: bool) -> Dict[str, Any]:
  plugin_id = plugin.get("id") or plugin.get("plugin_slug") or "unknown"
  bundle_path = bundle_path_override or plugin.get("bundlelocation") or plugin.get("bundle_location")
  if not bundle_path:
    raise RuntimeError("Manifest missing bundlelocation/bundle_location")

  url = f"/api/v1/public/plugins/{plugin_id}/{bundle_path}"
  resp = await client.get(url)
  result = {"status": resp.status_code, "url": str(client.base_url.join(url))}

  if resp.status_code != 200:
    result["error"] = resp.text
    return result

  body = resp.text
  scope = plugin.get("scope") or "<missing-scope>"
  module_names = []
  modules = plugin.get("modules")
  if isinstance(modules, list):
    module_names = [m.get("name") for m in modules if isinstance(m, dict) and m.get("name")]

  result["scope_found"] = (f"var {scope}" in body) or (f"{scope}=" in body)
  result["modules_found"] = {name: (name in body) for name in module_names}
  result["content_length"] = len(body)

  if verbose:
    print(f"[bundle] fetched {result['url']} ({result['content_length']} bytes)")
    print(f"[bundle] scope match: {result['scope_found']}")
    if module_names:
      print(f"[bundle] modules: {json.dumps(result['modules_found'])}")

  return result


async def main(argv: Optional[Iterable[str]] = None) -> int:
  args = parse_args(argv)
  async with httpx.AsyncClient(base_url=args.api_base, timeout=args.timeout) as client:
    try:
      print(f"[auth] logging in as {args.email}")
      token = await login(client, args.email, args.password)
      client.headers.update({"Authorization": f"Bearer {token}"})

      manifest = await fetch_manifest(client, token)
      plugin = select_plugin(manifest, args.plugin_id)
      if not plugin:
        print(f"[error] plugin '{args.plugin_id}' not found in manifest ({len(manifest)} entries)", file=sys.stderr)
        return 1

      print("[manifest] plugin entry:")
      print(json.dumps(plugin, indent=2))

      bundle_result = await fetch_bundle(client, plugin, args.bundle_path, args.verbose)
      print("[bundle] result:", json.dumps(bundle_result, indent=2))

      if bundle_result.get("status") != 200:
        return 2
      if not bundle_result.get("scope_found"):
        print("[warn] scope string not found in bundle content", file=sys.stderr)
        return 3
      missing_modules = [name for name, found in bundle_result.get("modules_found", {}).items() if not found]
      if missing_modules:
        print(f"[warn] modules not found in bundle body: {', '.join(missing_modules)}", file=sys.stderr)
        return 4

      print("[ok] bundle reachable and contains expected scope/module markers")
      return 0
    except httpx.HTTPStatusError as exc:
      print(f"[error] HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
      return 1
    except Exception as exc:  # pylint: disable=broad-except
      print(f"[error] {exc}", file=sys.stderr)
      return 1


if __name__ == "__main__":
  raise SystemExit(asyncio.run(main()))
