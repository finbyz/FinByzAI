from io import BytesIO
import os
import requests
import base64
import time
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass
from google import genai
from langchain_core.language_models.base import (
    LanguageModelInput,
)
from langchain_core.runnables import RunnableConfig


@dataclass
class ImageResponse:
    """Response containing generated images and metadata"""
    images: List[str]  # URLs or base64 strings
    model: str
    provider: str
    prompt: str
    metadata: Optional[Dict[str, Any]] = None


class ImageGeneration:
    """Simple unified image generation class supporting multiple providers"""
    
    def __init__(self, model: str, api_key: str, **kwargs):
        self.full_model = model
        self.api_key = api_key
        self.config = kwargs
        
        # Parse provider and model from format: provider/model_name
        self.provider, self.model = self._parse_model(model)
    
    def _parse_model(self, model: str) -> tuple[str, str]:
        """Parse provider and model from provider/model_name format"""
        if '/' in model:
            provider_part, model_part = model.split('/', 1)
            provider = provider_part.lower()
            
            # Map provider names to internal names
            provider_mapping = {
                'openai': 'openai',
                'google': 'google', 
                'gemini': 'google',  # gemini is Google's brand
                'vertex': 'google',
                'stability': 'stability',
                'stabilityai': 'stability',
                'huggingface': 'huggingface',
                'hf': 'huggingface',
                'replicate': 'replicate'
            }
            
            if provider in provider_mapping:
                return provider_mapping[provider], model_part
            else:
                # If provider not recognized, treat as huggingface format (org/model)
                return 'huggingface', model
        else:
            # No slash, try to auto-detect from model name
            return self._detect_provider_fallback(model), model
    
    def _detect_provider_fallback(self, model: str) -> str:
        """Fallback detection for models without provider prefix"""
        model_lower = model.lower()
        
        if any(x in model_lower for x in ['dall-e', 'dalle', 'gpt']):
            return 'openai'
        elif any(x in model_lower for x in ['imagen', 'vertex', 'gemini']):
            return 'google'
        elif any(x in model_lower for x in ['stable', 'sdxl', 'sd-']):
            return 'stability'
        elif ':' in model or 'replicate' in model_lower:
            return 'replicate'
        else:
            return 'openai'  # Default fallback
    def invoke(
        self,
        input: LanguageModelInput,
        config: Optional[RunnableConfig] = None,
        *,
        stop: Optional[list[str]] = None,
        **kwargs: Any
    ):
        prompt: str = ""

        if hasattr(input, "to_string"):
            prompt = input.to_string()

        elif isinstance(input, (list, tuple)):
            try:
                prompt = "\n".join(
                    f"{m.role.upper()}: {m.content}" if hasattr(m, "role") else f"{m[0].upper()}: {m[1]}"
                    for m in input
                )
            except Exception:
                raise ValueError("Invalid message format. Expected (role, content) tuples or BaseMessage objects.")

        elif isinstance(input, str):
            prompt = input

        else:
            raise ValueError(f"Unsupported LanguageModelInput type: {type(input)}")
        if self.provider == 'openai':
            return self._invoke_openai(prompt, **kwargs)
        elif self.provider == 'google':
            return self._invoke_google(prompt, **kwargs)
        elif self.provider == 'stability':
            return self._invoke_stability(prompt, **kwargs)
        elif self.provider == 'huggingface':
            return self._invoke_huggingface(prompt, **kwargs)
        elif self.provider == 'replicate':
            return self._invoke_replicate(prompt, **kwargs)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
    
    def _invoke_openai(self, prompt: str, **kwargs) -> ImageResponse:
        """OpenAI DALL-E implementation"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model,
            "prompt": prompt,
            "n": kwargs.get('n', 1),
            "response_format": kwargs.get('response_format', 'b64_json')
        }
        
        # Add optional parameters
        if kwargs.get('size'):
            data['size'] = kwargs['size']
        if kwargs.get('quality') and 'dall-e-3' in self.model:
            data['quality'] = kwargs['quality']
        if kwargs.get('style') and 'dall-e-3' in self.model:
            data['style'] = kwargs['style']
        
        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers=headers,
            json=data
        )
        response.raise_for_status()
        
        result = response.json()
        images = []
        for item in result["data"]:
            if data["response_format"] == "url":
                images.append(item['url'])
            else:
                images.append(item["b64_json"])
        
        return ImageResponse(
            images=images,
            model=self.full_model,
            provider='openai',
            prompt=prompt,
            metadata={'created': result.get('created')}
        )
    
    def _invoke_google(self, prompt: str, **kwargs) -> ImageResponse:
        """Google Imagen implementation"""
        client = genai.Client(api_key=self.api_key,**kwargs)
        images = []
        if "imagen" in self.model:
            response = client.models.generate_images(
                model=self.model,
                prompt=prompt
            )
            for image in response.generated_images:
                images.append(base64.b64encode(image.image.image_bytes).decode("utf-8"))
        else:
            response = client.models.generate_content(
                model=self.model,
                contents=[prompt],
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    images.append(base64.b64encode(part.inline_data.data).decode("utf-8"))
        
        
        return ImageResponse(
            images=images,
            model=self.full_model,
            provider='google',
            prompt=prompt,
            metadata=None
        )
    
    def _invoke_stability(self, prompt: str, **kwargs) -> ImageResponse:
        """Stability AI implementation"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "text_prompts": [{"text": prompt}],
            "samples": kwargs.get('n', 1),
            "steps": kwargs.get('steps', 50),
            "cfg_scale": kwargs.get('cfg_scale', 7)
        }
        
        if kwargs.get('width') and kwargs.get('height'):
            data['width'] = kwargs['width']
            data['height'] = kwargs['height']
        
        response = requests.post(
            f"https://api.stability.ai/v1/generation/{self.model}/text-to-image",
            headers=headers,
            json=data
        )
        response.raise_for_status()
        
        result = response.json()
        images = [artifact["base64"] for artifact in result.get("artifacts", [])]
        
        return ImageResponse(
            images=images,
            model=self.full_model,
            provider='stability',
            prompt=prompt,
            metadata={'seed': result.get('seed')}
        )
    
    def _invoke_huggingface(self, prompt: str, **kwargs) -> ImageResponse:
        """Hugging Face implementation"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {"inputs": prompt}
        if kwargs.get('negative_prompt'):
            data['negative_prompt'] = kwargs['negative_prompt']
        if kwargs.get('num_inference_steps'):
            data['num_inference_steps'] = kwargs['num_inference_steps']
        
        response = requests.post(
            f"https://api-inference.huggingface.co/models/{self.model}",
            headers=headers,
            json=data
        )
        response.raise_for_status()
        
        # Convert binary response to base64
        image_b64 = base64.b64encode(response.content).decode()
        
        return ImageResponse(
            images=[image_b64],
            model=self.full_model,
            provider='huggingface',
            prompt=prompt
        )
    
    def _invoke_replicate(self, prompt: str, **kwargs) -> ImageResponse:
        """Replicate implementation"""
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "version": self.model if ':' in self.model else None,
            "input": {"prompt": prompt}
        }
        
        # Add optional parameters
        for key in ['width', 'height', 'num_outputs', 'scheduler', 'guidance_scale']:
            if kwargs.get(key):
                data['input'][key] = kwargs[key]
        
        # Create prediction
        response = requests.post(
            "https://api.replicate.com/v1/predictions",
            headers=headers,
            json=data
        )
        response.raise_for_status()
        
        result = response.json()
        prediction_id = result["id"]
        
        # Poll for completion
        while True:
            response = requests.get(
                f"https://api.replicate.com/v1/predictions/{prediction_id}",
                headers=headers
            )
            response.raise_for_status()
            result = response.json()
            
            if result["status"] == "succeeded":
                break
            elif result["status"] == "failed":
                raise Exception(f"Prediction failed: {result.get('error')}")
            
            time.sleep(1)
        
        images = result.get("output", [])
        if isinstance(images, str):
            images = [images]
        
        return ImageResponse(
            images=images,
            model=self.full_model,
            provider='replicate',
            prompt=prompt,
            metadata={'prediction_id': prediction_id}
        )

