import streamlit as st
import io
import re
import uuid
import hashlib
from datetime import datetime
from dateutil.parser import parse as date_parse
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials

scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
try:
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    gc = gspread.authorize(creds)
    SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.sheet1
except Exception as e:
    st.error(f"Google Sheets Auth Error: {e}")
    st.stop()

def extract_explicit_table(page, table_bbox, column_x_coords):
    cropped_page = page.within_bbox(table_bbox)
    words = cropped_page.extract_words(keep_blank_chars=True, use_text_flow=True, y_tolerance=3, x_tolerance=3)
    row_dict = {}
    for word in words:
        row_key = round(word['top'] / 4)
        row_dict.setdefault(row_key, []).append(word)

    extracted_rows = []
    for row_top in sorted(row_dict.keys()):
        words = sorted(row_dict[row_top], key=lambda w: w["x0"])
        cols = [""] * (len(column_x_coords) - 1)
        for word in words:
            x = word["x0"]
            for i, (x0, x1) in enumerate(zip(column_x_coords[:-1], column_x_coords[1:])):
                if x0 <= x <= x1:
                    cols[i] += " " + word["text"]
                    break
        cols = [c.strip() for c in cols if c]
        if len(cols) == 3:
            extracted_rows.append({
                "Type of charge": cols[0],
                "Calculation": cols[1],
                "Amount": cols[2]
            })
    return extracted_rows

def extract_charges_from_pdf(file_io):
    charges = []
    with pdfplumber.open(file_io) as pdf:
        page = pdf.pages[0]
        width, height = page.width, page.height
        # Adjust coordinates as per real PDF structure
        table_bbox = (30, 200, width-30, 530)
        column_x_coords = [30, 180, 440, width-30]

        extracted_rows = extract_explicit_table(page, table_bbox, column_x_coords)
        for row in extracted_rows:
            amt_clean = row["Amount"].replace(',', '').replace('−', '-').replace('(', '-').replace(')', '')
            try:
                charges.append({
                    "Charge_Type": row["Type of charge"],
                    "Calculation": row["Calculation"],
                    "Amount": float(amt_clean)
                })
            except ValueError:
                continue
    return charges

def extract_total_use_from_pdf(file_io):
    with pdfplumber.open(file_io) as pdf:
        page = pdf.pages[1]
        text = page.extract_text() or ""
        match = re.search(r"\b(\d{4,6})\s+kWh\b", text)
        return match.group(1) if match else ""

def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Account_Number": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = ''.join(p.extract_text() or "" for p in pdf.pages)
        acct_match = re.search(r"Account\s*#?:?\s*(\d{4}\s*\d{4}\s*\d{3})", text, re.I)
        if acct_match:
            metadata["Account_Number"] = acct_match.group(1).replace(" ", "")
        date_match = re.search(r"Bill\s*Issue\s*date:\s*(.+)", text, re.I)
        if date_match:
            try:
                metadata["Bill_Month_Year"] = date_parse(date_match.group(1), fuzzy=True).strftime("%m-%Y")
            except:
                pass
    return metadata

def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    total_use = extract_total_use_from_pdf(file_io)

    consolidated = {}
    for c in charges:
        ct = c["Charge_Type"].strip()
        amt = c["Amount"]
        calc = c.get("Calculation", "")
        consolidated[ct] = {"Amount": amt, "Calculation": calc}

    account_number = metadata.get("Account_Number", "")
    user_id = st.session_state.get('customer_ids', {}).get(account_number, str(uuid.uuid4()))
    st.session_state.setdefault('customer_ids',{})[account_number] = user_id

    existing = worksheet.get_all_records()
    bill_id = next((r["Bill_ID"] for r in existing if r.get("Bill_Hash") == bill_hash), str(uuid.uuid4()))

    output_row = {"User_ID": user_id, "Bill_ID": bill_id, "Bill_Month_Year": metadata.get("Bill_Month_Year"), "Bill_Hash": bill_hash, "Total Use": total_use}
    for ct, data in consolidated.items():
        output_row[f"{ct} Amount"] = data["Amount"]
        output_row[f"{ct} Calculation"] = data["Calculation"]
    return output_row

def append_row_to_sheet(row_dict):
    headers = worksheet.row_values(1)
    new_cols = [col for col in row_dict if col not in headers]
    if new_cols:
        headers.extend(new_cols)
        worksheet.update("A1", [headers])
    row = [row_dict.get(h,"") for h in headers]
    worksheet.append_row(row)

st.title("Delmarva BillWatch Uploader")
uploaded_file = st.file_uploader("Upload PDF Bill:", type=["pdf"])

if uploaded_file:
    try:
        file_data = uploaded_file.read()
        file_io = io.BytesIO(file_data)
        output_row = process_pdf(file_io)
        append_row_to_sheet(output_row)
        st.success("✅ PDF Uploaded Successfully. Check Google Sheets.")
        st.write(output_row)
    except Exception as e:
        st.error(f"Error in processing: {e}")
