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


class TransportError(ElectrochemistryError):
    """Base error for an instrument communication transaction."""


class TransportConnectionError(TransportError):
    """A transport session could not be opened or is not connected."""


class TransportTimeoutError(TransportError, TimeoutError):
    """A bounded transport operation exceeded its configured timeout."""


class TransportProtocolError(TransportError):
    """An instrument reply did not satisfy the configured framing or protocol."""


class TransportResponseTooLarge(TransportProtocolError):
    """A reply exceeded the configured hard byte limit."""


class AmbiguousTransportError(TransportError):
    """A mutating command may have executed but its delivery was not confirmed."""


class IncompatibleInstrumentError(TransportProtocolError):
    """The connected unit identity or command language is unsupported."""
