"""Common date boundaries; automatic flight stamps are implemented in 41c."""

from datetime import date

from models import Passport


def date_eligible(
    passport: Passport, visit_date: date, *, today: date | None = None
) -> bool:
    return (
        visit_date <= (today or date.today())
        and (passport.start_date is None or visit_date >= passport.start_date)
        and (passport.end_date is None or visit_date <= passport.end_date)
    )
