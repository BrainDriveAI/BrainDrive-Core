"""
OpenAI provider implementation.
"""
from typing import Dict, List, Any, AsyncGenerator
from openai import AsyncOpenAI
from .base import AIProvider


class OpenAIProvider(AIProvider):
    """OpenAI provider implementation."""
    
    @property
    def provider_name(self) -> str:
        return "openai"
    
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize the provider with configuration."""
        self.api_key = config.get("api_key", "")
        self.organization = config.get("organization", None)
        self.base_url = config.get("base_url", None)
        self.server_name = config.get("server_name", "OpenAI API")

        
        # Initialize the OpenAI client
        client_kwargs = {"api_key": self.api_key}
        if self.organization:
            client_kwargs["organization"] = self.organization
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
            
        self.client = AsyncOpenAI(**client_kwargs)
        return True
    
    async def get_models(self) -> List[Dict[str, Any]]:
        """Get available models from the provider."""
        models = await self.client.models.list()
        return [
            {
                "id": model.id,
                "name": model.id,
                "provider": "openai",
                "metadata": {
                    "created": model.created,
                    "owned_by": model.owned_by
                }
            }
            for model in models.data
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
            # Call the OpenAI API
            response = await self.client.completions.create(
                prompt=prompt,
                **api_params
            )
            
            return {
                "text": response.choices[0].text,
                "provider": "openai",
                "model": model,
                "finish_reason": response.choices[0].finish_reason,
                "metadata": {
                    "id": response.id,
                    "usage": response.usage.model_dump() if response.usage else None
                }
            }
        except Exception as e:
            return {
                "error": True,
                "message": f"OpenAI API error: {str(e)}",
                "provider": "openai",
                "model": model
            }
    
    async def generate_stream(self, prompt: str, model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate streaming text from a prompt (streaming mode).
        
        Args:
            prompt: The input prompt text
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Yields:
            Dictionaries containing chunks of generated text and metadata
        """
        # Create a copy of params to avoid modifying the original
        payload_params = params.copy()
        
        # Ensure stream is set to True for streaming mode
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
            # Call the OpenAI API with streaming
            stream = await self.client.completions.create(
                prompt=prompt,
                **api_params
            )
            
            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                chunk_text = choice.text or ""
                finish_reason = choice.finish_reason

                # Emit terminal finish chunks even when content is empty.
                if chunk_text or finish_reason is not None:
                    yield {
                        "text": chunk_text,
                        "provider": "openai",
                        "model": model,
                        "finish_reason": finish_reason,
                        "done": finish_reason is not None,
                        "metadata": {
                            "id": chunk.id
                        }
                    }
        except Exception as e:
            yield {
                "error": True,
                "message": f"OpenAI API error: {str(e)}",
                "provider": "openai",
                "model": model,
                "done": True
            }
    
    async def chat_completion(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a chat completion (batch/full response mode).
        
        Args:
            messages: List of chat messages with role and content
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Returns:
            A dictionary containing the generated chat completion and metadata
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
            # Call the OpenAI API
            response = await self.client.chat.completions.create(
                messages=messages,
                **api_params
            )
            
            result = {
                "text": response.choices[0].message.content,
                "provider": "openai",
                "model": model,
                "finish_reason": response.choices[0].finish_reason,
                "metadata": {
                    "id": response.id,
                    "usage": response.usage.model_dump() if response.usage else None
                }
            }
            
            # Add chat-specific fields
            result["choices"] = [
                {
                    "message": {
                        "role": response.choices[0].message.role,
                        "content": response.choices[0].message.content
                    },
                    "finish_reason": response.choices[0].finish_reason
                }
            ]
            
            return result
        except Exception as e:
            return {
                "error": True,
                "message": f"OpenAI API error: {str(e)}",
                "provider": "openai",
                "model": model
            }
    
    async def chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate a streaming chat completion (streaming mode).
        
        Args:
            messages: List of chat messages with role and content
            model: The model to use for generation
            params: Additional parameters for the generation
            
        Yields:
            Dictionaries containing chunks of the chat completion and metadata
        """
        # Create a copy of params to avoid modifying the original
        payload_params = params.copy()
        
        # Ensure stream is set to True for streaming mode
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
            # Call the OpenAI API with streaming
            stream = await self.client.chat.completions.create(
                messages=messages,
                **api_params
            )
            
            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta
                content = getattr(delta, "content", None) or ""
                role = getattr(delta, "role", None)
                finish_reason = choice.finish_reason

                # Emit terminal finish chunks even when content is empty.
                if content or finish_reason is not None or role is not None:
                    chunk_data = {
                        "text": content,
                        "provider": "openai",
                        "model": model,
                        "finish_reason": finish_reason,
                        "done": finish_reason is not None,
                        "metadata": {
                            "id": chunk.id
                        }
                    }

                    # Add chat-specific fields
                    chunk_data["choices"] = [
                        {
                            "delta": {
                                "role": role,
                                "content": content
                            },
                            "finish_reason": finish_reason
                        }
                    ]

                    yield chunk_data
        except Exception as e:
            yield {
                "error": True,
                "message": f"OpenAI API error: {str(e)}",
                "provider": "openai",
                "model": model,
                "done": True
            }
    
    async def validate_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate connection to the provider."""
        api_key = config.get("api_key", "")
        organization = config.get("organization", None)
        base_url = config.get("base_url", None)
        
        # Initialize a temporary client for validation
        client_kwargs = {"api_key": api_key}
        if organization:
            client_kwargs["organization"] = organization
        if base_url:
            client_kwargs["base_url"] = base_url
            
        try:
            client = AsyncOpenAI(**client_kwargs)
            models = await client.models.list()
            return {
                "status": "success",
                "models_count": len(models.data),
                "provider": "openai"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "provider": "openai"
            }
