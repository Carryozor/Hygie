from enum import Enum
from typing import Union
from pydantic import BaseModel, field_validator

class ConditionField(str, Enum):
    DAYS_NOT_WATCHED = "days_not_watched"
    PLAY_COUNT       = "play_count"
    RATING           = "rating"
    FILE_SIZE_GB     = "file_size_gb"
    ADDED_DAYS_AGO   = "added_days_ago"
    MEDIA_TYPE       = "media_type"
    SEERR_USER_ID    = "seerr_user_id"

class ConditionOp(str, Enum):
    GT     = "gt"
    LT     = "lt"
    GTE    = "gte"
    LTE    = "lte"
    EQ     = "eq"
    IN     = "in"
    NOT_IN = "not_in"

class RuleOperator(str, Enum):
    AND = "AND"
    OR  = "OR"

class RuleAction(str, Enum):
    QUEUE       = "queue"
    NOTIFY_ONLY = "notify_only"

class Condition(BaseModel):
    field: ConditionField
    op:    ConditionOp
    value: Union[int, float, str, list]

    @field_validator("value")
    @classmethod
    def _validate_list_ops(cls, v, info):
        op = info.data.get("op")
        if op in (ConditionOp.IN, ConditionOp.NOT_IN) and not isinstance(v, list):
            raise ValueError(f"op={op} requires a list value, got {type(v).__name__}")
        return v

class ExpertRule(BaseModel):
    id:          int | None     = None
    name:        str
    library_id:  int | None     = None
    conditions:  list[Condition]
    operator:    RuleOperator   = RuleOperator.AND
    action:      RuleAction     = RuleAction.QUEUE
    enabled:     bool           = True
    priority:    int            = 0
    created_at:  str | None     = None
