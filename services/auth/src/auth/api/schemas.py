from pydantic import BaseModel, EmailStr, Field, field_validator

# RFC 5321 maximum, matching users.email's VARCHAR(320) -- rejected here rather than at the DB
# so an oversized email never reaches argon2 or the database.
_MAX_EMAIL_LENGTH = 320


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def _check_email_length(cls, value: str) -> str:
        if len(value) > _MAX_EMAIL_LENGTH:
            raise ValueError("email exceeds maximum length")
        return value

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must not be blank")
        return stripped


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    is_admin: bool
