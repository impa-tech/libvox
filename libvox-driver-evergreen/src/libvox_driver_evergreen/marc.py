"""MARCXML, read just far enough to fill a `BiblioRecord`.

Evergreen stores each record as MARC21 in MARCXML and hands it over as such. A
Koha flattens the same record into named fields before the driver sees it;
here the driver does that flattening, so the MARC tags stop in this file.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from libvox_driver_koha.errors import UnusableRecord

_NS = "{http://www.loc.gov/MARC21/slim}"


@dataclass(frozen=True, slots=True)
class DataField:
    tag: str
    ind1: str
    ind2: str
    subfields: tuple[tuple[str, str], ...]

    def first(self, *codes: str) -> str | None:
        for code, value in self.subfields:
            if code in codes and value.strip():
                return value
        return None

    def all(self, *codes: str) -> list[str]:
        return [value for code, value in self.subfields if code in codes and value.strip()]


@dataclass(frozen=True, slots=True)
class MarcRecord:
    leader: str = ""
    control: dict[str, str] = field(default_factory=dict)
    fields: tuple[DataField, ...] = ()

    def datafields(self, *tags: str) -> list[DataField]:
        return [f for f in self.fields if f.tag in tags]

    def first(self, tag: str, *codes: str) -> str | None:
        for f in self.datafields(tag):
            value = f.first(*codes)
            if value:
                return value
        return None


def parse(xml: str) -> MarcRecord:
    """One MARCXML `<record>`, as a `MarcRecord`.

    **A document type declaration is refused before anything parses it.**
    MARCXML never needs one, and a DTD is where entity expansion lives -- the
    one way a record from someone else's server could make this process
    allocate without bound. Refusing it costs a real catalogue nothing.
    """
    if not isinstance(xml, str) or not xml.strip():
        raise UnusableRecord("the record carries no MARC")
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise UnusableRecord("the record's MARCXML declares a DTD; refused")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise UnusableRecord(f"the record's MARCXML does not parse: {exc}") from exc

    if root.tag != f"{_NS}record":
        found = root.find(f".//{_NS}record")
        if found is None:
            raise UnusableRecord("no MARC21 record in the document")
        root = found

    leader = root.findtext(f"{_NS}leader") or ""
    control = {
        cf.get("tag", ""): cf.text or "" for cf in root.findall(f"{_NS}controlfield")
    }
    fields = tuple(
        DataField(
            tag=df.get("tag", ""),
            ind1=df.get("ind1", " "),
            ind2=df.get("ind2", " "),
            subfields=tuple(
                (sf.get("code", ""), sf.text or "") for sf in df.findall(f"{_NS}subfield")
            ),
        )
        for df in root.findall(f"{_NS}datafield")
    )
    return MarcRecord(leader=leader, control=control, fields=fields)


def records_in(xml: str) -> list[MarcRecord]:
    """Every MARC21 record in a larger document, such as an SRU response."""
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise UnusableRecord("the document declares a DTD; refused")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise UnusableRecord(f"the document does not parse: {exc}") from exc
    return [
        parse(ET.tostring(node, encoding="unicode"))
        for node in root.iter(f"{_NS}record")
    ]
