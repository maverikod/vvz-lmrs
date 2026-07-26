#!/usr/bin/env python3
"""Send one message either through LMRS JSON-RPC or directly to vLLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Mapping


def _assistant_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    inner = data.get("payload") if isinstance(data, dict) else None
    response = inner if isinstance(inner, dict) else payload
    choices = response.get("choices") if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    return None


def _post_json(url: str, payload: Mapping[str, object], timeout: float) -> Mapping[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    loaded = json.loads(body or "{}")
    if not isinstance(loaded, Mapping):
        raise RuntimeError("endpoint returned non-object JSON")
    return loaded


async def _call_lmrs(args: argparse.Namespace) -> object:
    try:
        from mcp_proxy_adapter.client.jsonrpc_client import JsonRpcClient
    except ImportError as exc:  # pragma: no cover - operational dependency check.
        raise SystemExit(
            "mcp-proxy-adapter is required for --mode lmrs. Install the LMRS [server] "
            "extra or run inside the LMRS image."
        ) from exc

    client = JsonRpcClient(
        protocol=args.protocol,
        host=args.host,
        port=args.port,
        token_header=args.token_header,
        token=args.token,
        cert=args.cert,
        key=args.key,
        ca=args.ca,
        check_hostname=args.check_hostname,
        timeout=args.http_timeout,
    )
    try:
        params: dict[str, object] = {
            "model_name": args.model,
            "message": args.message,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
        }
        if args.system:
            params["system"] = args.system
        return await client.execute_command_unified(
            args.command,
            params,
            expect_queue=False,
            auto_poll=False,
            timeout=args.timeout,
        )
    finally:
        await client.close()


def _call_vllm(args: argparse.Namespace) -> object:
    protocol = "https" if args.protocol in {"https", "mtls"} else "http"
    url = f"{protocol}://{args.host}:{args.port}/v1/chat/completions"
    messages: list[dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.message})
    return _post_json(
        url,
        {
            "model": args.model,
            "messages": messages,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
        },
        args.http_timeout,
    )


async def _run(args: argparse.Namespace) -> int:
    result = await _call_lmrs(args) if args.mode == "lmrs" else _call_vllm(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        text = _assistant_text(result)
        print(text if text is not None else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="LMRS/vLLM host or IP address")
    parser.add_argument("port", type=int, help="LMRS/vLLM port")
    parser.add_argument("message", help="Message to send to the model")
    parser.add_argument("--mode", choices=("lmrs", "vllm"), default="lmrs", help="lmrs=JSON-RPC MCP command, vllm=direct OpenAI-compatible API")
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct", help="Served vLLM model id")
    parser.add_argument("--protocol", choices=("http", "https", "mtls"), default="https")
    parser.add_argument("--command", default="chat", help="LMRS MCP command to call")
    parser.add_argument("--system", default=None, help="Optional system prompt")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=120.0, help="Overall LMRS command timeout")
    parser.add_argument("--http-timeout", type=float, default=120.0, help="HTTP client timeout")
    parser.add_argument("--token-header", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--cert", default=None, help="Client certificate for mTLS")
    parser.add_argument("--key", default=None, help="Client private key for mTLS")
    parser.add_argument("--ca", default=None, help="CA certificate for TLS/mTLS")
    parser.add_argument("--check-hostname", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print full JSON response")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
