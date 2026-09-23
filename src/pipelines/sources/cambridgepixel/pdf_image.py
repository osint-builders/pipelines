import hashlib
from io import BytesIO

from pypdf import PdfReader

from pipelines.media import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS

MAX_PDF_BYTES = 20 * 1024 * 1024


def extract(body: bytes, review: dict) -> tuple[bytes, str]:
    selection = review["pdf_image"]
    if (
        not body.startswith(b"%PDF-")
        or len(body) > MAX_PDF_BYTES
        or hashlib.sha256(body).hexdigest() != selection["document_sha256"]
        or review["image_url"] != review["page_url"]
    ):
        raise ValueError("Reviewed radar PDF changed")
    reader = PdfReader(BytesIO(body))
    page_number = selection["page"]
    if type(page_number) is not int or not 1 <= page_number <= len(reader.pages):
        raise ValueError("Reviewed radar PDF page is missing")
    page = reader.pages[page_number - 1]
    if not review["quote"] or review["quote"] not in " ".join(
        page.extract_text().split()
    ):
        raise ValueError("Reviewed radar PDF quote changed")
    images = [image for image in page.images if image.name == selection["name"]]
    if len(images) != 1:
        raise ValueError("Reviewed radar PDF image is missing or ambiguous")
    image = images[0]
    if (
        not image.data
        or len(image.data) > MAX_IMAGE_BYTES
        or image.image is None
        or image.image.width * image.image.height > MAX_IMAGE_PIXELS
        or hashlib.sha256(image.data).hexdigest() != selection["image_sha256"]
    ):
        raise ValueError("Reviewed radar PDF image changed")
    mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image.image.format or "")
    if image.image.format == "JPEG2000" and selection.get("png_sha256"):
        output = BytesIO()
        image.image.save(output, format="PNG")
        body = output.getvalue()
        if (
            len(body) > MAX_IMAGE_BYTES
            or hashlib.sha256(body).hexdigest() != selection["png_sha256"]
        ):
            raise ValueError("Reviewed radar PNG conversion changed")
        return body, "image/png"
    if mime is None:
        raise ValueError("Unsupported embedded radar image format")
    return image.data, mime
