from enum import Enum

class TripStatus(str, Enum):
    """Trip status enumeration matching the database enum values"""
    GTFS = "gtfs"
    DEPOT = "depot"
    SCHOOL = "school"
    SERVICE = "service"
    OTHER = "other"
