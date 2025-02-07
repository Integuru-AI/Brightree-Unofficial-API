import re
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator


class BasePatient(BaseModel):
    patient_id: int
    name_first: Optional[str] = ""
    name_last: Optional[str] = ""
    name_middle: Optional[str] = ""
    name_suffix: Optional[str] = ""
    name_preferred: Optional[str] = ""
    email: Optional[str] = None
    dob: Optional[str] = None
    ssn: Optional[str] = None
    phone_home: Optional[str] = None
    phone_mobile: Optional[str] = None
    phone_fax: Optional[str] = None

    @field_validator('phone_home', 'phone_mobile', 'phone_fax')
    def format_phone(self, v: Optional[str]) -> str:
        if not v or v == "(___) ___-____":
            return "(___) ___-____"

        numbers_only = re.sub(r'\D', '', v)

        if len(numbers_only) == 10:
            return f"({numbers_only[:3]}) {numbers_only[3:6]}-{numbers_only[6:]}"
        elif len(numbers_only) == 11 and numbers_only[0] == '1':
            return f"({numbers_only[1:4]}) {numbers_only[4:7]}-{numbers_only[7:]}"
        else:
            raise ValueError("Phone number must be a valid US number (10 digits)")

    @field_validator('ssn')
    def format_ssn(self, v: Optional[str]) -> str:
        if not v or v == "___-__-____":
            return "___-__-____"

        numbers_only = re.sub(r'\D', '', v)

        if len(numbers_only) != 9:
            raise ValueError("SSN must be 9 digits")

        return f"{numbers_only[:3]}-{numbers_only[3:5]}-{numbers_only[5:]}"

    @field_validator('email')
    def validate_email(self, v: Optional[str]) -> str:
        if not v:
            return ""
        # If email is provided, validate it's a proper email format
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator('dob')
    def validate_date_format(self, val: Optional[str]) -> str:
        if not val:
            return ""

        try:
            # Attempt to parse the date string
            datetime.strptime(val, '%Y-%m-%d')
        except ValueError:
            raise ValueError('start_date must be in YYYY-MM-DD format')
        return val


class NewPatient(BasePatient):
    patient_id: int = 0  # Always 0 for new patients


class ExistingPatient(BasePatient):
    patient_id: int  # Required, no default value
