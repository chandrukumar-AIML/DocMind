
from __future__ import annotations

import asyncio
import functools
import io
import logging
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional, Any

from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from pydub import AudioSegment

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.ingest_utils import generate_ingest_correlation_id
from app.core.retry import retry_async, RetryConfig
from app.core.openai_errors import classify_openai_error
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-M) -------------------------
# ========================================================================
_WHISPER_MAX_FILE_MB: Final = 24
_WHISPER_MAX_DURATION_MIN: Final = 60
_CHUNK_DURATION_SEC: Final = 600
_CHUNK_OVERLAP_SEC: Final = 30
_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 30.0
_MIN_AUDIO_DURATION: Final = 0.1
_MAX_AUDIO_DURATION: Final = 3600

_WHISPER_TIMEOUT: Final = 300


@dataclass  # FIXED: Removed frozen=True for safe __post_init__ mutation
class TranscriptSegment:
    """Transcription segment with timing and speaker."""

    start: float
    end: float
    text: str
    speaker: str = ""
    correlation_id: Optional[str] = None

    def __post_init__(self):
        # DVMELTSS-V: Validate timing
        if self.start < 0 or self.end < self.start:
            self.start = 0.0
            self.end = max(0.0, self.end)


@dataclass
class TranscriptionResult:
    """Complete transcription of an audio/video file."""

    source_file: str
    full_text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = "en"
    duration_sec: float = 0.0
    model_used: str = "whisper-1"
    has_speakers: bool = False
    error: Optional[str] = None
    correlation_id: Optional[str] = None

    @property
    def speaker_count(self) -> int:
        speakers = {s.speaker for s in self.segments if s.speaker}
        return len(speakers)

    def to_chunks(self, max_words: int = 200) -> list[str]:
        """Chunk transcript by word count, respecting segment boundaries."""
        if not self.segments:
            return _chunk_text_by_words(self.full_text, max_words)
        chunks = []
        current = []
        word_count = 0
        for seg in self.segments:
            speaker_prefix = f"{seg.speaker}: " if seg.speaker else ""
            seg_text = f"{speaker_prefix}{seg.text.strip()}"
            seg_words = len(seg_text.split())
            if word_count + seg_words > max_words and current:
                chunks.append("\n".join(current))
                current = []
                word_count = 0
            current.append(seg_text)
            word_count += seg_words
        if current:
            chunks.append("\n".join(current))
        return chunks if chunks else [self.full_text]

    def to_dict(self) -> dict:
        """Serialize for API responses / logging."""
        return {
            "source_file": self.source_file,
            "full_text": self.full_text[:500] + ("..." if len(self.full_text) > 500 else ""),
            "segment_count": len(self.segments or []),
            "language": self.language,
            "duration_sec": round(self.duration_sec, 2),
            "model_used": self.model_used,
            "has_speakers": self.has_speakers,
            "error": self.error,
            "correlation_id": self.correlation_id,
        }


def _chunk_text_by_words(text: str, max_words: int) -> list[str]:
    """Helper: chunk text by word count."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i : i + max_words]))
    return chunks or [text]


@asynccontextmanager
async def _temp_audio_file(audio_bytes: bytes, suffix: str = ".mp3"):
    """DVMELTSS-E: Async-safe temp file context manager."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(audio_bytes)
            temp_path = Path(tmp.name)
        yield temp_path
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError as e:
                logger.warning(f"Temp file cleanup failed: {e}")


