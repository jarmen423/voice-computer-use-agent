"""Unit tests for shared AudioDevice ownership behavior."""

from voiceuse.audio_device import AudioDevice


class FakePyAudioModule:
    """Minimal PyAudio module stand-in for lease bookkeeping tests."""

    paInt16 = 8

    def __init__(self) -> None:
        self.instances: list[FakePyAudio] = []

    def PyAudio(self):
        """Return one fake PyAudio process owner."""
        instance = FakePyAudio()
        self.instances.append(instance)
        return instance


class FakePyAudio:
    """Minimal PyAudio instance that opens fake streams."""

    def __init__(self) -> None:
        self.terminated = False

    def open(self, **kwargs):
        """Create a fake stream with the requested PyAudio kwargs."""
        return FakeStream(kwargs)

    def terminate(self) -> None:
        """Record process-level audio shutdown."""
        self.terminated = True


class FakeStream:
    """Fake PyAudio stream with close/stop bookkeeping."""

    def __init__(self, kwargs) -> None:
        self.kwargs = kwargs
        self.stopped = False
        self.closed = False

    def stop_stream(self) -> None:
        """Record stream stop."""
        self.stopped = True

    def close(self) -> None:
        """Record stream close."""
        self.closed = True


def test_audio_device_reuses_single_pyaudio_instance() -> None:
    """Multiple stream leases should share one process-level PyAudio owner."""
    module = FakePyAudioModule()
    device = AudioDevice(pyaudio_module=module)

    first = device.open_input_stream(
        owner="input",
        rate=16000,
        channels=1,
        format=device.pa_int16,
        frames_per_buffer=480,
    )
    second = device.open_output_stream(
        owner="output",
        rate=24000,
        channels=1,
        format=device.pa_int16,
        frames_per_buffer=960,
    )

    assert len(module.instances) == 1
    assert len(device.active_leases()) == 2

    device.close_stream(first)
    assert first.stopped is True
    assert first.closed is True
    assert len(device.active_leases()) == 1

    device.close_stream(second)
    device.stop()
    assert module.instances[0].terminated is True
