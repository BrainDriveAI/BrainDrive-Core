from typing import List, Dict, Any, Optional, Tuple


def apply_persona_prompt_and_params(
    messages: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]],
    persona_system_prompt: Optional[str],
    persona_model_settings: Optional[Dict[str, Any]],
    max_history: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Ensure the persona system prompt is the leading system message and merge persona model settings into params.
    Optionally trims history (removing oldest non-system messages) while preserving the system prompt and the latest turns.
    """
    params = params.copy() if params else {}
    if persona_model_settings:
        params.update(persona_model_settings)

    # Remove existing system messages; we'll re-insert persona system prompt if provided
    non_system_messages = [m for m in messages if m.get("role") != "system"]
    updated_messages: List[Dict[str, Any]] = []

    if persona_system_prompt:
        updated_messages.append({"role": "system", "content": persona_system_prompt})

    # Apply optional trimming on history (non-system only)
    if max_history is not None and max_history > 0 and len(non_system_messages) > max_history:
        non_system_messages = non_system_messages[-max_history:]

    updated_messages.extend(non_system_messages)
    return updated_messages, params