def _validate_transcribe_inputs(
    file_path: Optional[str | Path],
    file_bytes: Optional[bytes],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate transcription inputs before processing."""
    if file_path is None and file_bytes is None:
        return False, "Either file_path or file_bytes must be provided"
    if file_path is not None and not isinstance(file_path, (str, Path)):
        return False, "file_path must be a string, Path, or None"
    if file_bytes is not None and not isinstance(file_bytes, bytes):
        return False, "file_bytes must be bytes or None"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


class AudioTranscriber:
    """
    Audio/video transcription using OpenAI Whisper API.
    Optionally adds speaker diarization via pyannote.audio.
    """

    def __init__(self, model: str = "whisper-1", max_retries: int = _MAX_RETRIES):
        settings = get_settings()
        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError("OpenAI API key required for transcription")
        self.client = AsyncOpenAI(api_key=api_key, timeout=30.0)
        self.whisper_model = model
        self.language = getattr(settings, "whisper_language", "") or None
        self.enable_diarization = getattr(settings, "enable_speaker_diarization", False)
        self.hf_token = getattr(settings, "huggingface_token", "")
        self.max_retries = max_retries
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=max_retries,
                backoff_base=_RETRY_BASE_DELAY,
                backoff_max=_RETRY_MAX_DELAY,
                exceptions=(Exception,),
            )
        )
        logger.info(f"AudioTranscriber initialized: model={model}, async=True")

    def _validate_audio_file(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """DVMELTSS-V: Validate audio file before processing."""
        if not file_path.exists():
            return False, f"File not found: {file_path}"
        size_mb = file_path.stat().st_size / 1024 / 1024
        if size_mb > _WHISPER_MAX_FILE_MB:
            return True, None  # Will be chunked
        try:
            audio = AudioSegment.from_file(str(file_path))
            duration_min = len(audio) / 1000 / 60
            if duration_min > _WHISPER_MAX_DURATION_MIN:
                return True, None
            if duration_min < _MIN_AUDIO_DURATION / 60:
                return False, f"Audio too short: {duration_min:.2f}min"
        except Exception:
            pass
        return True, None

    async def _whisper_transcribe_async(
        self,
        audio_path: Path,
        source_file: str,
        correlation_id: str,
    ) -> TranscriptionResult:
        """Async: Call OpenAI Whisper API and parse verbose JSON response."""
        corr_id = correlation_id

        for attempt in range(self.max_retries + 1):
            try:
                loop = asyncio.get_running_loop()

                audio_bytes = await loop.run_in_executor(None, lambda: audio_path.read_bytes())

                # OpenAI API expects file-like object, not raw bytes
                audio_file = io.BytesIO(audio_bytes)
                audio_file.name = audio_path.name  # Set name for API

                kwargs = dict(
                    model=self.whisper_model,
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
                if self.language:
                    kwargs["language"] = self.language

                response = await asyncio.wait_for(
                    self.client.audio.transcriptions.create(**kwargs),
                    timeout=_WHISPER_TIMEOUT,
                )

                full_text = response.text or ""
                segments = []
                duration = 0.0
                raw_segments = getattr(response, "segments", []) or []

                for seg in raw_segments:
                    start = float(getattr(seg, "start", 0))
                    end = float(getattr(seg, "end", 0))
                    text = str(getattr(seg, "text", "")).strip()
                    if text:
                        segments.append(TranscriptSegment(start=start, end=end, text=text, correlation_id=corr_id))
                    duration = max(duration, end)

                language = getattr(response, "language", "en") or "en"

                logger.info(
                    f"[{corr_id}] Whisper: {source_file} | "
                    f"{len(segments)} segments | {duration:.1f}s | lang={language}"
                )

                return TranscriptionResult(
                    source_file=source_file,
                    full_text=full_text,
                    segments=segments,
                    language=language,
                    duration_sec=duration,
                    model_used=self.whisper_model,
                    correlation_id=corr_id,
                )

            except asyncio.TimeoutError:
                logger.error(f"[{corr_id}] Whisper API call timed out after {_WHISPER_TIMEOUT}s")
                return TranscriptionResult(
                    source_file=source_file,
                    full_text="",
                    error=f"Whisper API timeout after {_WHISPER_TIMEOUT}s",
                    model_used=self.whisper_model,
                    correlation_id=corr_id,
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                err = classify_openai_error(e)
                if err.error_type == "quota":
                    logger.warning(f"[{corr_id}] Whisper: quota exceeded")
                    return TranscriptionResult(
                        source_file=source_file,
                        full_text="",
                        error="OpenAI quota exceeded",
                        model_used=self.whisper_model,
                        correlation_id=corr_id,
                    )
                if attempt < self.max_retries:
                    wait = min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY)
                    logger.warning(f"[{corr_id}] Whisper retry {attempt+1} in {wait}s: {err.error_type}")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[{corr_id}] Whisper failed after retries: {err.error_type}")
                    return TranscriptionResult(
                        source_file=source_file,
                        full_text="",
                        error=f"Whisper API error: {err.error_type}",
                        model_used=self.whisper_model,
                        correlation_id=corr_id,
                    )
            except Exception as e:
                logger.error(f"[{corr_id}] Whisper unexpected error: {type(e).__name__}: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(_RETRY_BASE_DELAY)
                else:
                    return TranscriptionResult(
                        source_file=source_file,
                        full_text="",
                        error=f"Whisper error: {type(e).__name__}",
                        model_used=self.whisper_model,
                        correlation_id=corr_id,
                    )

        return TranscriptionResult(
            source_file=source_file,
            full_text="",
            error="Max retries exceeded",
            model_used=self.whisper_model,
            correlation_id=corr_id,
        )

    async def _transcribe_large_file_async(
        self,
        audio_path: Path,
        source_file: str,
        correlation_id: str,
    ) -> TranscriptionResult:
        """Async: Split audio into chunks and transcribe each."""
        corr_id = correlation_id

        try:
            loop = asyncio.get_running_loop()

            audio = await loop.run_in_executor(None, functools.partial(AudioSegment.from_file, str(audio_path)))

            duration_s = len(audio) / 1000
            all_segs: list[TranscriptSegment] = []
            all_text = []
            time_offset = 0.0

            with tempfile.TemporaryDirectory() as tmp_dir:
                chunk_paths = []
                try:
                    for i, start_ms in enumerate(range(0, len(audio), _CHUNK_DURATION_SEC * 1000)):
                        chunk = audio[start_ms : start_ms + _CHUNK_DURATION_SEC * 1000]
                        chunk_path = Path(tmp_dir) / f"chunk_{i:03d}.mp3"

                        await loop.run_in_executor(
                            None,
                            functools.partial(chunk.export, str(chunk_path), format="mp3"),
                        )
                        chunk_paths.append(chunk_path)

                    semaphore = asyncio.Semaphore(3)

                    async def transcribe_chunk(path: Path, offset: float) -> TranscriptionResult:
                        async with semaphore:
                            result = await self._whisper_transcribe_async(path, f"{source_file}[chunk]", corr_id)
                            adjusted_segs = [
                                TranscriptSegment(
                                    start=seg.start + offset,
                                    end=seg.end + offset,
                                    text=seg.text,
                                    correlation_id=corr_id,
                                )
                                for seg in result.segments
                            ]
                            result.segments = adjusted_segs
                            return result

                    tasks = [
                        transcribe_chunk(path, time_offset + i * _CHUNK_DURATION_SEC)
                        for i, path in enumerate(chunk_paths)
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for i, res in enumerate(results):
                        if isinstance(res, TranscriptionResult) and res.full_text:
                            all_text.append(res.full_text)
                            all_segs.extend(res.segments)
                        time_offset += _CHUNK_DURATION_SEC - _CHUNK_OVERLAP_SEC

                finally:
                    for path in chunk_paths:
                        if path.exists():
                            try:
                                path.unlink()
                            except OSError:
                                pass

            return TranscriptionResult(
                source_file=source_file,
                full_text=" ".join(all_text),
                segments=all_segs,
                duration_sec=duration_s,
                model_used=self.whisper_model,
                correlation_id=corr_id,
            )

        except Exception as e:
            logger.error(f"[{corr_id}] Large file transcription failed: {e}")
            return TranscriptionResult(
                source_file=source_file,
                full_text="",
                error=f"Chunking error: {type(e).__name__}",
                model_used=self.whisper_model,
                correlation_id=corr_id,
            )

    async def _add_speaker_labels_async(
        self,
        result: TranscriptionResult,
        audio_path: Path,
        correlation_id: str,
    ) -> TranscriptionResult:
        """Async: Add speaker labels using pyannote.audio."""
        corr_id = correlation_id

        if not self.hf_token:
            logger.warning(f"[{corr_id}] HF token not set — skipping diarization")
            return result

        try:
            from pyannote.audio import Pipeline as PyannotePipeline

            logger.info(f"[{corr_id}] Running speaker diarization...")

            diarization_pipeline = PyannotePipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.hf_token,
            )

            loop = asyncio.get_running_loop()

            diarization = await loop.run_in_executor(None, functools.partial(diarization_pipeline, str(audio_path)))

            speaker_turns: list[tuple[float, float, str]] = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                speaker_turns.append((turn.start, turn.end, speaker))

            labelled_segments = []
            for seg in result.segments:
                seg_mid = (seg.start + seg.end) / 2
                speaker = self._find_speaker(seg_mid, speaker_turns)
                labelled_segments.append(
                    TranscriptSegment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text,
                        speaker=speaker,
                        correlation_id=corr_id,
                    )
                )

            full_text = "\n".join(f"{s.speaker}: {s.text}" if s.speaker else s.text for s in labelled_segments)

            logger.info(
                f"[{corr_id}] Diarization complete: {len(set(s for s in labelled_segments if s.speaker))} speakers"
            )

            return TranscriptionResult(
                source_file=result.source_file,
                full_text=full_text,
                segments=labelled_segments,
                language=result.language,
                duration_sec=result.duration_sec,
                model_used=result.model_used,
                has_speakers=True,
                correlation_id=corr_id,
            )

        except ImportError:
            logger.warning(f"[{corr_id}] pyannote.audio not installed — skipping diarization")
            return result
        except Exception as e:
            logger.warning(f"[{corr_id}] Speaker diarization failed: {type(e).__name__}")
            return result

    @staticmethod
    def _find_speaker(time_point: float, speaker_turns: list[tuple[float, float, str]]) -> str:
        """Find which speaker was talking at a given time point."""
        for start, end, speaker in speaker_turns:
            if start <= time_point <= end:
                num = speaker.replace("SPEAKER_", "")
                try:
                    return f"Speaker {chr(65 + int(num))}"
                except (ValueError, IndexError):
                    return speaker
        return ""

    async def transcribe_async(
        self,
        file_path: str | Path,
        file_bytes: Optional[bytes] = None,
        enable_diarization: Optional[bool] = None,
        correlation_id: Optional[str] = None,
    ) -> TranscriptionResult:
        """Async version: Transcribe an audio or video file."""
        corr_id = correlation_id or generate_ingest_correlation_id("audio")

        # ✅ Validate inputs
        is_valid, error = _validate_transcribe_inputs(file_path, file_bytes, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid transcribe inputs: {error}")
            return TranscriptionResult(
                source_file=str(file_path) if file_path else "unknown",
                full_text="",
                error=error,
                correlation_id=corr_id,
            )

        file_path_obj = Path(file_path) if isinstance(file_path, str) else file_path

        if not file_path_obj.exists():
            return TranscriptionResult(
                source_file=str(file_path_obj),
                full_text="",
                error=f"File not found: {file_path_obj}",
                correlation_id=corr_id,
            )

        if file_bytes is None:
            loop = asyncio.get_running_loop()
            file_bytes = await loop.run_in_executor(None, lambda: file_path_obj.read_bytes())

        is_valid, error = self._validate_audio_file(file_path_obj)
        if not is_valid:
            return TranscriptionResult(
                source_file=str(file_path_obj),
                full_text="",
                error=error,
                correlation_id=corr_id,
            )

        size_mb = len(file_bytes) / 1024 / 1024
        logger.info(f"[{corr_id}] Transcribing: {file_path_obj.name} | {size_mb:.1f}MB")

        async with _temp_audio_file(file_bytes) as audio_path:
            if size_mb > _WHISPER_MAX_FILE_MB:
                result = await self._transcribe_large_file_async(audio_path, file_path_obj.name, corr_id)
            else:
                result = await self._whisper_transcribe_async(audio_path, file_path_obj.name, corr_id)

            use_diarization = enable_diarization if enable_diarization is not None else self.enable_diarization
            if use_diarization and result.segments and not result.error:
                result = await self._add_speaker_labels_async(result, audio_path, corr_id)

        return result

    def transcribe(
        self,
        file_path: str | Path,
        file_bytes: Optional[bytes] = None,
        enable_diarization: Optional[bool] = None,
        correlation_id: Optional[str] = None,
    ) -> TranscriptionResult:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_transcribe():
            return await self.transcribe_async(file_path, file_bytes, enable_diarization, correlation_id)

        return run_async_in_task(_do_transcribe)


def get_audio_metadata() -> dict[str, Any]:
    """✅ NEW: Return audio transcriber metadata for debugging."""
    return {
        "limits": {
            "max_file_mb": _WHISPER_MAX_FILE_MB,
            "max_duration_min": _WHISPER_MAX_DURATION_MIN,
            "chunk_duration_sec": _CHUNK_DURATION_SEC,
            "chunk_overlap_sec": _CHUNK_OVERLAP_SEC,
        },
        "retry_config": {
            "max_attempts": _MAX_RETRIES,
            "backoff_base": _RETRY_BASE_DELAY,
            "backoff_max": _RETRY_MAX_DELAY,
        },
        "api_timeout_seconds": _WHISPER_TIMEOUT,
        "supported_formats": ["mp3", "wav", "m4a", "mp4", "mov", "avi"],
        "diarization_enabled": False,  # Requires pyannote.audio + HF token
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "AudioTranscriber",
    "TranscriptionResult",
    "TranscriptSegment",
    "get_audio_metadata",
]
# Local smoke test entry point. Run: python -m

