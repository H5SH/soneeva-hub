"""A branch's opening hours."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class TimeRange(BaseModel):
    """Open from `open` to `close`, 24h; a close before the open runs past midnight."""

    model_config = ConfigDict(extra="forbid")

    open: str = Field(pattern=HHMM)
    close: str = Field(pattern=HHMM)

    @model_validator(mode="after")
    def _not_empty(self) -> "TimeRange":
        if self.open == self.close:
            raise ValueError("A range opens and closes at different times")
        return self


Day = list[TimeRange]


DayField = Field(default_factory=list, max_length=2)


class WeekHours(BaseModel):
    """A branch's opening hours (`branches/{id}.hours`): per day up to two ranges; empty is closed."""

    model_config = ConfigDict(extra="forbid")

    mon: Day = DayField
    tue: Day = DayField
    wed: Day = DayField
    thu: Day = DayField
    fri: Day = DayField
    sat: Day = DayField
    sun: Day = DayField
