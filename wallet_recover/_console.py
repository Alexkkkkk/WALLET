"""Safe CLI text output without changing console encodings or binary files."""
import sys


class _DiscardTextStream:
    """Frozen windowed applications may have no stdout or stderr."""
    encoding = "utf-8"
    errors = "backslashreplace"

    def write(self, text):
        return len(text)

    def writelines(self, lines):
        for text in lines:
            self.write(text)

    def flush(self):
        pass

    def isatty(self):
        return False


class _SafeTextStream:
    """Fallback for embedded/captured streams without usable reconfigure()."""
    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        try:
            return self._stream.write(text)
        except UnicodeEncodeError as exc:
            # cp1252/cp866 errors name the generic 'charmap' codec, so prefer
            # the actual stream encoding. Without it, ASCII escaping is safe
            # for an unknown charmap and avoids guessing its byte mapping.
            encoding = getattr(self._stream, "encoding", None) or exc.encoding
            if encoding == "charmap":
                encoding = "ascii"
            safe = text.encode(encoding, "backslashreplace").decode(encoding)
            self._stream.write(safe)
            return len(text)

    def writelines(self, lines):
        for text in lines:
            self.write(text)

    def __getattr__(self, name):
        # Keep flush/isatty/fileno and especially .buffer on the original stream.
        return getattr(self._stream, name)


def configure_console_streams():
    """Make stdout/stderr Unicode-safe at CLI startup, before argparse output.

    Preserve UTF-8 and real Windows legacy console/redirected encodings alike.
    Only the text error policy changes; password/wordlist files and raw stream
    buffers are untouched. Importing CLI helpers from the GUI has no side effects.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _DiscardTextStream())
            continue
        if isinstance(stream, (_SafeTextStream, _DiscardTextStream)):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="backslashreplace")
                continue
            except (AttributeError, OSError, TypeError, ValueError):
                # Third-party wrappers may advertise reconfigure but reject it.
                pass
        setattr(sys, name, _SafeTextStream(stream))
