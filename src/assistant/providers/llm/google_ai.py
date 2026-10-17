import os
from google import genai
from google.genai import types
from loguru import logger

class GoogleAIProvider:
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_name = model_name
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY is not set in environment variables.")
        self.client = genai.Client(api_key=api_key)

    async def generate_response(self, prompt: str, system_instruction: str = None) -> str:
        try:
            config = None
            if system_instruction:
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction
                )
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config
            )
            return response.text
        except Exception as e:
            logger.error(f"Error generating response from Google AI: {e}")
            raise e