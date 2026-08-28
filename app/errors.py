class CheckerError(Exception):
    """Base error that can safely be shown to a user."""


class InvalidParcelReference(CheckerError):
    pass


class ParcelNotFound(CheckerError):
    pass


class UpstreamServiceError(CheckerError):
    pass


class DocumentDownloadError(CheckerError):
    pass


class PDFExtractionError(CheckerError):
    pass
