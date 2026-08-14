from __future__ import annotations

import sys
import wave
from pathlib import Path
from typing import Any


_ORIGINAL_SAVE_AUDIO: Any = None


def _save_audio_without_torchcodec(
    wav: Any,
    path: str | Path,
    samplerate: int,
    bitrate: int = 320,
    clip: str = "rescale",
    bits_per_sample: int = 16,
    as_float: bool = False,
    preset: int = 2,
) -> None:
    """Write Demucs WAV output without torchaudio's torchcodec backend."""

    target = Path(path)
    if target.suffix.lower() != ".wav":
        if _ORIGINAL_SAVE_AUDIO is None:
            raise RuntimeError(f"Unsupported Demucs output format: {target.suffix}")
        _ORIGINAL_SAVE_AUDIO(
            wav,
            target,
            samplerate=samplerate,
            bitrate=bitrate,
            clip=clip,
            bits_per_sample=bits_per_sample,
            as_float=as_float,
            preset=preset,
        )
        return

    if as_float or bits_per_sample != 16:
        print("[UtaSyncNext Demucs] WAV fallback writes 16-bit PCM output.")

    audio = __import__("demucs.audio", fromlist=["prevent_clip"])
    np = __import__("numpy")

    wav = audio.prevent_clip(wav, mode=clip)
    wav = wav.detach().cpu()
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)

    # Demucs tensors are channel-first. The wave module expects interleaved
    # frames, so convert to [samples, channels] and then to PCM16.
    data = np.asarray(wav.clamp(-1.0, 1.0).transpose(0, 1).numpy(), dtype=np.float32)
    pcm = np.rint(data * 32767.0).clip(-32768, 32767).astype(np.int16)

    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(int(pcm.shape[1]))
        handle.setsampwidth(2)
        handle.setframerate(int(samplerate))
        handle.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    audio = __import__("demucs.audio", fromlist=["save_audio"])
    separate = __import__("demucs.separate", fromlist=["main", "save_audio"])

    global _ORIGINAL_SAVE_AUDIO
    _ORIGINAL_SAVE_AUDIO = audio.save_audio
    audio.save_audio = _save_audio_without_torchcodec
    separate.save_audio = _save_audio_without_torchcodec

    separate.main(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
