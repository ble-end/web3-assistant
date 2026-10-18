"""Google GenAI (Gemini) provider wrapper.

Handles communication with the Google GenAI SDK for natural conversation.
"""
import logging
import os
from google import genai
from google.genai import types

log = logging.getLogger(__name__)


class GoogleAIProvider:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            log.warning("No Gemini API key found in environment variables.")
        self.client = genai.Client(api_key=api_key) if api_key else None
        # Using the requested model
        self.model_name = "gemini-3.8-flash"

    async def generate_response(self, prompt: str) -> str:
        if not self.client:
            return "AI provider is not configured (missing API key)."
        try:
            # Configure generation parameters to keep responses concise and fast
            config = types.GenerateContentConfig(
                max_output_tokens=500,
                temperature=0.7,
            )
            
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )
            return response.text if response and response.text else "I received an empty response."
        except Exception as e:
            log.error(f"Error calling Gemini API: {e}")
            raise