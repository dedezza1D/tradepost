import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from tradepost.security.password_policy import MAX_LENGTH, MIN_LENGTH

BR_STATES = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)

Locale = Literal["pt-BR", "en"]

Password = Annotated[str, StringConstraints(min_length=MIN_LENGTH, max_length=MAX_LENGTH)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
Bio = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


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
    locale: Locale = "pt-BR"

    _state = field_validator("state")(_validate_state)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: Password


class ProfileUpdateIn(BaseModel):
    display_name: DisplayName | None = None
    # An empty string clears the bio.
    bio: Bio | None = None
    city: City | None = None
    state: str | None = None
    locale: Locale | None = None

    _state = field_validator("state")(_validate_state)


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    city: str | None
    state: str | None
    municipality_id: int | None
    locale: str
    is_admin: bool
    created_at: datetime
    bio: str | None = None
    avatar_url: str | None = None


class PublicUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    city: str | None
    state: str | None
    created_at: datetime
