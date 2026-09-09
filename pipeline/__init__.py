"""Stage 1: institutional documents -> markdown, plus a text-layer coverage report.

Model-free for text by design: no OCR, no VLM, no LLM anywhere in this package.
"""

__all__ = ["StopAndAsk"]


class StopAndAsk(RuntimeError):
    """A decision belongs to a human, so the pipeline stops instead of guessing.

    Raised where a plausible-looking automatic choice would quietly foreclose a design
    decision — an oversized CSV that would need a chunking scheme, an unimplemented
    `csv_mode`, an encrypted source. The message must say what was found and what decision
    is needed.
    """
