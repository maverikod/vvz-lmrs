"""The LMRS client: one method per public LMRS command.

Wraps the ``mcp_proxy_adapter`` framework JSON-RPC client. No method builds a
URL, a JSON-RPC envelope or a raw transport payload: transport, protocol
selection, connection management, serialization and error mapping all belong to
the framework client. Queued commands are driven to completion through the
framework's queued-job facility rather than by polling here.

The client never defines the API. Every method mirrors a command the server
registers, with the same name, so client and server can be read against each
other without a translation table.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any

from mcp_proxy_adapter.client.jsonrpc_client.client import JsonRpcClient


class LmrsClient:
    """Full-surface client for one LMRS server.

    Attributes:
        transport: The framework JSON-RPC client carrying every request.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8012,
        protocol: str = "https",
        *,
        token: str | None = None,
        token_header: str | None = None,
        cert: str | None = None,
        key: str | None = None,
        ca: str | None = None,
        check_hostname: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Build a client bound to one LMRS server.

        Args:
            host: Server hostname.
            port: Server port.
            protocol: Transport protocol: ``http``, ``https`` or ``mtls``.
            token: Authentication token value, when the server requires one.
            token_header: Header carrying the token.
            cert: Client certificate path for mTLS.
            key: Client private key path for mTLS.
            ca: CA certificate path.
            check_hostname: Whether TLS verifies the hostname.
            timeout: Request timeout in seconds; the framework default applies
                when omitted.
        """
        self.transport = JsonRpcClient(
            protocol=protocol,
            host=host,
            port=port,
            token=token,
            token_header=token_header,
            cert=cert,
            key=key,
            ca=ca,
            check_hostname=check_hostname,
            timeout=timeout,
        )

    async def _call(self, command: str, params: dict[str, Any] | None = None, *, queued: bool = False) -> Any:
        """Send one command through the framework client.

        Args:
            command: Public command name, as registered by the server.
            params: Command parameters, omitted when the command takes none.
            queued: True for a command the server runs through its queue; the
                framework then waits for the job to finish instead of returning
                a job id for the caller to poll.

        Returns:
            The command result the server produced.
        """
        return await self.transport.execute_command_unified(
            command,
            params or {},
            expect_queue=True if queued else False,
            auto_poll=True,
        )

    async def healthcheck(self) -> Any:
        """Report adapter health.

        Returns:
            The healthcheck result.
        """
        return await self._call("healthcheck")

    async def info(self) -> Any:
        """Describe service identity, build, runtime and capabilities.

        Returns:
            The info payload.
        """
        return await self._call("info")

    async def model_status(self, model_name: str) -> Any:
        """Report the memory residency status of a model.

        Args:
            model_name: Name of the model to inspect.

        Returns:
            The model status result.
        """
        return await self._call("model_status", {"model_name": model_name})

    async def capacity(self) -> Any:
        """Report measured VRAM facts and the usable dynamic pool.

        Returns:
            The capacity snapshot.
        """
        return await self._call("capacity")

    async def token_count(
        self,
        input_tokens: int | None = None,
        tokenizer_name: str | None = None,
        tokenizer_accuracy: str | None = None,
        *,
        message: str | None = None,
        system: str | None = None,
        model_name: str | None = None,
        tool_tokens: int = 0,
        service_tokens: int = 0,
        reserved_output_tokens: int = 0,
        rough_estimate: bool = False,
    ) -> Any:
        """Report the token breakdown and required tokens of a request.

        Two modes, decided by the server: pass ``message`` (text mode) and the
        prompt is counted with the runtime's own tokenizer; or pass
        ``input_tokens`` with the tokenizer identity (numeric mode) and the
        server sums the caller-declared components.

        Args:
            input_tokens: Numeric mode: tokens in the input prompt.
            tokenizer_name: Numeric mode: which tokenizer produced the counts.
            tokenizer_accuracy: Numeric mode: accuracy descriptor.
            message: Text mode: the user message to count.
            system: Text mode: optional system instruction counted with it.
            model_name: Text mode: model whose tokenizer applies; the resident
                model is assumed when omitted.
            tool_tokens: Numeric mode: tokens consumed by tool definitions.
            service_tokens: Numeric mode: tokens for service instructions.
            reserved_output_tokens: Output tokens reserved for generation.
            rough_estimate: Numeric mode: whether the counts are rough.

        Returns:
            The token breakdown and the required token total.
        """
        params: dict[str, Any] = {"reserved_output_tokens": reserved_output_tokens}
        if message is not None:
            params["message"] = message
            if system is not None:
                params["system"] = system
            if model_name is not None:
                params["model_name"] = model_name
        else:
            params.update(
                {
                    "input_tokens": input_tokens,
                    "tokenizer_name": tokenizer_name,
                    "tokenizer_accuracy": tokenizer_accuracy,
                    "tool_tokens": tool_tokens,
                    "service_tokens": service_tokens,
                    "rough_estimate": rough_estimate,
                }
            )
        return await self._call("token_count", params)

    async def estimate(self, **request: Any) -> Any:
        """Report whether a request would execute, queue or be rejected.

        Args:
            **request: The estimate inputs the server's estimate schema
                declares: request_id, model_name, token_breakdown,
                declared_context_window, capacity, kv_bytes_per_token,
                per_request_overhead_bytes and runtime_batch_overhead_bytes.

        Returns:
            The dry-run outcome with its reason code.
        """
        return await self._call("estimate", dict(request))

    async def chat(
        self,
        message: str,
        model_name: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """Send a chat message to the locally served model.

        Args:
            message: The user message.
            model_name: Model to serve the request.
            system: Optional system instruction.
            temperature: Optional sampling temperature.
            max_tokens: Optional output token limit.

        Returns:
            The chat result.
        """
        params: dict[str, Any] = {"message": message, "model_name": model_name}
        if system is not None:
            params["system"] = system
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        return await self._call("chat", params)

    async def queue_status(self) -> Any:
        """Report the current request queue state.

        Returns:
            The queue entries.
        """
        return await self._call("queue_status")

    async def cancel(self, request_id: str) -> Any:
        """Cancel a queued request.

        Args:
            request_id: Identifier of the request to cancel.

        Returns:
            The queue state the cancellation produced.
        """
        return await self._call("cancel", {"request_id": request_id})

    async def local_model_cache_preload(self, model_name: str) -> Any:
        """Prepare a model in the local disk cache.

        A preload downloads model weights, so the server runs it through its
        queue and this call returns only once the download has finished; the
        framework's queued-job facility does the waiting.

        Args:
            model_name: Model to preload.

        Returns:
            The preload result.
        """
        return await self._call("local_model_cache_preload", {"model_name": model_name}, queued=True)

    async def local_model_cache_status(self, model_name: str) -> Any:
        """Report local disk-cache status for a model.

        Args:
            model_name: Model to inspect.

        Returns:
            The cache status result.
        """
        return await self._call("local_model_cache_status", {"model_name": model_name})

    async def local_model_cache_delete(self, model_name: str) -> Any:
        """Remove a model from the tracked local disk cache.

        Args:
            model_name: Model to remove.

        Returns:
            The delete result.
        """
        return await self._call("local_model_cache_delete", {"model_name": model_name})

    async def local_model_load(self, model_name: str, *, allow_preload: bool = False) -> Any:
        """Load a model into memory.

        Args:
            model_name: Model to load.
            allow_preload: Whether loading may preload an uncached model.

        Returns:
            The load result.
        """
        return await self._call(
            "local_model_load",
            {"model_name": model_name, "allow_preload": allow_preload},
        )

    async def local_model_unload(self, model_name: str) -> Any:
        """Unload a model from memory.

        Args:
            model_name: Model to unload.

        Returns:
            The unload result.
        """
        return await self._call("local_model_unload", {"model_name": model_name})

    async def local_model_reload(self, model_name: str) -> Any:
        """Re-probe a model's residency.

        Args:
            model_name: Model to reload.

        Returns:
            The reload result.
        """
        return await self._call("local_model_reload", {"model_name": model_name})

    async def local_lmcache_status(self) -> Any:
        """Report LMCache enablement, per-tier usage and hit accounting.

        Returns:
            The LMCache status.
        """
        return await self._call("local_lmcache_status")

    async def local_lmcache_purge(self, *, namespace: str | None = None, session: str | None = None) -> Any:
        """Remove cached LMCache artifacts globally or for one binding.

        Args:
            namespace: Namespace binding to scope the purge to.
            session: Session binding to scope the purge to.

        Returns:
            The purge summary.
        """
        params: dict[str, Any] = {}
        if namespace is not None:
            params["namespace"] = namespace
        if session is not None:
            params["session"] = session
        return await self._call("local_lmcache_purge", params)

    async def local_model_switch(self, model_name: str) -> Any:
        """Switch the resident model to another model.

        This command is queued on the server, so the call returns only once the
        switch has finished; the framework's queued-job facility does the
        waiting.

        Args:
            model_name: Model to switch to.

        Returns:
            The switch result with its progress stages.
        """
        return await self._call("local_model_switch", {"model_name": model_name}, queued=True)
