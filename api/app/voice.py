import asyncio
import io
import subprocess
import time

from fastapi import HTTPException, UploadFile
from openai import AsyncOpenAI

from app.config import settings

MAX_AUDIO_BYTES = 12 * 1024 * 1024

class SpeechProvider:
    """Provider boundary; configured implementation uses an OpenAI-compatible audio API."""
    async def transcribe(self, audio: bytes, filename: str) -> str:
        if settings.stt_provider != "openai_compatible":
            raise HTTPException(503, "当前语音识别服务尚未配置")
        if not settings.openai_api_key:
            raise HTTPException(503, "语音服务尚未配置，请家人协助设置")
        client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=15)
        try:
            result = await client.audio.transcriptions.create(model=settings.stt_model, file=(filename, audio, "audio/wav"), language="zh")
            return result.text.strip()
        finally:
            await client.close()

    async def synthesize(self, text: str) -> bytes:
        if settings.tts_provider != "openai_compatible":
            raise HTTPException(503, "当前语音播报服务尚未配置")
        if not settings.openai_api_key:
            raise HTTPException(503, "语音服务尚未配置，请家人协助设置")
        client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=15)
        try:
            response = await client.audio.speech.create(model=settings.tts_model, voice=settings.tts_voice, input=text, response_format="mp3")
            return response.content
        finally:
            await client.close()

provider = SpeechProvider()

async def to_16khz_mono_wav(data: bytes) -> bytes:
    """Transcode in memory; no source or converted audio is retained on disk."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, error = await proc.communicate(data)
    if proc.returncode != 0:
        raise HTTPException(400, "录音格式无法识别，请重新录一次")
    return output

async def read_recording(file: UploadFile) -> tuple[bytes, str]:
    source = await file.read(MAX_AUDIO_BYTES + 1)
    if len(source) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "录音超过限制，请分成两段说")
    if not source:
        raise HTTPException(400, "没有收到录音，请再试一次")
    return await to_16khz_mono_wav(source), "recording.wav"
