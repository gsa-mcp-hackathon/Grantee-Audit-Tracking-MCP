"""Related organizations from an e-filed Form 990's Schedule R.

Used by scripts/fetch_schedule_r.py on return XML from the IRS e-file zips.
Parts I-IV list related organizations, one group element per row; Part V
(transactions) and Part VI (unrelated partnerships) are left out.
"""

import xml.etree.ElementTree as ET

NS = "{http://www.irs.gov/efile}"

# (XML group, Schedule R part, relationship), in the form's order.
PARTS = [
    ("IdDisregardedEntitiesGrp", "I", "disregarded entity"),
    ("IdRelatedTaxExemptOrgGrp", "II", "related tax-exempt organization"),
    (
        "IdRelatedOrgTxblPartnershipGrp",
        "III",
        "related organization taxable as a partnership",
    ),
    (
        "IdRelatedOrgTxblCorpTrGrp",
        "IV",
        "related organization taxable as a corporation or trust",
    ),
]
ENTITY_TYPE = {"C": "corporation", "T": "trust"}  # Part IV, column (f)


def schedule_r(xml):
    """(filing, related) from one return's XML bytes.

    filing has the filer's ein and name, tax_year, tax_period_end, form, and
    whether a Schedule R is attached; related has one dict per row of Parts
    I-IV. A return without Schedule R has no related rows.
    """
    root = ET.fromstring(xml)
    header = root.find(f"{NS}ReturnHeader")
    filer = header.find(f"{NS}Filer") if header is not None else None
    if filer is None:
        raise ValueError("not an e-filed return: no ReturnHeader/Filer")
    schedule = root.find(f"{NS}ReturnData/{NS}IRS990ScheduleR")
    filing = {
        "ein": _text(filer, "EIN"),
        "name": _name(filer.find(f"{NS}BusinessName")),
        "tax_year": _text(header, "TaxYr"),
        "tax_period_end": _text(header, "TaxPeriodEndDt"),
        "form": _text(header, "ReturnTypeCd"),
        "has_schedule_r": schedule is not None,
    }
    if schedule is None:
        return filing, []
    related = []
    for group, part, relationship in PARTS:
        for row in schedule.iter(f"{NS}{group}"):
            name = row.find(f"{NS}DisregardedEntityName")
            if name is None:
                name = row.find(f"{NS}RelatedOrganizationName")
            controlled = _text(row, "ControlledOrganizationInd")
            entity_type = _text(row, "EntityTypeTxt")
            related.append(
                {
                    "part": part,
                    "relationship": relationship,
                    "name": _name(name),
                    "ein": _text(row, "EIN"),
                    "state": _text(row, "USAddress/StateAbbreviationCd"),
                    "primary_activity": _text(row, "PrimaryActivitiesTxt"),
                    "legal_domicile": _text(row, "LegalDomicileStateCd")
                    or _text(row, "LegalDomicileForeignCountryCd"),
                    "exempt_code_section": _text(row, "ExemptCodeSectionTxt"),
                    "entity_type": ENTITY_TYPE.get(entity_type, entity_type),
                    "direct_controlling_entity": _name(
                        row.find(f"{NS}DirectControllingEntityName")
                    ),
                    # Parts II and IV only: 1/X yes, 0 no, blank not asked.
                    "controlled": {"1": "Y", "X": "Y", "0": "N"}.get(controlled, ""),
                }
            )
    return filing, related


def _text(element, path):
    """Stripped text at a path of unprefixed tags, or '' if absent."""
    found = element.find("/".join(f"{NS}{tag}" for tag in path.split("/")))
    return (found.text or "").strip() if found is not None else ""


def _name(element):
    """A BusinessName-style element's two lines, joined."""
    if element is None:
        return ""
    lines = [
        _text(element, "BusinessNameLine1Txt"),
        _text(element, "BusinessNameLine2Txt"),
    ]
    return " ".join(line for line in lines if line)
