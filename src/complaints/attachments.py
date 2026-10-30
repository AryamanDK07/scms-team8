"""Evidence attachment rules for SCMS (SCMS-F-008).

Up to three files per complaint, each a JPG, PNG or PDF of at most
5 MB.

The file type is decided by reading the first few bytes, not by
trusting the extension or the browser-supplied Content-Type. Both
of those are chosen by the client, so an executable renamed to
.jpg would otherwise be accepted and stored. Checking the magic
number means the stored file really is the kind of file it claims
to be.
"""

from pathlib import Path

from django.conf import settings
from django.utils.module_loading import import_string

#: Maximum size of a single attachment.
MAX_BYTES = 5 * 1024 * 1024

#: Maximum number of attachments on one complaint.
MAX_PER_COMPLAINT = 3

#: Magic numbers for the three permitted formats, as
#: (signature, offset, label, canonical content type).
SIGNATURES = (
    (b"\xff\xd8\xff", 0, "JPG", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", 0, "PNG", "image/png"),
    (b"%PDF", 0, "PDF", "application/pdf"),
)

#: Extensions that may accompany each detected format.
EXTENSIONS = {
    "JPG": {".jpg", ".jpeg"},
    "PNG": {".png"},
    "PDF": {".pdf"},
}

ALLOWED_LABELS = "JPG, PNG or PDF"


class AttachmentRejected(Exception):
    """Raised with a message meant to be shown to the complainant."""


def get_scanner():
    """Return the configured malware scanner, or None.

    SCMS-SR-005 requires uploads to be scanned for malware. A
    scanner is an external service -- ClamAV or similar -- and none
    is available to this project, so ATTACHMENT_MALWARE_SCANNER is
    empty by default and no scanning happens.

    That is stated plainly rather than papered over. A function
    here that always returned "clean" would make the requirement
    look met while giving no protection at all, which is worse than
    the gap, because it would stop anyone looking again.

    What is built and tested is the integration point: set
    ATTACHMENT_MALWARE_SCANNER to the dotted path of a callable
    taking the upload and returning a truthy verdict for an
    infected file, and every upload goes through it before being
    stored.
    """
    path = getattr(settings, "ATTACHMENT_MALWARE_SCANNER", "")

    return import_string(path) if path else None


def scan_upload(upload):
    """Reject the upload if the configured scanner flags it.

    Does nothing when no scanner is configured, which is the
    default. The file position is restored afterwards so the
    scanner cannot leave the upload half-read for whoever stores
    it next.
    """
    scanner = get_scanner()

    if scanner is None:
        return

    try:
        verdict = scanner(upload)
    finally:
        upload.seek(0)

    if verdict:
        raise AttachmentRejected(
            f"\"{upload.name}\" was rejected by the malware scanner "
            f"({verdict}). It has not been stored."
        )


def detect_format(upload):
    """Return the format label read from the file's leading bytes.

    None if the content matches none of the permitted formats.
    """
    longest = max(len(signature) for signature, *_ in SIGNATURES)

    upload.seek(0)
    head = upload.read(longest)
    upload.seek(0)

    for signature, offset, label, _ in SIGNATURES:
        if head[offset:offset + len(signature)] == signature:
            return label

    return None


def canonical_content_type(label):
    for _, _, candidate, content_type in SIGNATURES:
        if candidate == label:
            return content_type

    return "application/octet-stream"


def validate_upload(upload, existing_count=0):
    """Check one upload against SCMS-F-008.

    Raises AttachmentRejected with a message that says which rule
    was broken, so the complainant can fix it rather than guess.
    Returns the detected format label.
    """
    if existing_count >= MAX_PER_COMPLAINT:
        raise AttachmentRejected(
            f"A complaint may have at most "
            f"{MAX_PER_COMPLAINT} attachments. "
            f"Remove one before adding another."
        )

    if upload.size > MAX_BYTES:
        megabytes = upload.size / (1024 * 1024)
        raise AttachmentRejected(
            f"\"{upload.name}\" is {megabytes:.1f} MB. "
            f"Each attachment must be 5 MB or smaller."
        )

    if upload.size == 0:
        raise AttachmentRejected(
            f"\"{upload.name}\" is empty."
        )

    label = detect_format(upload)

    if label is None:
        raise AttachmentRejected(
            f"\"{upload.name}\" is not a {ALLOWED_LABELS} file. "
            f"Its contents do not match any of those formats."
        )

    suffix = Path(upload.name).suffix.lower()

    if suffix not in EXTENSIONS[label]:
        raise AttachmentRejected(
            f"\"{upload.name}\" contains {label} data but has the "
            f"extension \"{suffix}\". Rename it to match its "
            f"contents and try again."
        )

    # Last, so the scanner is spared the files already refused on
    # size, emptiness or format -- and so a rejection message names
    # the simplest reason the upload failed rather than the most
    # alarming one.
    scan_upload(upload)

    return label
