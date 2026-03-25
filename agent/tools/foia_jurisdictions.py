"""
agent/tools/foia_jurisdictions.py
==================================
Liberation Bot — FOIA Jurisdiction Data Layer

This module is the single source of truth for public records law metadata
across all 50 U.S. states, Washington D.C., and the federal government.

For each jurisdiction it records:
  - The official name of the public records law
  - The statutory citation
  - The response deadline (in calendar days, or a string for "prompt"/"none")
  - Whether requests are restricted to residents only
  - The primary submission method and contact point (where known)
  - Common exemptions relevant to Neurowarfare / AHI requesters
  - Whether the jurisdiction has an online FOIA portal

This data is consumed by:
  - FOIADialogueAgent  (agent/foia_dialogue.py) — injected into the LLM context
  - FOIASession        (bot/foia_session.py)    — displayed in previews
  - bot.py             — shown via !foia_jurisdictions command
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

JurisdictionInfo = dict  # typed dict-style, kept simple for JSON serialisability


# ---------------------------------------------------------------------------
# Federal agencies most relevant to Neurowarfare / AHI victims
# ---------------------------------------------------------------------------

FEDERAL_AHI_AGENCIES: list[dict] = [
    {
        "name": "Central Intelligence Agency (CIA)",
        "abbreviation": "CIA",
        "foia_email": "cia-foia@ucia.gov",
        "foia_portal": "https://www.cia.gov/resources/foia/",
        "notes": (
            "Primary intelligence agency investigating AHIs abroad. "
            "Expect long delays (months to years) due to classification review. "
            "Cite the HAVANA Act (2021) and NDAA provisions in your request."
        ),
    },
    {
        "name": "Department of Defense (DoD)",
        "abbreviation": "DOD",
        "foia_email": "osd.pentagon.osd-cmo.mbx.osd-foia@mail.mil",
        "foia_portal": "https://www.esd.whs.mil/FOIA/",
        "notes": (
            "Oversees military AHI investigations. File with the specific component "
            "(e.g., DIA, Army, Navy) most likely to hold records. "
            "DoD IG is a separate office: dodig.foia@dodig.mil"
        ),
    },
    {
        "name": "Federal Bureau of Investigation (FBI)",
        "abbreviation": "FBI",
        "foia_email": "foiparequest@fbi.gov",
        "foia_portal": "https://www.fbi.gov/services/records-management/foipa",
        "notes": (
            "Maintains field office records in addition to HQ records. "
            "An internal FBI report on AHIs was widely criticised as flawed. "
            "Request specifically: 'all records related to Anomalous Health Incidents "
            "or Havana Syndrome investigations.'"
        ),
    },
    {
        "name": "Department of State",
        "abbreviation": "DOS",
        "foia_email": "FOIArequest@state.gov",
        "foia_portal": "https://foia.state.gov/",
        "notes": (
            "Holds records on diplomatic personnel AHI cases (Cuba, China, etc.). "
            "The State Department has been processing and releasing AHI records "
            "pursuant to ongoing FOIA litigation."
        ),
    },
    {
        "name": "National Security Agency (NSA)",
        "abbreviation": "NSA",
        "foia_email": "nsafoia@nsa.gov",
        "foia_portal": "https://www.nsa.gov/Resources/Everyone/FOIA/",
        "notes": (
            "Holds signals intelligence records. Expect heavy redactions. "
            "Use precise technical terminology (e.g., 'pulsed radiofrequency energy', "
            "'directed microwave energy') rather than colloquial terms."
        ),
    },
    {
        "name": "Defense Intelligence Agency (DIA)",
        "abbreviation": "DIA",
        "foia_email": "dia-foia@dodiis.mil",
        "foia_portal": "https://www.dia.mil/About/FOIA/",
        "notes": (
            "Produced key AHI intelligence assessments. "
            "Request all records related to 'Anomalous Health Incidents', "
            "'directed energy weapons assessments', and 'foreign adversary capabilities.'"
        ),
    },
    {
        "name": "Department of Health and Human Services (HHS)",
        "abbreviation": "HHS",
        "foia_email": "foia@hhs.gov",
        "foia_portal": "https://www.hhs.gov/foia/index.html",
        "notes": (
            "NIH and CDC fall under HHS. Relevant for medical research records "
            "on AHI symptoms, treatment protocols, and neurological studies."
        ),
    },
    {
        "name": "Department of Homeland Security (DHS)",
        "abbreviation": "DHS",
        "foia_email": "dhsfoia@hq.dhs.gov",
        "foia_portal": "https://www.dhs.gov/foia",
        "notes": (
            "DHS reportedly acquired a device believed to be related to AHI attacks "
            "under the Biden administration. Request records related to 'directed "
            "energy device acquisition' and 'AHI investigation programs.'"
        ),
    },
    {
        "name": "Office of the Director of National Intelligence (ODNI)",
        "abbreviation": "ODNI",
        "foia_email": "odni-foia@dni.gov",
        "foia_portal": "https://www.dni.gov/index.php/who-we-are/organizations/general-counsel/foia",
        "notes": (
            "Published the February 2022 AHI Executive Summary. "
            "Request the full unredacted assessment and all supporting intelligence."
        ),
    },
]


# ---------------------------------------------------------------------------
# State jurisdiction registry
# ---------------------------------------------------------------------------
# Keys: two-letter state abbreviation (uppercase) or "DC" or "FEDERAL"
# ---------------------------------------------------------------------------

JURISDICTIONS: dict[str, JurisdictionInfo] = {

    # ---- Federal ----
    "FEDERAL": {
        "name": "Federal",
        "law_name": "Freedom of Information Act (FOIA)",
        "citation": "5 U.S.C. § 552",
        "response_days": 20,          # working days
        "response_note": "20 working days; national-security agencies routinely take months to years",
        "deadline_days": 20,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media / educational / non-commercial scientific",
        "expedited_processing": True,
        "expedited_grounds": "Imminent threat to life or physical safety; urgency to inform public",
        "submission_methods": ["Online portal (FOIA.gov)", "Agency-specific email", "Mail"],
        "portal_url": "https://www.foia.gov/",
        "common_exemptions": [
            "Exemption 1 — Classified national security information",
            "Exemption 3 — Specifically exempted by statute",
            "Exemption 5 — Deliberative process / attorney-client privilege",
            "Exemption 6 — Personal privacy",
            "Exemption 7 — Law enforcement records",
        ],
        "appeal_deadline_days": 90,
        "appeal_body": "Agency's FOIA Appeals Office, then U.S. District Court",
        "ahi_notes": (
            "For AHI/Neurowarfare requests, target the CIA, DOD, FBI, State Department, "
            "NSA, DIA, ODNI, and DHS. Use the HAVANA Act (2021) as leverage. "
            "Cite 'Anomalous Health Incidents' as the preferred official terminology."
        ),
    },

    # ---- Alabama ----
    "AL": {
        "name": "Alabama",
        "law_name": "Alabama Public Records Law",
        "citation": "Ala. Code § 36-12-40",
        "response_days": None,
        "response_note": "No statutory deadline; must respond within a 'reasonable time'",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request (mail or email)"],
        "portal_url": None,
        "common_exemptions": ["Law enforcement records", "Personal privacy", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- Alaska ----
    "AK": {
        "name": "Alaska",
        "law_name": "Alaska Public Records Act",
        "citation": "Alaska Stat. § 40.25.110",
        "response_days": 10,
        "response_note": "10 business days",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / waiver at agency discretion",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Attorney-client privilege"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- Arizona ----
    "AZ": {
        "name": "Arizona",
        "law_name": "Arizona Public Records Law",
        "citation": "Ariz. Rev. Stat. § 39-121",
        "response_days": None,
        "response_note": "Prompt; no specific statutory deadline",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Waiver at agency discretion",
        "expedited_processing": False,
        "submission_methods": ["Written or verbal request (written strongly recommended)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- Arkansas ----
    "AR": {
        "name": "Arkansas",
        "law_name": "Arkansas Freedom of Information Act",
        "citation": "Ark. Code Ann. § 25-19-101 et seq.",
        "response_days": 3,
        "response_note": "3 working days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": True,
        "fee_waiver_available": False,
        "fee_waiver_grounds": None,
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personnel records", "Medical records", "Law enforcement"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": "Note: Requests are restricted to Arkansas residents.",
    },

    # ---- California ----
    "CA": {
        "name": "California",
        "law_name": "California Public Records Act (CPRA)",
        "citation": "Cal. Gov. Code § 7920.000 et seq.",
        "response_days": 10,
        "response_note": "10 calendar days to acknowledge; 14-day extension possible",
        "deadline_days": 10,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / waiver at agency discretion",
        "expedited_processing": False,
        "submission_methods": ["Written request (mail, email, or online portal)"],
        "portal_url": None,
        "common_exemptions": [
            "Personal privacy (Cal. Gov. Code § 6254(c))",
            "Law enforcement (§ 6254(f))",
            "Preliminary drafts / deliberative process",
            "Litigation privilege",
        ],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- Colorado ----
    "CO": {
        "name": "Colorado",
        "law_name": "Colorado Open Records Act (CORA)",
        "citation": "Colo. Rev. Stat. § 24-72-201 et seq.",
        "response_days": None,
        "response_note": "Prompt; 3 business days for inspection; copies within a reasonable time",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Waiver at agency discretion",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Connecticut ----
    "CT": {
        "name": "Connecticut",
        "law_name": "Connecticut Freedom of Information Act",
        "citation": "Conn. Gen. Stat. § 1-200 et seq.",
        "response_days": 4,
        "response_note": "4 business days",
        "deadline_days": 4,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / indigency",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personnel records", "Medical records", "Law enforcement"],
        "appeal_deadline_days": 30,
        "appeal_body": "Freedom of Information Commission (FOIC)",
        "ahi_notes": None,
    },

    # ---- Delaware ----
    "DE": {
        "name": "Delaware",
        "law_name": "Delaware Freedom of Information Act",
        "citation": "Del. Code Ann. tit. 29, § 10001 et seq.",
        "response_days": 15,
        "response_note": "15 business days",
        "deadline_days": 15,
        "deadline_type": "working",
        "residents_only": True,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": "Note: Requests are restricted to Delaware residents.",
    },

    # ---- Washington D.C. ----
    "DC": {
        "name": "Washington, D.C.",
        "law_name": "District of Columbia Freedom of Information Act",
        "citation": "D.C. Code § 2-531 et seq.",
        "response_days": 15,
        "response_note": "15 business days",
        "deadline_days": 15,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": True,
        "expedited_grounds": "Imminent threat to life or safety",
        "submission_methods": ["Written request (online portal or mail)"],
        "portal_url": "https://foia-dc.gov/",
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": 90,
        "appeal_body": "D.C. Office of Open Government, then D.C. Superior Court",
        "ahi_notes": None,
    },

    # ---- Florida ----
    "FL": {
        "name": "Florida",
        "law_name": "Florida Sunshine Law / Public Records Act",
        "citation": "Fla. Stat. § 119.01 et seq.",
        "response_days": None,
        "response_note": "Prompt; no specific deadline — one of the strongest open-records laws in the US",
        "deadline_days": 10,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": False,
        "fee_waiver_grounds": "Fees may be waived at agency discretion for public interest",
        "expedited_processing": False,
        "submission_methods": ["Written or verbal request (written recommended)"],
        "portal_url": None,
        "common_exemptions": [
            "Law enforcement active investigations",
            "Medical records",
            "Personal privacy",
        ],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court; Attorney General enforcement",
        "ahi_notes": None,
    },

    # ---- Georgia ----
    "GA": {
        "name": "Georgia",
        "law_name": "Georgia Open Records Act",
        "citation": "Ga. Code Ann. § 50-18-70 et seq.",
        "response_days": 3,
        "response_note": "3 business days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Law enforcement", "Personal privacy", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court; Attorney General",
        "ahi_notes": None,
    },

    # ---- Hawaii ----
    "HI": {
        "name": "Hawaii",
        "law_name": "Hawaii Uniform Information Practices Act (UIPA)",
        "citation": "Haw. Rev. Stat. § 92F-1 et seq.",
        "response_days": 10,
        "response_note": "10 business days",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Government deliberations"],
        "appeal_deadline_days": None,
        "appeal_body": "Office of Information Practices (OIP), then Circuit Court",
        "ahi_notes": None,
    },

    # ---- Idaho ----
    "ID": {
        "name": "Idaho",
        "law_name": "Idaho Public Records Act",
        "citation": "Idaho Code § 74-101 et seq.",
        "response_days": 3,
        "response_note": "3 business days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Illinois ----
    "IL": {
        "name": "Illinois",
        "law_name": "Illinois Freedom of Information Act (FOIA)",
        "citation": "5 ILCS 140/1 et seq.",
        "response_days": 5,
        "response_note": "5 business days; 5-day extension possible",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media / non-profit",
        "expedited_processing": True,
        "expedited_grounds": "Imminent threat to life or safety",
        "submission_methods": ["Written request (mail, email, or online)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Deliberative process"],
        "appeal_deadline_days": 60,
        "appeal_body": "Public Access Counselor (PAC), then Circuit Court",
        "ahi_notes": None,
    },

    # ---- Indiana ----
    "IN": {
        "name": "Indiana",
        "law_name": "Indiana Access to Public Records Act (APRA)",
        "citation": "Ind. Code § 5-14-3-1 et seq.",
        "response_days": 7,
        "response_note": "7 calendar days",
        "deadline_days": 7,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Public Access Counselor (PAC), then Circuit Court",
        "ahi_notes": None,
    },

    # ---- Iowa ----
    "IA": {
        "name": "Iowa",
        "law_name": "Iowa Open Records Law",
        "citation": "Iowa Code § 22.1 et seq.",
        "response_days": 20,
        "response_note": "20 business days",
        "deadline_days": 20,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Kansas ----
    "KS": {
        "name": "Kansas",
        "law_name": "Kansas Open Records Act (KORA)",
        "citation": "Kan. Stat. Ann. § 45-215 et seq.",
        "response_days": 3,
        "response_note": "3 business days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court; Attorney General",
        "ahi_notes": None,
    },

    # ---- Kentucky ----
    "KY": {
        "name": "Kentucky",
        "law_name": "Kentucky Open Records Act",
        "citation": "Ky. Rev. Stat. Ann. § 61.870 et seq.",
        "response_days": 3,
        "response_note": "3 business days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Preliminary agency action"],
        "appeal_deadline_days": 30,
        "appeal_body": "Attorney General, then Circuit Court",
        "ahi_notes": None,
    },

    # ---- Louisiana ----
    "LA": {
        "name": "Louisiana",
        "law_name": "Louisiana Public Records Law / Sunshine Law",
        "citation": "La. Rev. Stat. § 44:1 et seq.",
        "response_days": 3,
        "response_note": "3 business days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Maine ----
    "ME": {
        "name": "Maine",
        "law_name": "Maine Freedom of Access Act (FOAA)",
        "citation": "Me. Rev. Stat. tit. 1, § 401 et seq.",
        "response_days": 5,
        "response_note": "5 business days",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- Maryland ----
    "MD": {
        "name": "Maryland",
        "law_name": "Maryland Public Information Act (MPIA)",
        "citation": "Md. Code Ann., Gen. Prov. § 4-101 et seq.",
        "response_days": 30,
        "response_note": "30 calendar days (10 days to acknowledge)",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "State Public Information Act Compliance Board, then Circuit Court",
        "ahi_notes": None,
    },

    # ---- Massachusetts ----
    "MA": {
        "name": "Massachusetts",
        "law_name": "Massachusetts Public Records Act",
        "citation": "Mass. Gen. Laws ch. 66, § 10",
        "response_days": 10,
        "response_note": "10 business days",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media / non-profit",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Deliberative process"],
        "appeal_deadline_days": 90,
        "appeal_body": "Supervisor of Records, then Superior Court",
        "ahi_notes": None,
    },

    # ---- Michigan ----
    "MI": {
        "name": "Michigan",
        "law_name": "Michigan Freedom of Information Act (FOIA)",
        "citation": "Mich. Comp. Laws § 15.231 et seq.",
        "response_days": 5,
        "response_note": "5 business days",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / indigency",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": 180,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- Minnesota ----
    "MN": {
        "name": "Minnesota",
        "law_name": "Minnesota Government Data Practices Act (MGDPA)",
        "citation": "Minn. Stat. § 13.01 et seq.",
        "response_days": None,
        "response_note": "Prompt; no specific deadline — note this is a Data Practices Act, not a FOIA",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Private data on individuals", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Commissioner of Administration, then District Court",
        "ahi_notes": (
            "Minnesota uses a unique 'data practices' framework rather than a traditional FOIA. "
            "Data is classified as public, private, or confidential by statute."
        ),
    },

    # ---- Mississippi ----
    "MS": {
        "name": "Mississippi",
        "law_name": "Mississippi Public Records Act",
        "citation": "Miss. Code Ann. § 25-61-1 et seq.",
        "response_days": 7,
        "response_note": "7 calendar days",
        "deadline_days": 7,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- Missouri ----
    "MO": {
        "name": "Missouri",
        "law_name": "Missouri Sunshine Law",
        "citation": "Mo. Rev. Stat. § 610.010 et seq.",
        "response_days": 3,
        "response_note": "3 business days",
        "deadline_days": 3,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- Montana ----
    "MT": {
        "name": "Montana",
        "law_name": "Montana Public Records Act",
        "citation": "Mont. Code Ann. § 2-6-1001 et seq.",
        "response_days": None,
        "response_note": "No statutory deadline; must respond within a 'reasonable time'",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Nebraska ----
    "NE": {
        "name": "Nebraska",
        "law_name": "Nebraska Public Records Law",
        "citation": "Neb. Rev. Stat. § 84-712 et seq.",
        "response_days": 4,
        "response_note": "4 business days",
        "deadline_days": 4,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Nevada ----
    "NV": {
        "name": "Nevada",
        "law_name": "Nevada Open Records Act",
        "citation": "Nev. Rev. Stat. § 239.001 et seq.",
        "response_days": 5,
        "response_note": "5 business days",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- New Hampshire ----
    "NH": {
        "name": "New Hampshire",
        "law_name": "New Hampshire Right to Know Law",
        "citation": "N.H. Rev. Stat. Ann. § 91-A:1 et seq.",
        "response_days": 5,
        "response_note": "5 business days",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Attorney-client privilege"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- New Jersey ----
    "NJ": {
        "name": "New Jersey",
        "law_name": "New Jersey Open Public Records Act (OPRA)",
        "citation": "N.J. Stat. Ann. § 47:1A-1 et seq.",
        "response_days": 7,
        "response_note": "7 business days",
        "deadline_days": 7,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": False,
        "submission_methods": ["Written request (agency-specific OPRA form required)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": 45,
        "appeal_body": "Government Records Council (GRC) or Superior Court",
        "ahi_notes": None,
    },

    # ---- New Mexico ----
    "NM": {
        "name": "New Mexico",
        "law_name": "New Mexico Inspection of Public Records Act (IPRA)",
        "citation": "N.M. Stat. Ann. § 14-2-1 et seq.",
        "response_days": 15,
        "response_note": "15 calendar days",
        "deadline_days": 15,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court; Attorney General",
        "ahi_notes": None,
    },

    # ---- New York ----
    "NY": {
        "name": "New York",
        "law_name": "New York Freedom of Information Law (FOIL)",
        "citation": "N.Y. Pub. Off. Law § 84 et seq.",
        "response_days": 5,
        "response_note": "5 business days to acknowledge; 20 business days to respond",
        "deadline_days": 20,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media / non-profit",
        "expedited_processing": False,
        "submission_methods": ["Written request (online portal or mail)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets", "Deliberative process"],
        "appeal_deadline_days": 30,
        "appeal_body": "Agency Appeals Officer, then Article 78 proceeding in Supreme Court",
        "ahi_notes": None,
    },

    # ---- North Carolina ----
    "NC": {
        "name": "North Carolina",
        "law_name": "North Carolina Public Records Law",
        "citation": "N.C. Gen. Stat. § 132-1 et seq.",
        "response_days": None,
        "response_note": "No statutory deadline; must respond within a 'reasonable time'",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written or verbal request (written recommended)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- North Dakota ----
    "ND": {
        "name": "North Dakota",
        "law_name": "North Dakota Open Records Statute",
        "citation": "N.D. Cent. Code § 44-04-18 et seq.",
        "response_days": None,
        "response_note": "No statutory deadline; must respond within a 'reasonable time'",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Ohio ----
    "OH": {
        "name": "Ohio",
        "law_name": "Ohio Open Records Law",
        "citation": "Ohio Rev. Code Ann. § 149.43",
        "response_days": None,
        "response_note": "Prompt; no specific deadline",
        "deadline_days": 10,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written or verbal request (written recommended)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Court of Common Pleas",
        "ahi_notes": None,
    },

    # ---- Oklahoma ----
    "OK": {
        "name": "Oklahoma",
        "law_name": "Oklahoma Open Records Act",
        "citation": "Okla. Stat. tit. 51, § 24A.1 et seq.",
        "response_days": None,
        "response_note": "Prompt; no specific deadline",
        "deadline_days": 10,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },

    # ---- Oregon ----
    "OR": {
        "name": "Oregon",
        "law_name": "Oregon Public Records Law",
        "citation": "Or. Rev. Stat. § 192.311 et seq.",
        "response_days": None,
        "response_note": "Prompt; 2 business days for simple requests; 10 business days for complex",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": 60,
        "appeal_body": "Attorney General (state agencies) or District Attorney (local agencies)",
        "ahi_notes": None,
    },

    # ---- Pennsylvania ----
    "PA": {
        "name": "Pennsylvania",
        "law_name": "Pennsylvania Right-to-Know Law (RTKL)",
        "citation": "65 Pa. Cons. Stat. § 67.101 et seq.",
        "response_days": 5,
        "response_note": "5 business days; 30-day extension possible",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": False,
        "submission_methods": ["Written request (agency-specific form or letter)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets", "Deliberative process"],
        "appeal_deadline_days": 15,
        "appeal_body": "Agency Appeals Officer, then Office of Open Records (OOR)",
        "ahi_notes": None,
    },

    # ---- Rhode Island ----
    "RI": {
        "name": "Rhode Island",
        "law_name": "Rhode Island Access to Public Records Act (APRA)",
        "citation": "R.I. Gen. Laws § 38-2-1 et seq.",
        "response_days": 10,
        "response_note": "10 business days",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Attorney General, then Superior Court",
        "ahi_notes": None,
    },

    # ---- South Carolina ----
    "SC": {
        "name": "South Carolina",
        "law_name": "South Carolina Freedom of Information Act",
        "citation": "S.C. Code Ann. § 30-4-10 et seq.",
        "response_days": 15,
        "response_note": "15 calendar days",
        "deadline_days": 15,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- South Dakota ----
    "SD": {
        "name": "South Dakota",
        "law_name": "South Dakota Sunshine Law",
        "citation": "S.D. Codified Laws § 1-27-1 et seq.",
        "response_days": 15,
        "response_note": "15 business days",
        "deadline_days": 15,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- Tennessee ----
    "TN": {
        "name": "Tennessee",
        "law_name": "Tennessee Open Records Act",
        "citation": "Tenn. Code Ann. § 10-7-503 et seq.",
        "response_days": 7,
        "response_note": "7 business days",
        "deadline_days": 7,
        "deadline_type": "working",
        "residents_only": True,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Chancery Court",
        "ahi_notes": "Note: Requests are restricted to Tennessee residents.",
    },

    # ---- Texas ----
    "TX": {
        "name": "Texas",
        "law_name": "Texas Public Information Act (PIA)",
        "citation": "Tex. Gov. Code § 552.001 et seq.",
        "response_days": 10,
        "response_note": "10 business days",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": False,
        "submission_methods": ["Written request (mail, email, or online)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets", "Attorney General exceptions"],
        "appeal_deadline_days": None,
        "appeal_body": "Attorney General (mandatory pre-suit review), then District Court",
        "ahi_notes": None,
    },

    # ---- Utah ----
    "UT": {
        "name": "Utah",
        "law_name": "Utah Government Records Access and Management Act (GRAMA)",
        "citation": "Utah Code Ann. § 63G-2-101 et seq.",
        "response_days": 10,
        "response_note": "10 business days; 5-day expedited option available",
        "deadline_days": 10,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest / news media",
        "expedited_processing": True,
        "expedited_grounds": "Imminent threat to life or safety; time-sensitive public interest",
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets", "Classified information"],
        "appeal_deadline_days": 30,
        "appeal_body": "Agency Chief Administrative Officer, then State Records Committee",
        "ahi_notes": None,
    },

    # ---- Vermont ----
    "VT": {
        "name": "Vermont",
        "law_name": "Vermont Public Records Law",
        "citation": "Vt. Stat. Ann. tit. 1, § 315 et seq.",
        "response_days": 2,
        "response_note": "2 business days (one of the fastest in the nation)",
        "deadline_days": 2,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court",
        "ahi_notes": None,
    },

    # ---- Virginia ----
    "VA": {
        "name": "Virginia",
        "law_name": "Virginia Freedom of Information Act (VFOIA)",
        "citation": "Va. Code Ann. § 2.2-3700 et seq.",
        "response_days": 5,
        "response_note": "5 business days",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": True,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets", "Attorney-client privilege"],
        "appeal_deadline_days": None,
        "appeal_body": "FOIA Council (advisory), then Circuit Court",
        "ahi_notes": "Note: Requests are restricted to Virginia citizens.",
    },

    # ---- Washington ----
    "WA": {
        "name": "Washington",
        "law_name": "Washington Public Records Act (PRA)",
        "citation": "Wash. Rev. Code § 42.56.001 et seq.",
        "response_days": None,
        "response_note": "Prompt; 5 business days to acknowledge",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Superior Court (significant statutory penalties for non-compliance)",
        "ahi_notes": None,
    },

    # ---- West Virginia ----
    "WV": {
        "name": "West Virginia",
        "law_name": "West Virginia Freedom of Information Act",
        "citation": "W. Va. Code § 29B-1-1 et seq.",
        "response_days": 5,
        "response_note": "5 business days",
        "deadline_days": 5,
        "deadline_type": "working",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court",
        "ahi_notes": None,
    },

    # ---- Wisconsin ----
    "WI": {
        "name": "Wisconsin",
        "law_name": "Wisconsin Open Records Law",
        "citation": "Wis. Stat. § 19.31 et seq.",
        "response_days": None,
        "response_note": "Prompt; no specific deadline",
        "deadline_days": 10,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written or verbal request (written recommended)"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "Circuit Court; District Attorney (local agencies)",
        "ahi_notes": None,
    },

    # ---- Wyoming ----
    "WY": {
        "name": "Wyoming",
        "law_name": "Wyoming Public Records Act",
        "citation": "Wyo. Stat. Ann. § 16-4-201 et seq.",
        "response_days": None,
        "response_note": "No statutory deadline; must respond within a 'reasonable time'",
        "deadline_days": 30,
        "deadline_type": "calendar",
        "residents_only": False,
        "fee_waiver_available": True,
        "fee_waiver_grounds": "Public interest",
        "expedited_processing": False,
        "submission_methods": ["Written request"],
        "portal_url": None,
        "common_exemptions": ["Personal privacy", "Law enforcement", "Trade secrets"],
        "appeal_deadline_days": None,
        "appeal_body": "District Court",
        "ahi_notes": None,
    },
}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def calculate_foia_deadline(submitted_ts: float, jurisdiction_code: str) -> datetime:
    """
    Calculate the legal response deadline for a FOIA request.

    For 'calendar' deadlines: adds deadline_days calendar days to submitted_ts.
    For 'working' deadlines: adds deadline_days working days (Mon–Fri), skipping
    Saturday and Sunday. No external libraries — pure Python datetime only.

    Returns a timezone-aware datetime in UTC.
    """
    j = JURISDICTIONS.get(jurisdiction_code.upper())
    if not j:
        deadline_days = 20
        deadline_type = "working"
    else:
        deadline_days = j.get("deadline_days", 20)
        deadline_type = j.get("deadline_type", "working")

    start = datetime.fromtimestamp(submitted_ts, tz=timezone.utc)

    if deadline_type == "calendar":
        return start + timedelta(days=deadline_days)

    # working days — skip Saturday (5) and Sunday (6)
    current = start
    days_counted = 0
    while days_counted < deadline_days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon=0 … Fri=4
            days_counted += 1
    return current


def get_jurisdiction(code: str) -> Optional[JurisdictionInfo]:
    """
    Return jurisdiction metadata for a two-letter state code, 'DC', or 'FEDERAL'.
    Returns None if the code is not recognised.
    """
    return JURISDICTIONS.get(code.upper())


def list_jurisdiction_codes() -> list[str]:
    """Return all recognised jurisdiction codes, sorted."""
    return sorted(JURISDICTIONS.keys())


def format_jurisdiction_summary(code: str) -> str:
    """
    Return a human-readable Markdown summary of a jurisdiction's public
    records law, suitable for display in a Matrix chat message.
    """
    j = get_jurisdiction(code)
    if not j:
        return f"Unknown jurisdiction code: `{code}`"

    lines = [
        f"## {j['name']} — Public Records Law",
        "",
        f"**Law:** {j['law_name']}",
        f"**Citation:** `{j['citation']}`",
        f"**Response Deadline:** {j['response_note']}",
        f"**Residents Only:** {'Yes ⚠️' if j['residents_only'] else 'No'}",
        f"**Fee Waiver Available:** {'Yes' if j['fee_waiver_available'] else 'No'}",
    ]
    if j.get("fee_waiver_grounds"):
        lines.append(f"**Fee Waiver Grounds:** {j['fee_waiver_grounds']}")
    if j.get("expedited_processing"):
        lines.append(f"**Expedited Processing:** Yes — {j.get('expedited_grounds', 'see statute')}")
    if j.get("submission_methods"):
        lines.append(f"**Submission Methods:** {', '.join(j['submission_methods'])}")
    if j.get("portal_url"):
        lines.append(f"**Online Portal:** {j['portal_url']}")
    if j.get("appeal_body"):
        lines.append(f"**Appeal Body:** {j['appeal_body']}")
    if j.get("common_exemptions"):
        lines.append("")
        lines.append("**Common Exemptions:**")
        for ex in j["common_exemptions"]:
            lines.append(f"- {ex}")
    if j.get("ahi_notes"):
        lines.append("")
        lines.append(f"**AHI/Neurowarfare Notes:** {j['ahi_notes']}")

    return "\n".join(lines)


def format_federal_agencies_summary() -> str:
    """
    Return a human-readable Markdown list of federal agencies relevant to
    AHI/Neurowarfare FOIA requests.
    """
    lines = [
        "## Federal Agencies — AHI / Neurowarfare FOIA Targets",
        "",
        "The following federal agencies are most likely to hold records relevant "
        "to Anomalous Health Incidents (AHIs), Havana Syndrome, and directed energy weapons.",
        "",
    ]
    for agency in FEDERAL_AHI_AGENCIES:
        lines += [
            f"### {agency['name']} ({agency['abbreviation']})",
            f"- **FOIA Email:** `{agency['foia_email']}`",
            f"- **Portal:** {agency['foia_portal']}",
            f"- **Notes:** {agency['notes']}",
            "",
        ]
    return "\n".join(lines)
