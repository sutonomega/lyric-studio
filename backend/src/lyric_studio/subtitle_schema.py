from pydantic import BaseModel, Field


COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class SubtitleStyle(BaseModel):
    font: str = Field(min_length=1, max_length=64)
    size: int = Field(ge=16, le=144)
    color: str = Field(pattern=COLOR_PATTERN)
    opacity: int = Field(ge=0, le=100)
    outline: int = Field(ge=0, le=12)
    outline_color: str = Field(pattern=COLOR_PATTERN)
    shadow: int = Field(ge=0, le=18)
    letter_spacing: int = Field(ge=0, le=20)
    line_spacing: int = Field(ge=0, le=64)
    anchor: int = Field(ge=1, le=9)
    fade_in: int = Field(ge=0, le=2000)
    fade_out: int = Field(ge=0, le=2000)


class SubtitleCue(BaseModel):
    text: str
    start_ms: int | None
    end_ms: int | None


class SubtitleRequest(BaseModel):
    duration_ms: int = Field(gt=0)
    cues: list[SubtitleCue]
    skip_excluded: bool = False
    style: SubtitleStyle | None = None
