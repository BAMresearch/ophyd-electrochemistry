"""Exception taxonomy for the intended implementation."""


class ElectrochemistryError(RuntimeError):
    """Base acquisition/backend error."""


class ValidationError(ElectrochemistryError):
    """Invalid or physically unsafe request."""


class UnsupportedCapabilityError(ElectrochemistryError):
    """A requested capability has not been implemented or commissioned."""


class AcquisitionAborted(ElectrochemistryError):
    """Acquisition unsuccessful, independent of successful shutdown."""


class ShutdownUnconfirmed(ElectrochemistryError):
    """Output state cannot be confirmed; report UNKNOWN and do not re-arm."""


class RetainedDataError(ElectrochemistryError):
    """New acquisition would overwrite unarchived retained data."""
