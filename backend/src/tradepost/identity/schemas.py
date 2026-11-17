import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

BR_STATES = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)

Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]


def _validate_state(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.upper()
    if value not in BR_STATES:
        raise ValueError("unknown Brazilian state code")
    return value


class RegisterIn(BaseModel):
    email: EmailStr
    password: Password
    display_name: DisplayName
    city: City | None = None
    state: str | None = None

    _state = field_validator("state")(_validate_state)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: Password


class ProfileUpdateIn(BaseModel):
    display_name: DisplayName | None = None
    city: City | None = None
    state: str | None = None

    _state = field_validator("state")(_validate_state)


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    city: str | None
    state: str | None
    is_admin: bool
    created_at: datetime


class PublicUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    city: str | None
    state: str | None
    created_at: datetime
