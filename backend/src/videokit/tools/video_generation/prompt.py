"""Provider prompt for Video Generation (research V7).

Pure and deterministic: the same prompt and picture count always give the same text. The declaration maps the
user's 1-based "Picture N" wording to the provider's zero-indexed `<IMAGE_REF_N>` tags.
"""


def build_provider_prompt(prompt: str, picture_count: int) -> str:
    """The user's prompt unchanged, preceded by a reference declaration when there are pictures."""
    if picture_count < 0:
        raise ValueError("picture_count cannot be negative")
    if picture_count == 0:
        return prompt
    references = " ".join(f"<IMAGE_REF_{index}>@Picture{index + 1}" for index in range(picture_count))
    return f"[# References {references}]\n{prompt}"
