"""Task 2: generate synthetic consular case-email PDFs + SOP docs, then upload.

All data is fabricated. Facts (case type, location, agencies, flags) are woven into
the email prose so downstream ai_query extraction has genuine evidence to work from.

Run: python -m pipeline.01_generate_data          # generate + upload
     python -m pipeline.01_generate_data --local  # generate only
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta

from fpdf import FPDF

from pipeline import config

SEED = 20260917
random.seed(SEED)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
EMAIL_DIR = os.path.join(DATA, "emails")
SOP_DIR = os.path.join(DATA, "sop")

N_CASES = 120
MAILBOX = "ccms.cases@mfa.gov.sg"

# ---- Controlled vocabulary -------------------------------------------------
# country -> (cities, mission unit, mission mailbox)
COUNTRIES = {
    "Thailand": (["Bangkok", "Phuket", "Chiang Mai"], "Bangkok", "bangkok.mission@mfa.gov.sg"),
    "China": (["Guangzhou", "Shanghai", "Chengdu"], "Guangzhou", "guangzhou.mission@mfa.gov.sg"),
    "Malaysia": (["Kuala Lumpur", "Johor Bahru", "Penang"], "Kuala Lumpur", "kl.mission@mfa.gov.sg"),
    "Indonesia": (["Jakarta", "Bali", "Batam"], "Jakarta", "jakarta.mission@mfa.gov.sg"),
    "United Kingdom": (["London", "Manchester"], "London", "london.mission@mfa.gov.sg"),
    "Australia": (["Sydney", "Melbourne", "Perth"], "Canberra", "canberra.mission@mfa.gov.sg"),
    "Japan": (["Tokyo", "Osaka"], "Tokyo", "tokyo.mission@mfa.gov.sg"),
    "India": (["New Delhi", "Mumbai", "Chennai"], "New Delhi", "delhi.mission@mfa.gov.sg"),
}

CASE_TYPES = [
    "Arrest & Detention",
    "Serious Illness/Death",
    "Victim of Crime",
    "Lost/Stolen Document",
    "Evacuation",
    "Scam",
]

HQ_UNITS = ["CRC", "CON/OPS"]
AGENCIES = ["ICA", "SPF", "MHA", "MOH"]

FIRST = ["Wei Ming", "Siti", "Arjun", "Rachel", "Kai", "Nurul", "Priya", "Jun Jie",
         "Farah", "Daniel", "Mei Ling", "Ravi", "Hui Xin", "Aisyah", "Marcus", "Divya"]
SUR = ["Tan", "Lim", "Kumar", "Rahman", "Ng", "Wong", "Pillai", "Goh", "Ismail",
       "Chua", "Nair", "Lee", "Abdullah", "Rajaratnam", "Ong", "Fernandez"]
OFFICERS = ["Officer Lena Koh", "Officer Samuel Teo", "Officer Farhan Rashid",
            "Officer Grace Lim", "Officer Nabil Hassan", "Officer Cheryl Ang"]
MPS = ["Mr Tan Cheng Leong", "Ms Rania Osman", "Dr Vijay Menon", "Ms Clara Sim"]
CONSTITUENCIES = ["Tampines GRC", "Jurong GRC", "Marine Parade GRC", "Sengkang GRC"]


def nric() -> str:
    return random.choice("STFG") + "".join(str(random.randint(0, 9)) for _ in range(7)) + random.choice("ABCDEFGHJZ")


def person() -> str:
    return f"{random.choice(FIRST)} {random.choice(SUR)}"


def fmt(dt: datetime) -> str:
    return dt.strftime("%d %b %Y, %H:%M")


# ---- Scenario builders: return (title, facts, messages) --------------------
def scenario(ctype, country, cities, mission, mission_mbox, subject_name, nric_str,
             officer, start: datetime):
    city = random.choice(cities)
    agencies: list[str] = []
    flags: list[str] = []
    d0, d1, d2 = start, start + timedelta(days=random.randint(1, 4)), start + timedelta(days=random.randint(5, 20))
    closed = random.random() < 0.72
    status = "Closed" if closed else "Pending"

    if ctype == "Arrest & Detention":
        agencies = random.sample(["ICA", "MHA"], k=random.choice([0, 1, 1, 2]))
        if random.random() < 0.4:
            flags.append("language barrier")
        if random.random() < 0.3:
            flags.append("MP/POH escalation")
        body0 = (f"We were notified that {subject_name} (NRIC {nric_str}), a Singapore Citizen, "
                 f"was arrested by local police in {city}, {country} on {d0.strftime('%d %b %Y')} "
                 f"in connection with an alleged immigration offence. The next-of-kin contacted CRC "
                 f"requesting consular assistance and confirmation of welfare.")
        body1 = (f"{mission} consular team visited the detention facility in {city}. "
                 f"{subject_name} is in stable condition. We are arranging a list of local lawyers "
                 f"and have notified the family. " +
                 ("A translator was engaged as the detainee has limited local language ability. "
                  if "language barrier" in flags else "") +
                 ("We have coordinated with ICA on travel-document validity. " if "ICA" in agencies else ""))
        body2 = (f"Consular access confirmed and lawyer engaged. Case being monitored pending court date. "
                 + ("Closed as detainee released on bail and returned to Singapore." if closed else "Remains pending trial."))
        assistance_req = "Confirm welfare, arrange legal representation, notify next-of-kin."
        advice = "Provided list of local lawyers; advised family on consular limitations."

    elif ctype == "Serious Illness/Death":
        agencies = random.sample(["MOH", "ICA"], k=random.choice([0, 1, 1]))
        if random.random() < 0.5:
            flags.append("welfare concern")
        body0 = (f"CRC received a report that {subject_name} (NRIC {nric_str}) was hospitalised in "
                 f"critical condition in {city}, {country} following a road traffic accident on "
                 f"{d0.strftime('%d %b %Y')}. Family in Singapore is distressed and seeking updates.")
        body1 = (f"{mission} liaised with the hospital in {city}. The attending doctor advised the "
                 f"patient is in intensive care. " +
                 ("We flagged a welfare concern and are supporting the family with regular updates. "
                  if "welfare concern" in flags else "") +
                 ("MOH was consulted on medical evacuation options. " if "MOH" in agencies else ""))
        body2 = ("Patient stabilised and medically repatriated to Singapore. Case closed."
                 if closed else "Patient remains in ICU; case pending.")
        assistance_req = "Hospital liaison, family updates, repatriation options."
        advice = "Advised family on medical evacuation insurance and repatriation logistics."

    elif ctype == "Victim of Crime":
        agencies = random.sample(["SPF", "ICA"], k=random.choice([0, 1, 1, 2]))
        if random.random() < 0.3:
            flags.append("welfare concern")
        body0 = (f"{subject_name} (NRIC {nric_str}) reported being robbed at knife-point in {city}, "
                 f"{country} on {d0.strftime('%d %b %Y')}. Passport and belongings were stolen. "
                 f"Traveller is shaken and requesting assistance.")
        body1 = (f"{mission} assisted the victim in lodging a police report locally. " +
                 ("SPF was consulted regarding a linked report filed in Singapore. " if "SPF" in agencies else "") +
                 ("ICA advised on emergency Document of Identity issuance. " if "ICA" in agencies else ""))
        body2 = ("Emergency travel document issued; traveller returned safely. Case closed."
                 if closed else "Awaiting local police outcome; case pending.")
        assistance_req = "Police report assistance, emergency travel document, welfare support."
        advice = "Advised on replacing stolen documents and travel insurance claim."

    elif ctype == "Lost/Stolen Document":
        agencies = ["ICA"] if random.random() < 0.7 else []
        body0 = (f"{subject_name} (NRIC {nric_str}) lost their Singapore passport in {city}, {country} "
                 f"on {d0.strftime('%d %b %Y')} and is due to fly home in three days.")
        body1 = (f"{mission} verified identity and " +
                 ("coordinated with ICA to issue a Document of Identity. " if "ICA" in agencies else
                  "processed an emergency Document of Identity. "))
        body2 = ("Document of Identity collected; traveller departed on schedule. Case closed."
                 if closed else "Pending identity verification; case open.")
        assistance_req = "Emergency travel document issuance."
        advice = "Advised traveller on safeguarding documents and reporting to local police."

    elif ctype == "Evacuation":
        agencies = random.sample(["MHA", "MOH", "ICA"], k=random.choice([1, 2]))
        flags.append("welfare concern")
        if random.random() < 0.4:
            flags.append("MP/POH escalation")
        body0 = (f"Following severe flooding in {city}, {country}, {subject_name} (NRIC {nric_str}) "
                 f"and several other Singaporeans are stranded as of {d0.strftime('%d %b %Y')}. "
                 f"CRC is coordinating a possible assisted departure.")
        body1 = (f"{mission} established contact with the stranded group and is arranging safe "
                 f"transport to the capital. Inter-agency coordination underway with "
                 f"{', '.join(agencies) if agencies else 'local authorities'}.")
        body2 = ("All Singaporeans evacuated safely and accounted for. Case closed."
                 if closed else "Evacuation logistics ongoing; case pending.")
        assistance_req = "Locate and evacuate stranded citizens; coordinate assisted departure."
        advice = "Advised registrants via LRS to shelter in place until transport confirmed."

    else:  # Scam
        agencies = ["SPF"] if random.random() < 0.8 else []
        flags.append("scam")
        if random.random() < 0.35:
            flags.append("language barrier")
        body0 = (f"{subject_name} (NRIC {nric_str}) reported being lured to {city}, {country} by a "
                 f"fake job offer and coerced into a scam operation. Family alerted CRC on "
                 f"{d0.strftime('%d %b %Y')} fearing the victim is being held against their will.")
        body1 = (f"{mission} is working with local authorities to locate the victim. " +
                 ("SPF Anti-Scam Centre was engaged to trace linked transactions in Singapore. "
                  if "SPF" in agencies else "") +
                 ("A translator supported communication with the victim. " if "language barrier" in flags else ""))
        body2 = ("Victim located and safely repatriated; SPF pursuing the syndicate. Case closed."
                 if closed else "Victim's location still being confirmed; case pending.")
        assistance_req = "Locate victim, coordinate rescue/repatriation, anti-scam referral."
        advice = "Advised family on scam-victim support channels and SPF reporting."

    title = f"{ctype} — {subject_name} in {city}, {country}"
    facts = dict(case_type=ctype, country=country, city=city, mission=mission, agencies=agencies,
                 flags=flags, status=status, subject_name=subject_name, nric=nric_str,
                 assistance_req=assistance_req, advice=advice,
                 created=d0.strftime("%Y-%m-%d"), resolution_days=(d2 - d0).days if closed else None)
    handler_unit = random.choice(HQ_UNITS)
    messages = [
        dict(sender=f"CRC Duty Officer <{MAILBOX}>", to=f"{officer} <{mission_mbox}>",
             date=fmt(d0), body=body0),
        dict(sender=f"{officer} <{mission_mbox}>", to=f"CRC <{MAILBOX}>",
             date=fmt(d1), body=body1),
        dict(sender=f"CRC <{MAILBOX}>", to=f"{officer} <{mission_mbox}>",
             date=fmt(d2), body=body2),
    ]
    if "MP/POH escalation" in flags:
        mp = random.choice(MPS)
        constituency = random.choice(CONSTITUENCIES)
        messages.insert(2, dict(
            sender=f"MP Liaison <mp.liaison@mfa.gov.sg>", to=f"CRC <{MAILBOX}>", date=fmt(d1 + timedelta(hours=6)),
            body=(f"This case has been escalated by {mp} ({constituency}) on behalf of the family. "
                  f"Please provide a status update for the MP's office.")))
        facts["mp_name"] = mp
        facts["constituency"] = constituency
        facts["covering_mp"] = mp
    return title, facts, handler_unit, messages


def _line(pdf, text, style="", size=10):
    pdf.set_font("Helvetica", style, size)
    pdf.multi_cell(pdf.epw, 5, _ascii(text), new_x="LMARGIN", new_y="NEXT")


def write_thread_pdf(path, case_ref, title, messages):
    pdf = FPDF()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    _line(pdf, "CCMS - Case Email Thread (SYNTHETIC / TEST DATA)", "B", 11)
    _line(pdf, f"Subject: Re: [{case_ref}] {title}", "B", 12)
    _line(pdf, f"Cc: {MAILBOX}", "", 9)
    pdf.ln(2)
    for m in messages:
        _line(pdf, f"From: {m['sender']}", "B", 9)
        _line(pdf, f"To: {m['to']}", "B", 9)
        _line(pdf, f"Date: {m['date']}", "B", 9)
        _line(pdf, m["body"], "", 10)
        pdf.ln(3)
    pdf.output(path)


def _ascii(s: str) -> str:
    # fpdf core fonts are latin-1; replace fancy dashes/quotes.
    return (s.replace("—", "-").replace("–", "-").replace("’", "'")
            .replace("“", '"').replace("”", '"').encode("latin-1", "replace").decode("latin-1"))


SOP_TEXT = {
    "Arrest & Detention": [
        "Confirm the citizen's identity and place of detention via the local mission.",
        "Arrange consular access and verify welfare within 48 hours.",
        "Provide the family a list of local lawyers; do not recommend a specific one.",
        "Coordinate with ICA on travel-document validity where relevant.",
        "Log all contact and monitor until court outcome or release.",
    ],
    "Serious Illness/Death": [
        "Establish hospital liaison through the mission and obtain the attending doctor's assessment.",
        "Provide the next-of-kin regular, factual updates.",
        "Advise on medical evacuation and repatriation, consulting MOH where needed.",
        "For a death, assist with local formalities and repatriation of remains.",
    ],
    "Victim of Crime": [
        "Assist the victim to lodge a local police report.",
        "Advise on replacing stolen documents; coordinate ICA for emergency travel documents.",
        "Refer to SPF where a linked Singapore report exists.",
        "Offer welfare support and monitor until the traveller is safe.",
    ],
    "Lost/Stolen Document": [
        "Verify the applicant's identity against records.",
        "Issue an emergency Document of Identity via ICA coordination.",
        "Advise the traveller to lodge a local police report.",
    ],
    "Evacuation": [
        "Activate LRS to identify and contact registrants in the affected area.",
        "Advise citizens to shelter in place until assisted departure is confirmed.",
        "Coordinate inter-agency (MHA, MOH, ICA) transport and accounting of all citizens.",
        "Confirm every Singaporean is safe and accounted for before closing.",
    ],
    "Scam": [
        "Treat as urgent where the victim may be held against their will.",
        "Work with local authorities to locate the victim.",
        "Engage SPF Anti-Scam Centre to trace linked transactions.",
        "Coordinate rescue and repatriation; refer family to scam-victim support.",
    ],
}


def write_sop_pdf(path, ctype, steps):
    pdf = FPDF()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    _line(pdf, f"Consular SOP - {ctype}", "B", 13)
    _line(pdf, "Standard operating procedure (synthetic reference document).", "", 10)
    pdf.ln(2)
    for i, s in enumerate(steps, 1):
        _line(pdf, f"{i}. {s}", "", 10)
    pdf.output(path)


def generate():
    os.makedirs(EMAIL_DIR, exist_ok=True)
    os.makedirs(SOP_DIR, exist_ok=True)
    manifest = []
    country_items = list(COUNTRIES.items())
    for i in range(N_CASES):
        ctype = CASE_TYPES[i % len(CASE_TYPES)]
        country, (cities, mission, mbox) = country_items[i % len(country_items)]
        start = datetime(2024, 1, 1) + timedelta(days=random.randint(0, 900),
                                                  hours=random.randint(8, 18))
        year = start.year
        case_ref = f"SGCON/{year}/{10000 + i}"
        mfa_ref = f"MFA-{year}-{20000 + i}"
        subject_name = person()
        officer = random.choice(OFFICERS)
        title, facts, handler_unit, messages = scenario(
            ctype, country, cities, mission, mbox, subject_name, nric(), officer, start)
        path = os.path.join(EMAIL_DIR, f"{case_ref.replace('/', '_')}.pdf")
        write_thread_pdf(path, case_ref, title, messages)
        facts.update(case_ref=case_ref, mfa_ref=mfa_ref, handler_unit=handler_unit,
                     officer=officer, file=os.path.basename(path))
        manifest.append(facts)

    with open(os.path.join(DATA, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    for ctype, steps in SOP_TEXT.items():
        write_sop_pdf(os.path.join(SOP_DIR, f"SOP_{ctype.replace('/', '_').replace(' ', '_')}.pdf"),
                      ctype, steps)

    print(f"generated {len(manifest)} case PDFs in {EMAIL_DIR}")
    print(f"generated {len(SOP_TEXT)} SOP PDFs in {SOP_DIR}")
    return manifest


def upload():
    def cp(src, dst):
        subprocess.run(["databricks", "fs", "cp", "-r", "--overwrite", src, dst,
                        "--profile", config.PROFILE], check=True)
    cp(EMAIL_DIR + "/", f"dbfs:{config.VOL_EMAILS}/")
    cp(SOP_DIR + "/", f"dbfs:{config.VOL_SOP}/")
    print("uploaded to volumes")


if __name__ == "__main__":
    generate()
    if "--local" not in sys.argv:
        upload()
