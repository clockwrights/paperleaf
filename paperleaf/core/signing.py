"""Certificate (digital) signatures with pyHanko: create a self-signed digital
ID, sign a PDF with a .pfx/.p12 file, and check signatures. No Qt here.

Offline only: certificates are never fetched and revocation is not checked
online (``allow_fetching=False``); no timestamp server is used. On top of that,
every pyHanko call runs inside ``_offline()``, which refuses network
connections (pyHanko imports an HTTP client it never needs here). Trust comes
from the Windows certificate store (through oscrypto) plus the certificates
the user chose to trust in Paperleaf.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import glob
import os
import socket
from dataclasses import dataclass

import pymupdf

ID_FILTER = "Digital IDs (*.pfx *.p12);;All files (*.*)"


def _appdir(name: str) -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "Paperleaf", name)
    os.makedirs(path, exist_ok=True)
    return path


@contextlib.contextmanager
def _offline():
    """No network connections while pyHanko runs."""
    def refuse(*_a, **_k):
        raise OSError("Paperleaf works offline: network access is blocked.")
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    saved_pair = socket.socketpair

    def pair(*a, **k):
        # On Windows asyncio's event loop makes its wake-up pipe with socketpair(),
        # which connects two sockets over loopback inside this process. Allow
        # only that; everything else stays blocked.
        socket.socket.connect = saved[0]
        try:
            return saved_pair(*a, **k)
        finally:
            socket.socket.connect = refuse

    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse
    socket.socketpair = pair
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved
        socket.socketpair = saved_pair


def ids_dir() -> str:
    return _appdir("ids")


def trusted_dir() -> str:
    return _appdir("trusted")


def available() -> bool:
    import importlib.util
    return importlib.util.find_spec("pyhanko") is not None


# ================================================================ digital IDs
def create_id(name: str, password: str, email: str = "", organization: str = "", path: str | None = None,
              years: int = 5) -> str:
    """Create a self-signed digital ID (RSA 2048) as a password-protected .pfx file."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    if not name.strip():
        raise ValueError("Enter your name.")
    if len(password) < 6:
        raise ValueError("Use a password of at least 6 characters.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, name.strip())]
    if organization.strip():
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization.strip()))
    if email.strip():
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email.strip()))
    subject = x509.Name(attrs)
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(minutes=5))
            .not_valid_after(now + _dt.timedelta(days=365 * years))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .sign(key, hashes.SHA256()))
    data = pkcs12.serialize_key_and_certificates(
        name.strip().encode("utf-8"), key, cert, None,
        serialization.BestAvailableEncryption(password.encode("utf-8")))
    if path is None:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "Digital ID"
        path = os.path.join(ids_dir(), f"{safe}.pfx")
        k = 2
        while os.path.exists(path):
            path = os.path.join(ids_dir(), f"{safe} ({k}).pfx")
            k += 1
    with open(path, "wb") as f:
        f.write(data)
    return path


def list_ids() -> list[str]:
    return sorted(glob.glob(os.path.join(ids_dir(), "*.pfx")) + glob.glob(os.path.join(ids_dir(), "*.p12")))


@dataclass
class IdInfo:
    path: str
    name: str
    issuer: str
    expires: str
    self_signed: bool


def id_info(path: str, password: str) -> IdInfo:
    """Read a digital ID (checks the password)."""
    from cryptography.hazmat.primitives.serialization import pkcs12
    with open(path, "rb") as f:
        key, cert, _extra = pkcs12.load_key_and_certificates(f.read(), password.encode("utf-8"))
    if key is None or cert is None:
        raise ValueError("This file has no signing key.")
    return IdInfo(path, cert.subject.rfc4514_string(), cert.issuer.rfc4514_string(),
                  cert.not_valid_after_utc.strftime("%Y-%m-%d"), cert.subject == cert.issuer)


def _common_name(dn: str) -> str:
    for part in dn.split(","):
        if part.strip().upper().startswith("CN="):
            return part.strip()[3:]
    return dn


