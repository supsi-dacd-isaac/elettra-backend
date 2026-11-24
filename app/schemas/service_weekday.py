from typing import Literal, get_args


ServiceWeekday = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]
SERVICE_WEEKDAYS = get_args(ServiceWeekday)
