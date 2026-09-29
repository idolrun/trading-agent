"""Tradable instrument value types."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OptionContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: str = Field(pattern=r"^[A-Z]{1,6}$")  # option root, e.g. SPXW
    expiry: date
    strike: Decimal = Field(gt=0)
    right: Literal["call", "put"]

    @field_validator("strike")
    @classmethod
    def _strike_fits_occ(cls, strike: Decimal) -> Decimal:
        if (strike * 1000) % 1 != 0 or strike * 1000 >= 10**8:
            raise ValueError("strike must have at most 3 decimals and be below 100000")
        return strike

    def occ_symbol(self) -> str:
        """OCC symbology: root padded to 6, YYMMDD, C/P, strike x1000 in 8 digits."""
        right = "C" if self.right == "call" else "P"
        return f"{self.underlying:<6}{self.expiry:%y%m%d}{right}{int(self.strike * 1000):08d}"


class Stock(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(pattern=r"^[A-Z][A-Z.]{0,9}$")


Instrument = OptionContract | Stock


def broker_symbol(instrument: Instrument) -> str:
    """OCC symbol for options, ticker for stocks (the `broker_symbol` column)."""
    return instrument.occ_symbol() if isinstance(instrument, OptionContract) else instrument.symbol


def option_from_occ(symbol: str) -> OptionContract:
    """Inverse of `OptionContract.occ_symbol()`."""
    return OptionContract(
        underlying=symbol[:6].strip(),
        expiry=datetime.strptime(symbol[6:12], "%y%m%d").date(),
        strike=Decimal(symbol[13:]) / 1000,
        right="call" if symbol[12] == "C" else "put",
    )
