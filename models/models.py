import re
from typing import Optional
from datetime import date, datetime
from pydantic import BaseModel, field_validator, ConfigDict


phone_number_format = "(___) ___-____"


class BasePatient(BaseModel):
    model_config = ConfigDict(
        json_encoders={date: lambda v: v.strftime('%Y-%m-%d')},
        json_schema_extra={
            "example": {
                "patient_id": 12345,
                "name_first": "Bruce",
                "name_last": "Doe",
                "name_middle": "",
                "name_suffix": "",
                "name_preferred": "",
                "email": "bruce.doe@example.com",
                "dob": "1990-01-01",
                "ssn": "123-45-6789",
                "phone_home": "(123) 456-7890",
                "phone_mobile": "(123) 456-7890",
                "phone_fax": "(123) 456-7890"
            }
        }
    )

    patient_id: int
    name_first: Optional[str] = ""
    name_last: Optional[str] = ""
    name_middle: Optional[str] = ""
    name_suffix: Optional[str] = ""
    name_preferred: Optional[str] = ""
    email: Optional[str] = None
    dob: Optional[str] = None
    ssn: Optional[str] = None
    phone_home: Optional[str] = phone_number_format
    phone_mobile: Optional[str] = phone_number_format
    phone_fax: Optional[str] = phone_number_format

    @field_validator('phone_home', 'phone_mobile', 'phone_fax')
    @classmethod
    def format_phone(cls, v: Optional[str]) -> str:
        if not v or v == phone_number_format:
            return phone_number_format

        numbers_only = re.sub(r'\D', '', v)

        if len(numbers_only) == 10:
            return f"({numbers_only[:3]}) {numbers_only[3:6]}-{numbers_only[6:]}"
        elif len(numbers_only) == 11 and numbers_only[0] == '1':
            return f"({numbers_only[1:4]}) {numbers_only[4:7]}-{numbers_only[7:]}"
        else:
            raise ValueError("Phone number must be a valid US number (10 digits)")

    @field_validator('ssn')
    @classmethod
    def format_ssn(cls, v: Optional[str]) -> str:
        if not v or v == "___-__-____":
            return "___-__-____"

        numbers_only = re.sub(r'\D', '', v)

        if len(numbers_only) != 9:
            raise ValueError("SSN must be 9 digits")

        return f"{numbers_only[:3]}-{numbers_only[3:5]}-{numbers_only[5:]}"

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: Optional[str]) -> str:
        if not v:
            return ""
        # If email is provided, validate it's a proper email format
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator('dob')
    @classmethod
    def validate_date_format(cls, val: Optional[str]) -> str:
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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "patient_id": 12345,
                "name_first": "John",
                "name_last": "Doe",
                "name_middle": "",
                "name_suffix": "",
                "name_preferred": "",
                "email": "john.doe@example.com",
                "dob": "1990-01-01",
                "ssn": "123-45-6789",
                "phone_home": "(123) 456-7890",
                "phone_mobile": "(123) 456-7890",
                "phone_fax": "(123) 456-7890"
            }
        }
    )
    patient_id: int


class SalesOrder(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "patient_id": 12345,
                "scheduled_time": "14:30",
                "actual_time": "15:45",
                "scheduled_date": "2024-02-15",
                "actual_date": "2024-02-15",
                "phone_delivery": "(123) 456-7890",
                "phone_mobile": "(987) 654-3210",
                "order_notes": "Please deliver before noon",
                "delivery_notes": "Gate code: 1234"
            }
        }
    )

    patient_id: int
    scheduled_time: str
    actual_time: str
    scheduled_date: str
    actual_date: str
    phone_delivery: Optional[str] = phone_number_format
    phone_mobile: Optional[str] = phone_number_format
    order_notes: str
    delivery_notes: str

    @field_validator('phone_delivery', 'phone_mobile')
    @classmethod
    def format_phone(cls, v: Optional[str]) -> str:
        if not v or v == phone_number_format:
            return phone_number_format

        numbers_only = re.sub(r'\D', '', v)

        if len(numbers_only) == 10:
            return f"({numbers_only[:3]}) {numbers_only[3:6]}-{numbers_only[6:]}"
        elif len(numbers_only) == 11 and numbers_only[0] == '1':
            return f"({numbers_only[1:4]}) {numbers_only[4:7]}-{numbers_only[7:]}"
        else:
            raise ValueError("Phone number must be a valid US number (10 digits)")

    @field_validator('scheduled_time', 'actual_time')
    @classmethod
    def format_time(cls, v: Optional[str]) -> str:
        if not v:
            return ""

        try:
            # Parse 24-hour time
            parsed_time = datetime.strptime(v, '%H:%M')
            # Convert to 12-hour format with AM/PM
            return parsed_time.strftime('%I:%M %p').lstrip('0')
        except ValueError:
            raise ValueError('Time must be in HH:MM format (24-hour)')

    @field_validator('scheduled_date', 'actual_date')
    @classmethod
    def validate_date_format(cls, val: Optional[str]) -> str:
        if not val:
            return ""

        try:
            # Attempt to parse the date string
            datetime.strptime(val, '%Y-%m-%d')
        except ValueError:
            raise ValueError('start_date must be in YYYY-MM-DD format')
        return val
