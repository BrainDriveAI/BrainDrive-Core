"""
OpenRouter provider implementation.
"""
from typing import Dict, List, Any, AsyncGenerator
from openai import AsyncOpenAI
from .base import AIProvider


class OpenRouterProvider(AIProvider):
    """OpenRouter provider implementation."""
    
    @property
    def provider_name(self) -> str:
        return "openrouter"
    
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize the provider with configuration."""
        self.api_key = config.get("api_key", "")
        self.base_url = "https://openrouter.ai/api/v1"
        self.server_name = config.get("server_name", "OpenRouter API")
        client_headers = {
            "HTTP-Referer": config.get("client_referer", "https://app.braindrive.ai"),
            "X-Title": config.get("client_title", "BrainDrive Chat")
        }
        # Remove headers that were explicitly set to None/empty
        default_headers = {key: value for key, value in client_headers.items() if value}
        
        # Initialize the OpenAI client with OpenRouter configuration
        client_kwargs = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "default_headers": default_headers or None
        }
            
        self.client = AsyncOpenAI(**client_kwargs)
        return True
    
    async def get_models(self) -> List[Dict[str, Any]]:
        """Get available models from OpenRouter."""
        try:
            models = await self.client.models.list()
            return [
                {
                    "id": model.id,
                    "name": model.id,
                    "provider": "openrouter",
                    "metadata": {
                        "created": model.created,
                        "owned_by": model.owned_by,
                        "context_length": getattr(model, 'context_length', None),
                        "pricing": getattr(model, 'pricing', None)
                    }
                }
                for model in models.data
            ]
        except Exception as e:
            # If models.list fails, return a list of common OpenRouter models
            return [
                {
                    "id": "openai/gpt-4",
                    "name": "GPT-4",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "openai",
                        "context_length": 8192
                    }
                },
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "openai",
                        "context_length": 128000
                    }
                },
                {
                    "id": "openai/gpt-3.5-turbo",
                    "name": "GPT-3.5 Turbo",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "openai",
                        "context_length": 16385
                    }
                },
                {
                    "id": "anthropic/claude-3.5-sonnet",
                    "name": "Claude 3.5 Sonnet",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "anthropic",
                        "context_length": 200000
                    }
                },
                {
                    "id": "anthropic/claude-3-haiku",
                    "name": "Claude 3 Haiku",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "anthropic",
                        "context_length": 200000
                    }
                },
                {
                    "id": "google/gemini-pro",
                    "name": "Gemini Pro",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "google",
                        "context_length": 32768
                    }
                },
                {
                    "id": "meta-llama/llama-3.1-8b-instruct",
                    "name": "Llama 3.1 8B Instruct",
                    "provider": "openrouter",
                    "metadata": {
                        "owned_by": "meta-llama",
                        "context_length": 8192
                    }
                }
            ]
    
    async def generate_text(self, prompt: str, model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate text from a prompt (batch/full response mode).
        
        Args:
            prompt: The input prompt text
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Returns:
            A dictionary containing the generated text and metadata
        """
        # Create a copy of params to avoid modifying the original
        payload_params = params.copy()
        
        # Ensure stream is not set for batch mode
        if "stream" in payload_params:
            del payload_params["stream"]
        
        # Extract parameters that should not be passed to the API
        max_tokens = payload_params.pop("max_tokens", None)
        temperature = payload_params.pop("temperature", None)
        top_p = payload_params.pop("top_p", None)
        
        # Build the API parameters
        api_params = {
            "model": model,
            **payload_params
        }
        
        # Add optional parameters if provided
        if max_tokens is not None:
            api_params["max_tokens"] = max_tokens
        if temperature is not None:
            api_params["temperature"] = temperature
        if top_p is not None:
            api_params["top_p"] = top_p
        
        try:
            # Call the OpenRouter API
            response = await self.client.completions.create(
                prompt=prompt,
                **api_params
            )
            
            return {
                "text": response.choices[0].text,
                "provider": "openrouter",
                "model": model,
                "finish_reason": response.choices[0].finish_reason,
                "metadata": {
                    "id": response.id,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    } if response.usage else None
                }
            }
        except Exception as e:
            return {
                "error": str(e),
                "provider": "openrouter",
                "model": model
            }
    
    async def generate_stream(self, prompt: str, model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate streaming text from a prompt.
        
        Args:
            prompt: The input prompt text
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Yields:
            Dictionary containing streaming text chunks and metadata
        """
        # Create a copy of params to avoid modifying the original
        payload_params = params.copy()
        
        # Set stream to True for streaming mode
        payload_params["stream"] = True
        
        # Extract parameters that should not be passed to the API
        max_tokens = payload_params.pop("max_tokens", None)
        temperature = payload_params.pop("temperature", None)
        top_p = payload_params.pop("top_p", None)
        
        # Build the API parameters
        api_params = {
            "model": model,
            **payload_params
        }
        
        # Add optional parameters if provided
        if max_tokens is not None:
            api_params["max_tokens"] = max_tokens
        if temperature is not None:
            api_params["temperature"] = temperature
        if top_p is not None:
            api_params["top_p"] = top_p
        
        try:
            # Call the OpenRouter API with streaming
            stream = await self.client.completions.create(
                prompt=prompt,
                **api_params
            )
            
            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta_text = ""
                if hasattr(choice, "delta") and choice.delta is not None:
                    delta_text = getattr(choice.delta, "text", None) or ""
                finish_reason = choice.finish_reason

                # Emit terminal finish chunks even when content is empty.
                if delta_text or finish_reason is not None:
                    yield {
                        "choices": [
                            {
                                "text": delta_text,
                                "finish_reason": finish_reason
                            }
                        ],
                        "provider": "openrouter",
                        "model": model,
                        "metadata": {
                            "id": chunk.id
                        }
                    }
        except Exception as e:
            yield {
                "error": str(e),
                "provider": "openrouter",
                "model": model
            }
    
    async def chat_completion(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a chat completion.
        
        Args:
            messages: List of message objects with 'role' and 'content'
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Returns:
            A dictionary containing the chat completion and metadata
        """
        # Create a copy of params to avoid modifying the original
        payload_params = params.copy()
        
        # Ensure stream is not set for batch mode
        if "stream" in payload_params:
            del payload_params["stream"]
        
        # Extract parameters that should not be passed to the API
        max_tokens = payload_params.pop("max_tokens", None)
        temperature = payload_params.pop("temperature", None)
        top_p = payload_params.pop("top_p", None)
        payload_params.pop("context_window", None)
        payload_params.pop("stop_sequences", None)
        
        # Build the API parameters
        api_params = {
            "model": model,
            "messages": messages,
            **payload_params
        }
        
        # Add optional parameters if provided
        if max_tokens is not None:
            api_params["max_tokens"] = max_tokens
        if temperature is not None:
            api_params["temperature"] = temperature
        if top_p is not None:
            api_params["top_p"] = top_p
        
        try:
            # Call the OpenRouter API
            response = await self.client.chat.completions.create(**api_params)
            
            return {
                "content": response.choices[0].message.content,
                "role": response.choices[0].message.role,
                "provider": "openrouter",
                "model": model,
                "finish_reason": response.choices[0].finish_reason,
                "metadata": {
                    "id": response.id,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    } if response.usage else None
                }
            }
        except Exception as e:
            raise RuntimeError(f"OpenRouter chat completion failed: {e}") from e
    
    async def chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate a streaming chat completion.
        
        Args:
            messages: List of message objects with 'role' and 'content'
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Yields:
            Dictionary containing streaming chat completion chunks and metadata
        """
        # Create a copy of params to avoid modifying the original
        payload_params = params.copy()
        
        # Set stream to True for streaming mode
        payload_params["stream"] = True
        
        # Extract parameters that should not be passed to the API
        max_tokens = payload_params.pop("max_tokens", None)
        temperature = payload_params.pop("temperature", None)
        top_p = payload_params.pop("top_p", None)
        payload_params.pop("context_window", None)
        payload_params.pop("stop_sequences", None)
        
        # Build the API parameters
        api_params = {
            "model": model,
            "messages": messages,
            **payload_params
        }
        
        # Add optional parameters if provided
        if max_tokens is not None:
            api_params["max_tokens"] = max_tokens
        if temperature is not None:
            api_params["temperature"] = temperature
        if top_p is not None:
            api_params["top_p"] = top_p
        
        try:
            # Call the OpenRouter API with streaming
            stream = await self.client.chat.completions.create(**api_params)
            
            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta
                content = getattr(delta, "content", None) or ""
                role = getattr(delta, "role", None) or "assistant"
                finish_reason = choice.finish_reason

                # Emit terminal finish chunks even when content is empty.
                if content or finish_reason is not None:
                    yield {
                        "choices": [
                            {
                                "delta": {
                                    "content": content,
                                    "role": role
                                },
                                "finish_reason": finish_reason
                            }
                        ],
                        "provider": "openrouter",
                        "model": model,
                        "metadata": {
                            "id": chunk.id
                        }
                    }
        except Exception as e:
            raise RuntimeError(f"OpenRouter streaming chat failed: {e}") from e
    
    async def validate_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate connection to OpenRouter."""
        try:
            # Initialize with the provided config
            await self.initialize(config)
            
            # Try to get models to validate the connection
            models = await self.get_models()
            
            return {
                "valid": True,
                "provider": "openrouter",
                "message": f"Successfully connected to OpenRouter. Found {len(models)} models.",
                "models_count": len(models)
            }
        except Exception as e:
            return {
                "valid": False,
                "provider": "openrouter",
                "error": str(e),
                "message": "Failed to connect to OpenRouter"
            } 
