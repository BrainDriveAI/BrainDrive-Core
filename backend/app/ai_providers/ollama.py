"""
Ollama AI provider implementation (clean and streaming-ready).
"""
import httpx
import json
import asyncio
from typing import Dict, List, Any, AsyncGenerator, Optional
from .base import AIProvider

class OllamaProvider(AIProvider):
    @property
    def provider_name(self) -> str:
        return "ollama"

    async def initialize(self, config: Dict[str, Any]) -> bool:
        import logging
        logger = logging.getLogger(__name__)
        
        # Debug logging for server URL resolution
        logger.info(f"[OLLAMA] Ollama provider initializing with config: {config}")

        self.server_url = config.get("server_url", "http://localhost:11434")
        self.api_key = config.get("api_key", "")
        self.server_name = config.get("server_name", "Default Ollama Server")

        # Log what URL we're actually using
        if self.server_url == "http://localhost:11434" and "server_url" not in config:
            logger.warning(f"[OLLAMA] WARNING: Ollama provider defaulting to localhost! Config was: {config}")
        else:
            logger.info(f"[OLLAMA] Ollama provider using server_url: {self.server_url}")

        logger.info(f"[OLLAMA] Ollama provider initialized - server_name: {self.server_name}, server_url: {self.server_url}")
        return True

    async def get_models(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.server_url}/api/tags")
            models = response.json().get("models", [])
            return [
                {
                    "id": model["name"],
                    "name": model["name"],
                    "provider": "ollama",
                    "metadata": model
                }
                for model in models
            ]

    async def generate_text(self, prompt: str, model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_ollama_api(prompt, model, params, is_streaming=False)

    async def generate_stream(self, prompt: str, model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in self._stream_ollama_api(prompt, model, params):
            yield chunk

    async def chat_completion(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[OLLAMA] CHAT_COMPLETION CALLED")
        print(f"[OLLAMA] Server URL: {self.server_url}")
        print(f"[OLLAMA] Server Name: {self.server_name}")
        print(f"[OLLAMA] Model: {model}")
        print(f"[OLLAMA] Messages: {len(messages)} messages")
        if isinstance(params.get("tools"), list) and params.get("tools"):
            return await self._call_ollama_chat_api(messages, model, params)

        prompt = self._format_chat_messages(messages)
        
        result = await self._call_ollama_api(prompt, model, params, is_streaming=False)
        if "error" not in result:
            result["choices"] = [{
                "message": {
                    "role": "assistant",
                    "content": result.get("text", "")
                },
                "finish_reason": result.get("finish_reason")
            }]
        return result

    async def chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        if isinstance(params.get("tools"), list) and params.get("tools"):
            async for chunk in self._stream_ollama_chat_api(messages, model, params):
                yield chunk
            return

        prompt = self._format_chat_messages(messages)
        
        # TODO: implement full cancellation support
        # track the actual HTTP request and cancel it at the httpx level
        
        async for chunk in self._stream_ollama_api(prompt, model, params):
            if "error" not in chunk:
                chunk["choices"] = [{
                    "delta": {
                        "role": "assistant",
                        "content": chunk.get("text", "")
                    },
                    "finish_reason": chunk.get("finish_reason")
                }]
            yield chunk

    async def _call_ollama_chat_api(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)

        normalized_messages = self._normalize_chat_messages_for_ollama(messages)
        options = self._build_ollama_options(params)
        payload = {
            "model": model,
            "messages": normalized_messages,
            "stream": False,
            "options": options,
        }

        tools = params.get("tools")
        if isinstance(tools, list) and tools:
            payload["tools"] = tools

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        api_url = f"{self.server_url}/api/chat"
        logger.info(f"[OLLAMA] Making native chat API call to: {api_url}")

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(api_url, json=payload, headers=headers)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as http_err:
                    detail = await self._extract_error_detail(http_err.response)
                    return self._format_error(f"{http_err} | {detail}", model)

                result = response.json()
                message = result.get("message") if isinstance(result.get("message"), dict) else {}
                content = message.get("content", "") if isinstance(message.get("content"), str) else ""
                tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
                done = bool(result.get("done", False))
                finish_reason = self._normalize_finish_reason(result.get("done_reason"), done=done)

                return {
                    "text": content,
                    "content": content,
                    "message": {
                        "role": message.get("role", "assistant"),
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                    "tool_calls": tool_calls,
                    "provider": "ollama",
                    "model": model,
                    "metadata": result,
                    "finish_reason": finish_reason,
                    "choices": [
                        {
                            "message": {
                                "role": message.get("role", "assistant"),
                                "content": content,
                                "tool_calls": tool_calls,
                            },
                            "finish_reason": finish_reason,
                        }
                    ],
                }
        except Exception as e:
            return self._format_error(e, model)

    async def _stream_ollama_chat_api(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        import logging
        logger = logging.getLogger(__name__)

        normalized_messages = self._normalize_chat_messages_for_ollama(messages)
        options = self._build_ollama_options(params)
        payload = {
            "model": model,
            "messages": normalized_messages,
            "stream": True,
            "options": options,
        }

        tools = params.get("tools")
        if isinstance(tools, list) and tools:
            payload["tools"] = tools

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        logger.info(f"[OLLAMA] Streaming native chat API call to: {self.server_url}/api/chat")

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", f"{self.server_url}/api/chat", json=payload, headers=headers) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as http_err:
                        detail = await self._extract_error_detail(http_err.response)
                        yield self._format_error(f"{http_err} | {detail}", model, done=True)
                        return

                    async for chunk in response.aiter_lines():
                        if not chunk:
                            continue
                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue

                        message = data.get("message") if isinstance(data.get("message"), dict) else {}
                        content = message.get("content", "") if isinstance(message.get("content"), str) else ""
                        role = message.get("role", "assistant")
                        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
                        done = bool(data.get("done", False))
                        finish_reason = self._normalize_finish_reason(data.get("done_reason"), done=done)

                        yield {
                            "text": content,
                            "provider": "ollama",
                            "model": model,
                            "metadata": data,
                            "finish_reason": finish_reason,
                            "done": done,
                            "choices": [
                                {
                                    "delta": {
                                        "role": role,
                                        "content": content,
                                        "tool_calls": tool_calls,
                                    },
                                    "finish_reason": finish_reason,
                                }
                            ],
                        }
                        await asyncio.sleep(0.01)
        except Exception as e:
            yield self._format_error(e, model, done=True)

    def _build_ollama_options(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build Ollama options object from BrainDrive parameters.
        Maps BrainDrive/OpenAI-style params to Ollama format.

        Reference: https://github.com/ollama/ollama/blob/main/docs/modelfile.md
        """
        import logging
        logger = logging.getLogger(__name__)

        options = {}

        # Direct parameter mappings (BrainDrive param -> Ollama param)
        param_mappings = {
            "context_window": "num_ctx",      # Context window size
            "temperature": "temperature",      # Creativity control
            "top_p": "top_p",                 # Nucleus sampling
            "top_k": "top_k",                 # Token selection limit
            "seed": "seed",                   # Random seed
            "max_tokens": "num_predict",      # Max tokens to generate
            "min_p": "min_p",                 # Alternative to top_p
        }

        # Map direct parameters
        for brain_param, ollama_param in param_mappings.items():
            if brain_param in params and params[brain_param] is not None:
                options[ollama_param] = params[brain_param]
                logger.debug(f"Mapped {brain_param}={params[brain_param]} -> {ollama_param}")

        # Handle OpenAI-style penalties → repeat_penalty
        # Ollama doesn't support frequency_penalty or presence_penalty directly
        freq_penalty = params.get("frequency_penalty", 0) or 0
        pres_penalty = params.get("presence_penalty", 0) or 0

        if freq_penalty != 0 or pres_penalty != 0:
            # Convert from OpenAI range (-2.0 to 2.0) to Ollama range (0.0 to 2.0)
            # Formula: repeat_penalty = 1.0 + (max_penalty / 2)
            penalty = max(abs(freq_penalty), abs(pres_penalty))
            repeat_penalty = 1.0 + (penalty / 2.0)
            options["repeat_penalty"] = max(0.0, min(2.0, repeat_penalty))
            logger.debug(f"Mapped frequency/presence_penalty to repeat_penalty: {repeat_penalty}")

        # Handle stop sequences (can be list or string)
        stop_sequences = params.get("stop_sequences") or params.get("stop")
        if stop_sequences:
            if isinstance(stop_sequences, list) and len(stop_sequences) > 0:
                options["stop"] = stop_sequences
            elif isinstance(stop_sequences, str):
                options["stop"] = [stop_sequences]
            logger.debug(f"Added stop sequences: {options.get('stop')}")

        logger.info(f"[OLLAMA] Built Ollama options: {options}")
        return options

    def _normalize_finish_reason(self, done_reason: Any, done: bool) -> Optional[str]:
        """
        Normalize Ollama done_reason into provider-agnostic finish reasons.
        Token-limit truncation is normalized to "length".
        """
        if not done:
            return None

        if done_reason is None:
            return "stop"

        normalized = str(done_reason).strip().lower()
        if not normalized:
            return "stop"

        if normalized in {"length", "max_tokens", "max_token", "token_limit", "context_length"}:
            return "length"
        if normalized in {"stop", "eos", "end_turn"}:
            return "stop"
        return normalized

    async def _call_ollama_api(self, prompt: str, model: str, params: Dict[str, Any], is_streaming: bool = False) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)

        # Build Ollama options from params
        options = self._build_ollama_options(params)

        # Build payload with options object (Ollama API format)
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options  # All params go in options!
        }

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        # Log the actual URL being called
        api_url = f"{self.server_url}/api/generate"
        logger.info(f"[OLLAMA] Making Ollama API call to: {api_url}")
        logger.info(f"[OLLAMA] Payload options: {options}")

        try:
            # Large models can take a long time to start; increase timeout generously
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(api_url, json=payload, headers=headers)
                # If Ollama returns an error, capture the body for details
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as http_err:
                    detail = await self._extract_error_detail(http_err.response)
                    return self._format_error(f"{http_err} | {detail}", model)
                result = response.json()
                done = bool(result.get("done", False))
                return {
                    "text": result.get("response", ""),
                    "provider": "ollama",
                    "model": model,
                    "metadata": result,
                    "finish_reason": self._normalize_finish_reason(result.get("done_reason"), done=done),
                }
        except httpx.ConnectError as e:
            logger.error(f"[OLLAMA] ERROR: Cannot connect to Ollama server at {api_url}")
            return {
                "error": f"Cannot connect to Ollama server at {self.server_url}. "
                        f"Please check if the server is running and accessible.",
                "provider": "ollama",
                "model": model,
                "server_name": self.server_name,
                "server_url": self.server_url
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error(f"[OLLAMA] ERROR: Model '{model}' not found on server {self.server_name}")
                return {
                    "error": f"Model '{model}' not found on Ollama server '{self.server_name}'. "
                            f"Please check if the model is installed or use a different model.",
                    "provider": "ollama",
                    "model": model,
                    "server_name": self.server_name,
                    "server_url": self.server_url
                }
            else:
                logger.error(f"[OLLAMA] ERROR: HTTP error {e.response.status_code} from server {self.server_name}")
                return {
                    "error": f"HTTP {e.response.status_code} error from Ollama server '{self.server_name}': {e.response.text}",
                    "provider": "ollama",
                    "model": model,
                    "server_name": self.server_name,
                    "server_url": self.server_url
                }
        except Exception as e:
            return self._format_error(e, model)

    async def _stream_ollama_api(self, prompt: str, model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        import logging
        logger = logging.getLogger(__name__)

        # Build Ollama options from params
        options = self._build_ollama_options(params)

        # Build payload with options object (Ollama API format)
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": options  # All params go in options!
        }

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        logger.info(f"[OLLAMA] Streaming Ollama API call with options: {options}")

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", f"{self.server_url}/api/generate", json=payload, headers=headers) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as http_err:
                        detail = await self._extract_error_detail(http_err.response)
                        yield self._format_error(f"{http_err} | {detail}", model, done=True)
                        return
                    
                    try:
                        async for chunk in response.aiter_lines():
                            if chunk:
                                try:
                                    data = json.loads(chunk)
                                    done = bool(data.get("done", False))
                                    yield {
                                        "text": data.get("response", ""),
                                        "provider": "ollama",
                                        "model": model,
                                        "metadata": data,
                                        "finish_reason": self._normalize_finish_reason(data.get("done_reason"), done=done),
                                        "done": done
                                    }
                                    await asyncio.sleep(0.01)
                                except json.JSONDecodeError:
                                    continue
                    except asyncio.CancelledError:
                        print("Streaming was cancelled at the response level")
                        # Try to close the response gracefully
                        try:
                            response.aclose()
                        except:
                            pass
                        raise
        except asyncio.CancelledError:
            print("Streaming was cancelled at the client level")
            raise
        except Exception as e:
            yield self._format_error(e, model, done=True)

    async def _extract_error_detail(self, response: httpx.Response) -> str:
        """Extract a human-readable error detail from an HTTP error response."""
        if response is None:
            return ""
        try:
            # Try JSON first
            data = response.json()
            if isinstance(data, dict):
                return data.get("error") or data.get("message") or json.dumps(data)
            return str(data)
        except Exception:
            try:
                text = response.text
                return text.strip()[:1000]  # limit size
            except Exception:
                return ""

    def _format_error(self, error, model, done=False):
        error_response = {
            "error": True,
            "provider": "ollama",
            "model": model,
            "message": str(error)
        }
        if done:
            error_response["done"] = True
        return error_response

    async def validate_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        server_url = config.get("server_url", "http://localhost:11434")
        api_key = config.get("api_key", "")
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{server_url}/api/version", headers=headers)
                response.raise_for_status()
                return {
                    "status": "success",
                    "version": response.json().get("version", "unknown"),
                    "provider": "ollama"
                }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "provider": "ollama"
            }

    def _format_chat_messages(self, messages: List[Dict[str, Any]]) -> str:
        try:
            print(f"Formatting {len(messages)} messages")
            formatted = []
            for i, msg in enumerate(messages):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                tag = "system" if role == "system" else ("assistant" if role == "assistant" else "user")
                formatted_msg = f"<{tag}>\n{content}\n</{tag}>"
                print(f"  Formatting message {i+1}: role={role}, tag={tag}")
                print(f"  Formatted message: {formatted_msg}")
                formatted.append(formatted_msg)
            result = "\n".join(formatted)
            print(f"Final formatted result length: {len(result)} characters")
            print(f"FINAL FORMATTED PROMPT:\n{result}")
            return result
        except Exception as e:
            print(f"Chat formatting error: {e}")
            return "Hello, can you help me?"

    def _normalize_chat_messages_for_ollama(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize chat history into Ollama-native tool-call message format."""
        normalized: List[Dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue

            raw_role = msg.get("role")
            role = raw_role if isinstance(raw_role, str) else "user"
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"

            raw_content = msg.get("content", "")
            if isinstance(raw_content, str):
                content = raw_content
            elif raw_content is None:
                content = ""
            else:
                try:
                    content = json.dumps(raw_content, ensure_ascii=False)
                except Exception:
                    content = str(raw_content)

            entry: Dict[str, Any] = {
                "role": role,
                "content": content,
            }

            if role == "assistant":
                tool_calls = self._normalize_tool_calls_for_ollama(msg.get("tool_calls"))
                if tool_calls:
                    entry["tool_calls"] = tool_calls

            normalized.append(entry)

        return normalized

    def _normalize_tool_calls_for_ollama(self, raw_tool_calls: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw_tool_calls, list):
            return []

        normalized_calls: List[Dict[str, Any]] = []
        for call in raw_tool_calls:
            if not isinstance(call, dict):
                continue

            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = function.get("name") or call.get("name")
            if not isinstance(name, str) or not name.strip():
                continue

            raw_arguments = function.get("arguments")
            if raw_arguments is None:
                raw_arguments = call.get("arguments")
            arguments = self._coerce_tool_arguments_object(raw_arguments)

            normalized_call: Dict[str, Any] = {
                "function": {
                    "name": name.strip(),
                    "arguments": arguments,
                }
            }

            call_id = call.get("id")
            if isinstance(call_id, str) and call_id.strip():
                normalized_call["id"] = call_id.strip()

            normalized_calls.append(normalized_call)

        return normalized_calls

    def _coerce_tool_arguments_object(self, raw_arguments: Any) -> Dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments

        if isinstance(raw_arguments, str):
            stripped = raw_arguments.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed
                return {"value": parsed}
            except Exception:
                return {"_raw": stripped}

        if raw_arguments is None:
            return {}

        return {"value": raw_arguments}
