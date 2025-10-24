from google import genai
from google.genai import types
import time
from typing import List, Union, Optional

MIME_TYPES = {
    "txt": "text/plain",
    "md": "text/markdown",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    # Videos
    "flv": "video/x-flv",
    "mov": "video/quicktime",
    "mpeg": "video/mpeg",
    "mpegps": "video/mpegps",
    "mpg": "video/mpg",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "wmv": "video/wmv",
    "3gp": "video/3gpp",
    # Audio
    "aac": "audio/aac",
    "flac": "audio/flac",
    "mp3": "audio/mp3",
    "m4a": "audio/m4a",
    "mpga": "audio/mpga",
    "opus": "audio/opus",
    "pcm": "audio/pcm",
    "wav": "audio/wav",
    # Documents
    "pdf": "application/pdf",
    # Note: txt already present above (text/plain)
}


def create_gemini_cache(
    files: Union[str, List[str]],
    cache_display_name: str,
    system_instruction: str = "You are a helpful assistant that answers questions based on the provided content.",
    model: str = "models/gemini-1.5-flash-latest",
    ttl: str = "3600s",
    information:str = "",
    api_key: Optional[str] = None
) -> types.CachedContent:
    client = genai.Client(api_key=api_key)

    if isinstance(files, str):
        files = [files]
    uploaded_files = []
    for file_path in files:
        ext = file_path.split(".")[-1].lower()
        mime_type = MIME_TYPES.get(ext,"text/plain")
        config = {}
        if mime_type:
            config['mime_type'] = mime_type
        file = client.files.upload(
            file=file_path,
            config=config
        )
        
        while file.state.name == 'PROCESSING':
            time.sleep(2)
            file = client.files.get(name=file.name)
        
        if file.state.name == 'ACTIVE':
            uploaded_files.append(file)
        else:
            raise RuntimeError(f"Failed to process file {file_path}: {file.state.name}")
    if information:
        uploaded_files.append({
            "role": "user",
            "parts": [
                {
                    "text": information
                }
            ]
        })
        
    if not len(uploaded_files) : return
    cache = client.caches.create(
        model=model,
        config=types.CreateCachedContentConfig(
            display_name=cache_display_name,
            system_instruction=system_instruction,
            contents=uploaded_files,
            ttl=ttl,
        )
    )

    return cache


def delete_cache(cache,api_key: Optional[str] = None):
    client = genai.Client(api_key=api_key)
    return client.caches.delete(name=cache)