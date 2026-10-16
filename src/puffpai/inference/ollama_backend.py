"""
Ollama LLM Backend for PuffPAI (Development)

Wraps Ollama's HTTP API for local LLM inference.
Used during development on Windows/Linux with Ollama installed.
"""

from __future__ import annotations

import json
import requests

from .base import LLMBackend
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy


class OllamaBackend(LLMBackend):
    """Ollama HTTP backend for development use.

    Requires a running Ollama server (default: http://localhost:11434).
    """

    def __init__(self, url: str = "http://localhost:11434",
                 model: str = "qwen2.5:7b-instruct",
                 embed_model: str | None = None,
                 num_ctx: int = 2048,
                 num_predict: int = 256,
                 timeout: int = 30,
                 think: bool | None = None):
        self._url = url.rstrip('/')
        self._model = model
        self._embed_model = embed_model or model
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._timeout = timeout
        # Ollama's "think" switch for thinking models (qwen3.5): None leaves
        # the model's default; False makes short calls answer at once instead
        # of spending their whole token budget thinking.
        self._think = think

    def _build_payload(self, messages: list, temperature: float,
                       max_tokens: int, stream: bool,
                       format: dict | None = None,
                       tools: list | None = None) -> dict:
        """Build a native /api/chat payload.

        Uses the native endpoint rather than /v1/chat/completions — the
        OpenAI-compat endpoint silently ignores the "options" field, so
        num_ctx/num_predict would never be applied.

        ``format`` is a JSON schema Ollama turns into a grammar while decoding
        (the answer can only take that shape); ``tools`` is Ollama's tool list.
        Both are left out of the payload when ``None``, so a call without them
        is exactly what it was before they existed.
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
            "keep_alive": "30m",
            "options": {
                "temperature": temperature,
                "num_ctx": self._num_ctx,
                "num_predict": min(max_tokens, self._num_predict),
            },
        }
        if self._think is not None:
            payload["think"] = self._think
        if format is not None:
            payload["format"] = format
        if tools is not None:
            payload["tools"] = tools
        return payload

    def chat(self, messages: list, temperature: float = 0.7,
             max_tokens: int = 2048, format: dict | None = None,
             tools: list | None = None):
        """Send a chat request to Ollama's native /api/chat endpoint.

        Returns the answer text. With ``tools`` (even an empty list) it
        returns Ollama's whole message instead,
        ``{"role", "content", "tool_calls": [...]}``, so a caller can read the
        tool calls; errors are then ``{"role": "assistant", "content":
        "[Error] ...", "tool_calls": []}``.
        """
        if tools is not None:
            return self._chat_message(messages, temperature, max_tokens,
                                      format, tools)
        try:
            response = requests.post(  # lan-egress: Ollama chat, LAN
                f"{self._url}/api/chat",
                json=self._build_payload(messages, temperature,
                                         max_tokens, stream=False,
                                         format=format),
                timeout=self._timeout,
                proxies=_no_proxies(),
            )
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]
        except requests.exceptions.ConnectionError:
            return "[Error] Cannot connect to Ollama. Is it running?"
        except requests.exceptions.Timeout:
            return "[Error] Ollama request timed out."
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            return f"[Error] Unexpected Ollama response: {e}"
        except Exception as e:
            return f"[Error] Ollama error: {e}"

    def _chat_message(self, messages: list, temperature: float,
                      max_tokens: int, format: dict | None,
                      tools: list) -> dict:
        """One non-streamed call with ``tools``: Ollama's whole message."""
        def _error(text: str) -> dict:
            return {"role": "assistant", "content": text, "tool_calls": []}
        try:
            response = requests.post(  # lan-egress: Ollama chat with tools, LAN
                f"{self._url}/api/chat",
                json=self._build_payload(messages, temperature, max_tokens,
                                         stream=False, format=format,
                                         tools=tools),
                timeout=self._timeout,
                proxies=_no_proxies(),
            )
            response.raise_for_status()
            message = response.json()["message"]
            if not isinstance(message, dict):
                return _error("[Error] Unexpected Ollama response: no message")
            calls = message.get("tool_calls")
            return {"role": str(message.get("role") or "assistant"),
                    "content": str(message.get("content") or ""),
                    "tool_calls": calls if isinstance(calls, list) else []}
        except requests.exceptions.ConnectionError:
            return _error("[Error] Cannot connect to Ollama. Is it running?")
        except requests.exceptions.Timeout:
            return _error("[Error] Ollama request timed out.")
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            return _error(f"[Error] Unexpected Ollama response: {e}")
        except Exception as e:
            return _error(f"[Error] Ollama error: {e}")

    def chat_turn(self, messages: list, tools: list | None = None,
                  on_token=None, on_thinking=None, temperature: float = 0.3,
                  max_tokens: int = 2048) -> dict:
        """One streamed turn of the tool loop (:mod:`puffpai.brain_loop`).

        Words go to ``on_token`` as they arrive and thinking to
        ``on_thinking``; the return is the whole message,
        ``{"role", "content", "tool_calls"}``. When Ollama refuses or cannot
        be reached it is ``{..., "error": <Ollama's own words>, "status"}``
        (for example "... does not support tools"). Never raises.
        """
        def _error(text: str, status: int | None = None) -> dict:
            return {"role": "assistant", "content": "", "tool_calls": [],
                    "error": text, "status": status}

        def _call(fn, text):
            if fn is not None and text:
                try:
                    fn(text)
                except Exception:
                    pass
        try:
            response = requests.post(  # lan-egress: Ollama chat with tools, LAN
                f"{self._url}/api/chat",
                json=self._build_payload(messages, temperature, max_tokens,
                                         stream=True, tools=tools),
                timeout=self._timeout, stream=True, proxies=_no_proxies(),
            )
            if response.status_code != 200:
                detail = ""
                try:
                    body = response.json()
                    detail = str(body.get("error") or "") if isinstance(body, dict) else ""
                except Exception:
                    detail = str(getattr(response, "text", "") or "")[:300]
                return _error(detail or f"Ollama answered HTTP {response.status_code}",
                              response.status_code)
            content: list[str] = []
            calls: list = []
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    return _error(str(chunk["error"]))
                msg = chunk.get("message") if isinstance(chunk.get("message"), dict) else {}
                _call(on_thinking, msg.get("thinking") or "")
                token = msg.get("content") or ""
                if token:
                    content.append(token)
                    _call(on_token, token)
                if isinstance(msg.get("tool_calls"), list):
                    calls.extend(msg["tool_calls"])
                if chunk.get("done"):
                    break
            return {"role": "assistant", "content": "".join(content), "tool_calls": calls}
        except requests.exceptions.ConnectionError:
            return _error("Cannot connect to Ollama. Is it running?")
        except requests.exceptions.Timeout:
            return _error("Ollama request timed out.")
        except Exception as e:
            return _error(f"Ollama error: {e}")

    def prewarm(self, keep_alive: str = "10m") -> bool:
        """Load the model now (an empty chat), so a later call does not wait.

        Never raises; True when Ollama answered.
        """
        try:
            response = requests.post(  # lan-egress: Ollama model load, LAN
                f"{self._url}/api/chat",
                json={"model": self._model, "messages": [],
                      "keep_alive": keep_alive},
                timeout=self._timeout,
                proxies=_no_proxies(),
            )
            return response.status_code == 200
        except Exception:
            return False

    def chat_stream(self, messages: list, temperature: float = 0.7,
                    max_tokens: int = 2048, format: dict | None = None,
                    on_line=None):
        """Stream tokens from Ollama as they arrive.

        ``format`` is as in :meth:`chat`. ``on_line`` is called with every
        decoded line Ollama sends (a dict), including the last one that
        carries Ollama's own timings (``eval_count``, ``eval_duration``); an
        error raised by it is ignored.
        """
        try:
            response = requests.post(  # lan-egress: Ollama chat stream, LAN
                f"{self._url}/api/chat",
                json=self._build_payload(messages, temperature,
                                         max_tokens, stream=True,
                                         format=format),
                timeout=self._timeout,
                stream=True,
                proxies=_no_proxies(),
            )
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8", errors="replace"))
                    if on_line is not None and isinstance(chunk, dict):
                        try:
                            on_line(chunk)
                        except Exception:
                            pass
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except (ValueError, KeyError, IndexError):
                    continue
        except requests.exceptions.ConnectionError:
            yield "[Error] Cannot connect to Ollama. Is it running?"
        except requests.exceptions.Timeout:
            yield "[Error] Ollama request timed out."
        except Exception as e:
            yield f"[Error] Ollama error: {e}"

    def embed(self, text: str) -> list:
        """Generate embedding via Ollama's embedding endpoint."""
        try:
            response = requests.post(  # lan-egress: Ollama embeddings, LAN
                f"{self._url}/api/embeddings",
                json={"model": self._embed_model, "prompt": text},
                timeout=30,
                proxies=_no_proxies(),
            )
            response.raise_for_status()
            return response.json().get("embedding", [])
        except Exception:
            return []

    def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            response = requests.get(f"{self._url}/api/tags", timeout=3, proxies=_no_proxies())  # lan-egress: Ollama /api/tags, LAN
            return response.status_code == 200
        except Exception:
            return False

    @property
    def model_name(self) -> str:
        return self._model
