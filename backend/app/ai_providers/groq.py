"""
Groq provider implementation.
"""
from typing import Dict, List, Any, AsyncGenerator
import groq
from .base import AIProvider


class GroqProvider(AIProvider):
    """Groq provider implementation."""
    
    @property
    def provider_name(self) -> str:
        return "groq"
    
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize the provider with configuration."""
        self.api_key = config.get("api_key", "")
        self.server_name = config.get("server_name", "Groq API")
        self.server_url = config.get("server_url", "https://api.groq.com")
        
        # Initialize the Groq client
        self.client = groq.AsyncGroq(api_key=self.api_key)
        
        # Initialize dynamic model mapping
        self.model_mapping = {}
        await self._build_model_mapping()
        return True
    
    async def _build_model_mapping(self):
        """Build the model mapping by fetching from Groq API."""
        try:
            models_response = await self.client.models.list()
            for model in models_response.data:
                model_id = getattr(model, 'id', None)
                if model_id:
                    self.model_mapping[model_id] = model_id
            
        except Exception as e:
            # If API fails, use empty mapping - let get_models handle fallback
            self.model_mapping = {}
    
    def _get_model_id(self, model_name: str) -> str:
        """Convert display name to model ID if needed."""
        # Since we use ID as name, just return the model_name directly
        return model_name
    
    async def get_models(self) -> List[Dict[str, Any]]:
        """Get available models from Groq."""
        try:
            models_response = await self.client.models.list()
            models = []
            for model in models_response.data:
                model_id = getattr(model, 'id', None)
                if model_id:
                    model_data = {
                        "id": model_id,
                        "name": model_id,  # Use ID as name - simple!
                        "provider": "groq",
                        "metadata": {
                            "owned_by": "groq",
                            "created_at": getattr(model, 'created_at', None),
                            "description": f"Groq model: {model_id}"
                        }
                    }
                    models.append(model_data)
            
            return models
            
        except Exception as e:
            # Return empty list if API fails
            return []
    
    async def generate_text(self, prompt: str, model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate text from a prompt using Groq API."""
        try:
            # Get the actual model ID from the mapping
            model_id = self._get_model_id(model)
            
            # Extract parameters
            temperature = params.get("temperature", 0.7)
            max_tokens = params.get("max_tokens", 1000)
            top_p = params.get("top_p", 1.0)
            
            # Make the API call
            response = await self.client.chat.completions.create(
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )
            
            return {
                "text": response.choices[0].message.content,
                "provider": "groq",
                "model": model,
                "finish_reason": response.choices[0].finish_reason,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else None,
                    "completion_tokens": response.usage.completion_tokens if hasattr(response.usage, 'completion_tokens') else None,
                    "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else None
                },
                "metadata": {
                    "id": response.id if hasattr(response, 'id') else None
                }
            }
            
        except Exception as e:
            return {
                "error": True,
                "message": f"Groq API error: {str(e)}",
                "provider": "groq",
                "model": model
            }

    async def generate_stream(self, prompt: str, model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming text from a prompt using Groq API."""
        try:
            # Get the actual model ID from the mapping
            model_id = self._get_model_id(model)
            
            # Extract parameters
            temperature = params.get("temperature", 0.7)
            max_tokens = params.get("max_tokens", 1000)
            top_p = params.get("top_p", 1.0)
            
            # Make the streaming API call
            stream = await self.client.chat.completions.create(
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                messages=[{"role": "user", "content": prompt}],
                stream=True
            )
            
            async for chunk in stream:
                try:
                    if chunk.choices[0].delta.content:
                        yield {
                            "text": chunk.choices[0].delta.content,
                            "provider": "groq",
                            "model": model,
                            "finish_reason": None,
                            "done": False,
                            "metadata": {
                                "id": chunk.id if hasattr(chunk, 'id') else None
                            }
                        }
                    elif chunk.choices[0].finish_reason:
                        yield {
                            "text": "",
                            "provider": "groq",
                            "model": model,
                            "finish_reason": chunk.choices[0].finish_reason,
                            "done": True,
                            "metadata": {
                                "id": chunk.id if hasattr(chunk, 'id') else None
                            }
                        }
                except Exception as chunk_error:
                    print(f"Error processing streaming chunk: {chunk_error}")
                    continue
                    
        except Exception as e:
            yield {
                "error": True,
                "message": f"Groq API streaming error: {str(e)}",
                "provider": "groq",
                "model": model,
                "done": True
            }

    async def chat_completion(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a chat completion using Groq API."""
        try:
            # Get the actual model ID from the mapping
            model_id = self._get_model_id(model)
            
            # Extract parameters
            temperature = params.get("temperature", 0.7)
            max_tokens = params.get("max_tokens", 1000)
            top_p = params.get("top_p", 1.0)
            
            # Make the API call
            response = await self.client.chat.completions.create(
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                messages=messages,
                stream=False
            )

            message = response.choices[0].message
            content = message.content or ""
            raw_tool_calls = getattr(message, "tool_calls", None) or []
            tool_calls = []
            for tool_call in raw_tool_calls:
                function = getattr(tool_call, "function", None)
                tool_calls.append(
                    {
                        "id": getattr(tool_call, "id", None),
                        "type": getattr(tool_call, "type", "function"),
                        "function": {
                            "name": getattr(function, "name", None),
                            "arguments": getattr(function, "arguments", None),
                        },
                    }
                )
            
            return {
                "choices": [{
                    "message": {
                        "content": content,
                        "role": "assistant",
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": response.choices[0].finish_reason
                }],
                "provider": "groq",
                "model": model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else None,
                    "completion_tokens": response.usage.completion_tokens if hasattr(response.usage, 'completion_tokens') else None,
                    "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else None
                },
                "metadata": {
                    "id": response.id if hasattr(response, 'id') else None
                },
                "tool_calls": tool_calls,
            }
            
        except Exception as e:
            return {
                "error": True,
                "message": f"Groq API error: {str(e)}",
                "provider": "groq",
                "model": model
            }

    async def chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate a streaming chat completion using Groq API."""
        try:
            # Get the actual model ID from the mapping
            model_id = self._get_model_id(model)
            
            # Extract parameters
            temperature = params.get("temperature", 0.7)
            max_tokens = params.get("max_tokens", 1000)
            top_p = params.get("top_p", 1.0)
            
            # Make the streaming API call
            stream = await self.client.chat.completions.create(
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                messages=messages,
                stream=True
            )
            
            async for chunk in stream:
                try:
                    delta_tool_calls = []
                    raw_delta_tool_calls = getattr(chunk.choices[0].delta, "tool_calls", None) or []
                    for tool_call in raw_delta_tool_calls:
                        function = getattr(tool_call, "function", None)
                        delta_tool_calls.append(
                            {
                                "id": getattr(tool_call, "id", None),
                                "type": getattr(tool_call, "type", "function"),
                                "function": {
                                    "name": getattr(function, "name", None),
                                    "arguments": getattr(function, "arguments", None),
                                },
                            }
                        )

                    if chunk.choices[0].delta.content or delta_tool_calls:
                        yield {
                            "choices": [{
                                "delta": {
                                    "content": chunk.choices[0].delta.content or "",
                                    "tool_calls": delta_tool_calls,
                                },
                                "finish_reason": None
                            }],
                            "provider": "groq",
                            "model": model,
                            "done": False,
                            "metadata": {
                                "id": chunk.id if hasattr(chunk, 'id') else None
                            }
                        }
                    elif chunk.choices[0].finish_reason:
                        yield {
                            "choices": [{
                                "delta": {},
                                "finish_reason": chunk.choices[0].finish_reason
                            }],
                            "provider": "groq",
                            "model": model,
                            "done": True,
                            "metadata": {
                                "id": chunk.id if hasattr(chunk, 'id') else None
                            }
                        }
                except Exception as chunk_error:
                    print(f"Error processing streaming chunk: {chunk_error}")
                    continue
                    
        except Exception as e:
            yield {
                "error": True,
                "message": f"Groq API streaming error: {str(e)}",
                "provider": "groq",
                "model": model,
                "done": True
            }

    async def validate_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate connection to the Groq provider."""
        try:
            api_key = config.get("api_key", "")
            if not api_key:
                return {
                    "valid": False,
                    "message": "API key is required",
                    "provider": "groq"
                }
            
            # Create a temporary client with the provided API key
            temp_client = groq.AsyncGroq(api_key=api_key)
            
            # Try to list models to validate the connection
            models_response = await temp_client.models.list()
            
            if models_response.data:
                return {
                    "valid": True,
                    "message": "Connection to Groq API successful",
                    "provider": "groq",
                    "models_count": len(models_response.data),
                    "available_models": [model.id for model in models_response.data[:5]]  # Show first 5 models
                }
            else:
                return {
                    "valid": False,
                    "message": "Connection successful but no models found",
                    "provider": "groq"
                }
                
        except Exception as e:
            return {
                "valid": False,
                "message": f"Connection failed: {str(e)}",
                "provider": "groq"
            }
    
    async def validate_api_key(self, api_key: str) -> Dict[str, Any]:
        """
        Validate the Groq API key by making a test request.
        
        Args:
            api_key: The API key to validate
            
        Returns:
            A dictionary containing validation results
        """
        try:
            # Create a temporary client with the provided API key
            temp_client = groq.AsyncGroq(api_key=api_key)
            
            # Try to list models to validate the API key
            models_response = await temp_client.models.list()
            
            if models_response.data:
                return {
                    "valid": True,
                    "message": "API key is valid",
                    "models_count": len(models_response.data)
                }
            else:
                return {
                    "valid": False,
                    "message": "API key is valid but no models found"
                }
                
        except Exception as e:
            return {
                "valid": False,
                "message": f"Invalid API key: {str(e)}"
            }
    
    async def get_usage_info(self) -> Dict[str, Any]:
        """
        Get usage information for the current API key.
        
        Returns:
            A dictionary containing usage information
        """
        try:
            # Note: Groq doesn't provide usage endpoints like OpenAI
            # This is a placeholder for future implementation
            return {
                "provider": "groq",
                "message": "Usage tracking not available for Groq API",
                "available": False
            }
        except Exception as e:
            return {
                "provider": "groq",
                "error": str(e),
                "available": False
            }