# ================================================================ signing
def _pdf_box(path: str, password: str | None, pno: int, rect) -> tuple:
    """Unrotated top-left page coordinates -> PDF user space (x1, y1, x2, y2)."""
    d = pymupdf.open(path)
    try:
        if d.needs_pass:
            d.authenticate(password or "")
        page = d[pno]
        mb = page.mediabox
        r = pymupdf.Rect(rect)
        # PyMuPDF's unrotated space is the cropbox seen from the top of the mediabox
        cb = page.cropbox
        x0, x1 = r.x0 + cb.x0, r.x1 + cb.x0
        top = mb.y1  # PDF y of the mediabox top
        raw = d.xref_get_key(page.xref, "MediaBox")
        nums = [float(v) for v in raw[1].strip("[]").split()] if raw[0] == "array" else [mb.x0, mb.y0, mb.x1, mb.y1]
        top = max(nums[1], nums[3])
        y_top = top - (r.y0 + cb.y0)
        y_bottom = top - (r.y1 + cb.y0)
        return (x0, y_bottom, x1, y_top)
    finally:
        d.close()


def empty_signature_fields(path: str, password: str | None = None) -> list[tuple[str, int]]:
    """(field name, page) of unsigned signature fields."""
    d = pymupdf.open(path)
    try:
        if d.needs_pass:
            d.authenticate(password or "")
        out = []
        for p in range(d.page_count):
            for w in d[p].widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
                if not w.is_signed:
                    out.append((w.field_name, p))
        return out
    finally:
        d.close()


def _indirect_encrypt(data: bytes) -> bytes:
    """MuPDF writes the /Encrypt dictionary inline in the trailer; pyHanko's
    incremental writer needs it as an object. Append a small update that
    repeats the same (never encrypted) dictionary as an object."""
    d = pymupdf.open(stream=data, filetype="pdf")
    try:
        kind, value = d.xref_get_key(-1, "Encrypt")
        if kind != "dict":
            return data
        root = d.xref_get_key(-1, "Root")[1]
        ident = d.xref_get_key(-1, "ID")
        num = d.xref_length()
    finally:
        d.close()
    i = data.rfind(b"startxref")
    if i < 0:
        return data
    prev = int(data[i + 9:].split()[0])
    body = bytearray(data if data.endswith(b"\n") else data + b"\n")
    obj_at = len(body)
    body += f"{num} 0 obj\n{value}\nendobj\n".encode("latin-1")
    xref_at = len(body)
    trailer = f"<</Size {num + 1}/Root {root}/Prev {prev}/Encrypt {num} 0 R"
    if ident[0] == "array":
        trailer += f"/ID{ident[1]}"
    body += (f"xref\n0 1\n0000000000 65535 f \n{num} 1\n{obj_at:010d} 00000 n \n"
             f"trailer\n{trailer}>>\nstartxref\n{xref_at}\n%%EOF\n").encode("latin-1")
    return bytes(body)


def sign(src: str, out: str, pfx: str, pfx_password: str, *, field: str | None = None,
         page: int = 0, rect=None, reason: str = "", location: str = "", contact: str = "",
         doc_password: str | None = None) -> str:
    """Sign ``src`` into ``out``. Use an existing empty signature ``field``, or
    make a new one at ``rect`` (unrotated page coordinates) on ``page``."""
    with _offline():
        return _sign(src, out, pfx, pfx_password, field=field, page=page, rect=rect, reason=reason,
                     location=location, contact=contact, doc_password=doc_password)


def _sign(src, out, pfx, pfx_password, *, field, page, rect, reason, location, contact, doc_password):
    from pyhanko import stamp
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    signer = signers.SimpleSigner.load_pkcs12(pfx, passphrase=pfx_password.encode("utf-8"))
    if signer is None:
        raise ValueError("The digital ID could not be opened. Check its password.")
    name = _common_name(signer.signing_cert.subject.human_friendly.replace("Common Name: ", "CN="))
    try:
        name = signer.signing_cert.subject.native.get("common_name") or name
    except Exception:
        pass
    with open(src, "rb") as inf:
        data = inf.read()
    if doc_password is not None or b"/Encrypt" in data:
        data = _indirect_encrypt(data)
    import io
    w = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)   # MuPDF writes /Encrypt inline
    if w.security_handler is not None:
        w.encrypt(doc_password or "")
    if field is None:
        field = "Signature" + _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        box = _pdf_box(src, doc_password, page, rect or (360, 700, 560, 760))
        fields.append_signature_field(w, fields.SigFieldSpec(sig_field_name=field, on_page=page, box=box))
    meta = signers.PdfSignatureMetadata(field_name=field, reason=reason or None, location=location or None,
                                        contact_info=contact or None, name=name)
    style = stamp.TextStampStyle(stamp_text="Digitally signed by %(signer)s\nDate: %(ts)s",
                                 border_width=0)
    pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=style)
    tmp = out + ".paperleaf-saving"
    with open(tmp, "wb") as outf:
        pdf_signer.sign_pdf(w, output=outf)
    os.replace(tmp, out)
    return out


