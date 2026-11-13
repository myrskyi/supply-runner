"""Specimen PDFs for the demo: what the coordinator attaches, and what the agent sends back. All fictional and watermarked."""

import uuid
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from . import config

FONTS = ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
FONT = next((f for f in FONTS if Path(f).exists()), FONTS[-1])
OUT = config.DATA / "documents"
ORG = {
    "name": "Charitable Foundation \"Open Road Aid\" (fictional)",
    "address": "vul. Sumska 1, Kharkiv, Ukraine",
    "code": "EDRPOU 00000000",
}
PACK_FILES = ["End_user_letter.pdf", "Humanitarian_aid_declaration.pdf", "Export_licence_copy.pdf", "Driver_power_of_attorney_and_insurance.pdf", "Payment_confirmation.pdf"]
WATERMARK = "SPECIMEN · DEMO DOCUMENT · NOT VALID"


def render(filename: str, title: str, subtitle: str, sections: list[tuple[str, list[str]]], signature: str | None = None) -> Path:
    pdf = FPDF()
    pdf.add_font("U", "", FONT)
    pdf.add_page()
    pdf.set_font("U", size=40)
    pdf.set_text_color(235, 235, 235)
    with pdf.rotation(35, x=105, y=150):
        pdf.text(18, 160, "SPECIMEN")
    pdf.set_text_color(17, 24, 39)
    pdf.set_font("U", size=8)
    pdf.cell(0, 5, WATERMARK, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("U", size=17)
    pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("U", size=10)
    pdf.set_text_color(107, 114, 128)
    pdf.multi_cell(0, 5, subtitle, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(17, 24, 39)
    for heading, lines in sections:
        pdf.ln(4)
        pdf.set_font("U", size=11)
        pdf.set_fill_color(243, 244, 246)
        pdf.cell(0, 7, heading, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("U", size=10)
        for line in lines:
            pdf.multi_cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")
    if signature:
        pdf.ln(12)
        pdf.cell(0, 6, "______________________________", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, signature, new_x="LMARGIN", new_y="NEXT")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / filename
    pdf.output(str(path))
    return path


def coordinator_pack(order_ref: str = "SR-DEMO") -> list[Path]:
    today = datetime.now().strftime("%d.%m.%Y")
    goods = "2 × thermal imaging monocular (civilian hunting model), 20 × first aid kit with tourniquet"
    return [
        render("End_user_letter.pdf", "End-user statement", f"Ref. {order_ref} · {today} · structure follows common EU end-user statement templates", [
            ("End user", [ORG["name"], ORG["address"], ORG["code"]]),
            ("Goods", [goods]),
            ("End use", ["Humanitarian use by volunteer rescue teams in Kharkiv region.", "The goods will not be re-exported, resold or transferred to third parties.", "The goods will not be used for weapons of mass destruction or in breach of sanctions."]),
        ], "Head of the foundation (fictional), signature and stamp"),
        render("Humanitarian_aid_declaration.pdf", "Declaration of humanitarian aid", f"Ref. {order_ref} · {today} · fields as in the Ukrainian e-declaration of humanitarian aid (Resolution No. 953)", [
            ("Recipient", [ORG["name"], ORG["code"], ORG["address"]]),
            ("Donor / sender", ["Purchased with donor funds, carried by the recipient's own vehicle"]),
            ("Goods", [goods, "Total weight approx. 25 kg, 3 packages"]),
            ("Purpose", ["Free distribution to volunteer rescue teams, not for sale"]),
        ], "Authorised person of the recipient (fictional)"),
        render("Export_licence_copy.pdf", "Individual export authorisation for dual-use items (copy)", f"Specimen copy · layout follows Annex IIa of Regulation (EU) 2021/821 · not issued by any authority", [
            ("1. Exporter", [ORG["name"] + ", acting through its Czech purchasing agent"]),
            ("2. Authorisation No.", ["CZ-DEMO-0000-2026"]),
            ("3. Consignee and end user", [ORG["name"], ORG["address"]]),
            ("4. Items", ["Thermal imaging monocular, 2 pcs", "Control list entry: to be confirmed for the exact model (6A003 if listed)"]),
            ("5. Validity", [f"Issued {today}, valid 12 months"]),
        ]),
        render("Driver_power_of_attorney_and_insurance.pdf", "Power of attorney for the driver and vehicle insurance", f"Ref. {order_ref} · {today}", [
            ("Power of attorney", [f"{ORG['name']} authorises its driver (fictional, passport No. XX000000) to collect the goods of order {order_ref}, sign the delivery note and carry the goods to Kharkiv."]),
            ("Vehicle", ["Van, registration AX 0000 XX (fictional)"]),
            ("Motor insurance (Green Card system)", ["Insurer: Demo Insurance (fictional)", "Card No. UA/000/DEMO, valid for CZ, PL, UA until 31.12.2026"]),
        ], "Head of the foundation (fictional)"),
        render("Payment_confirmation.pdf", "Payment confirmation", f"Bank transfer receipt · {today}", [
            ("Payer", [ORG["name"], "IBAN UA00 0000 0000 0000 0000 0000 000 (fictional)"]),
            ("Payee", [f"Supplier of order {order_ref}"]),
            ("Amount", ["40 000 CZK"]),
            ("Reference", [f"Order {order_ref}, 2 × thermal imaging monocular"]),
            ("Status", ["Executed"]),
        ]),
    ]


def purchase_order(order: dict) -> Path:
    return render(f"Purchase_order_{order.get('number') or uuid.uuid4().hex[:8]}.pdf", "Purchase order", f"{datetime.now().strftime('%d.%m.%Y')} · prepared by Supply Runner for the coordinator", [
        ("Buyer", [ORG["name"], ORG["address"]]),
        ("Supplier", [order["supplier_name"]]),
        ("Items", [order["items"], f"Unit price {order.get('unit_price')} {order.get('currency')}, total {order.get('total')} {order.get('currency')}"]),
        ("Pickup", [f"In {order.get('lead_time_days')} day(s), own van, near Prague"]),
        ("Before pickup we need from you", [f"• {d['name']}" for d in order["documents"]]),
    ])


def order_confirmation(order: dict) -> Path:
    return render(f"Order_confirmation_{order['number']}.pdf", f"Order {order['number']} confirmed", f"{datetime.now().strftime('%d.%m.%Y')} · Supply Runner", [
        ("Supplier", [order["supplier_name"]]),
        ("Items", [order["items"], f"{order.get('total')} {order.get('currency')} paid"]),
        ("Pickup", [order.get("pickup") or "date to be agreed"]),
        ("Documents on file", [f"✓ {d['name']}" for d in order["documents"]]),
    ])


if __name__ == "__main__":
    for path in coordinator_pack():
        print(path)