# ================================================================ checking
@dataclass
class SignatureStatus:
    field: str
    signer: str
    when: str
    reason: str
    location: str
    intact: bool         # the signed bytes were not changed
    valid: bool          # the cryptography checks out
    trusted: bool        # the signer's certificate is trusted on this computer
    whole_file: bool     # nothing was added after signing
    error: str = ""

    @property
    def summary(self) -> str:
        if self.error:
            return f"Could not check this signature: {self.error}"
        if not self.intact or not self.valid:
            return "Invalid: the document was changed after it was signed, or the signature is damaged."
        parts = ["The document has not been changed since it was signed." if self.whole_file else
                 "The signed part is unchanged; changes were added after signing."]
        parts.append("The signer's certificate is trusted." if self.trusted else
                     "The signer's identity is unknown: the certificate is not trusted on this computer.")
        return " ".join(parts)

    @property
    def level(self) -> str:
        if self.error or not self.intact or not self.valid:
            return "invalid"
        return "valid" if self.trusted and self.whole_file else "unknown"


def _trust_roots():
    roots = []
    try:
        from oscrypto import trust_list
        roots += [c for c in trust_list.get_list()]
    except Exception:
        pass
    from asn1crypto import pem, x509 as ax509
    for f in glob.glob(os.path.join(trusted_dir(), "*")):
        try:
            with open(f, "rb") as fh:
                raw = fh.read()
            if pem.detect(raw):
                _t, _h, raw = pem.unarmor(raw)
            roots.append(ax509.Certificate.load(raw))
        except Exception:
            continue
    out = []
    for c in roots:
        out.append(c[0] if isinstance(c, tuple) else c)
    return out


def trust_certificate_of(pfx: str, password: str) -> str:
    """Trust the certificate of one of your own digital IDs (so your signatures show as trusted)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    with open(pfx, "rb") as f:
        _k, cert, _x = pkcs12.load_key_and_certificates(f.read(), password.encode("utf-8"))
    target = os.path.join(trusted_dir(), os.path.splitext(os.path.basename(pfx))[0] + ".cer")
    with open(target, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.DER))
    return target


def check(data: bytes, password: str | None = None) -> list[SignatureStatus]:
    """Check every signature in a PDF (given as bytes), offline."""
    with _offline():
        return _check(data, password)


def _check(data: bytes, password: str | None) -> list[SignatureStatus]:
    import io
    from pyhanko.pdf_utils.misc import PdfReadError
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    import logging
    for name in ("pyhanko", "pyhanko_certvalidator"):
        logging.getLogger(name).setLevel(logging.CRITICAL)  # "self-signed" etc. are reported below
    r = PdfFileReader(io.BytesIO(data), strict=False)
    if r.security_handler is not None:
        r.decrypt(password or "")
    roots = _trust_roots()
    out = []
    for sig in r.embedded_signatures:
        name = sig.field_name or ""
        try:
            vc = ValidationContext(trust_roots=roots, allow_fetching=False, revocation_mode="soft-fail")
            try:
                st = validate_pdf_signature(sig, vc)
            except PdfReadError:
                # the change analysis can't read the form (e.g. two fields with the same
                # name); the signed bytes and the coverage are still checked without it
                vc = ValidationContext(trust_roots=roots, allow_fetching=False, revocation_mode="soft-fail")
                st = validate_pdf_signature(sig, vc, skip_diff=True)
            cert = st.signing_cert
            signer = cert.subject.native.get("common_name") or cert.subject.human_friendly
            when = st.signer_reported_dt.strftime("%Y-%m-%d %H:%M") if st.signer_reported_dt else ""
            sd = sig.sig_object
            out.append(SignatureStatus(
                name, str(signer), when, str(sd.get("/Reason", "") or ""), str(sd.get("/Location", "") or ""),
                bool(st.intact), bool(st.valid), bool(st.trusted),
                str(getattr(st.coverage, "name", st.coverage)) == "ENTIRE_FILE"))
        except Exception as exc:
            out.append(SignatureStatus(name, "", "", "", "", False, False, False, False, str(exc)))
    return out


def has_signatures(doc) -> bool:
    try:
        return bool(doc.is_pdf and doc.doc.get_sigflags() > 0 and doc.is_signed())
    except Exception:
        return False
