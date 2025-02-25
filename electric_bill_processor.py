import streamlit as st
import io
import uuid
import hashlib
from dateutil.parser import parse as date_parse
import pdfplumber
import pytesseract
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials

# --- Google Sheets Setup ---
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
worksheet = spreadsheet.sheet1

# --- Helper: Standardize Charge Type Names ---
def standardize_charge_type(charge_type):
    charge_type = charge_type.strip().lower()
    charge_mapping = {
        "distribution charge": "Distribution Charge",
        "transmission": "Transmission Charge",
        "customer charge": "Customer Charge",
        "low income": "Low Income Charge",
        "green energy": "Green Energy Fund",
        "renewable compliance": "Renewable Compliance Charge",
        "wind & solar": "Renewable Compliance Wind & Solar",
        "qualified fuel": "Renewable Compliance Fuel Cells",
        "energy efficiency": "Energy Efficiency Surcharge",
        "standard offer": "Standard Offer Service Charge",
        "improvement charge": "Distribution System Improvement Charge",
        "finance charges": "Finance Charges",
        "myp adjustment": "MYP Adjustment",
        "environmental surcharge": "Environmental Surcharge",
        "administrative credit": "Administrative Credit",
        "universal service": "Universal Service Program",
        "franchise tax": "MD Franchise Tax",
        "adjustment": "Adjustment",
    }
    for key, value in charge_mapping.items():
        if key in charge_type:
            return value
    return " ".join(w.capitalize() for w in charge_type.split())  # Fallback

# --- Helper: Extract Text ---
def extract_text_from_pdf(file_bytes, use_ocr=False):
    text = ""
    with pdfplumber.open(file_bytes) as pdf:
        for page in pdf.pages:
            if use_ocr:
                img = page.to_image(resolution=300)
                text += pytesseract.image_to_string(Image.frombytes("RGB", img.size, img.tobytes()))
            else:
                text += page.extract_text(layout=True) or ""
    return text

# --- Extraction Functions ---
def extract_total_use(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "total use" in line.lower() or "electricity you used" in line.lower():
            for j in range(i, min(i + 5, len(lines))):
                tokens = lines[j].split()
                for token in tokens:
                    if token.replace(",", "").isdigit():
                        return token.replace(",", "")
    return ""

def extract_charges(text):
    rows = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    in_charge_section = False
    
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(x in line_lower for x in ["type of charge", "how we calculate", "delivery charges", "supply charges"]):
            in_charge_section = True
            continue
        if in_charge_section and any(x in line_lower for x in ["total electric", "your monthly", "deferred payment"]):
            in_charge_section = False
            break
        if in_charge_section:
            tokens = line.rsplit("$", 1)
            if len(tokens) == 2:
                desc, amount_str = tokens[0].strip(), tokens[1].strip()
                amount_str = amount_str.replace(",", "").replace("−", "-")
                try:
                    amount = float(amount_str)
                    if not any(x in desc.lower() for x in ["total", "meter", "page", "temp", "service number"]):
                        # Remove rate/kWh calculation text
                        desc = " ".join([w for w in desc.split() if not (w.startswith("$") or "kwh" in w.lower() or "kw" in w.lower())])
                        rows.append({"Charge_Type": desc, "Amount": amount})
                except ValueError:
                    continue
    return rows

def extract_metadata(text):
    metadata = {"Bill_Month_Year": "", "Account_Number": ""}
    lines = text.splitlines()
    
    for line in lines:
        line_lower = line.lower()
        if "bill issue date" in line_lower:
            date_part = line.split(":", 1)[1].strip()
            try:
                parsed_date = date_parse(date_part, fuzzy=True)
                metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
            except:
                pass
        if "account number" in line_lower:
            acc_part = line.split(":", 1)[1].strip() if ":" in line else line.split()[-1]
            metadata["Account_Number"] = "".join(filter(str.isdigit, acc_part))
    return metadata

# --- Main Processing Function ---
def process_pdf(file_io):
    file_bytes = file_io.getvalue()
    bill_hash = hashlib.md5(file_bytes).hexdigest()
    
    text = extract_text_from_pdf(file_io)
    charges = extract_charges(text)
    metadata = extract_metadata(text)
    total_use = extract_total_use(text)
    
    if not (charges and metadata["Bill_Month_Year"] and total_use):
        text = extract_text_from_pdf(file_io, use_ocr=True)
        charges = charges or extract_charges(text)
        metadata = metadata if metadata["Bill_Month_Year"] else extract_metadata(text)
        total_use = total_use or extract_total_use(text)

    # Consolidate charges
    consolidated = {}
    for c in charges:
        ct = standardize_charge_type(c["Charge_Type"])
        amt = c["Amount"]
        consolidated[ct] = consolidated.get(ct, 0) + amt

    # Generate User ID
    account_number = metadata.get("Account_Number", "")
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    user_id = st.session_state.customer_ids.get(account_number) or str(uuid.uuid4())
    if account_number:
        st.session_state.customer_ids[account_number] = user_id

    # Check for existing bill
    existing = worksheet.get_all_records()
    bill_id = next((row["Bill_ID"] for row in existing if row.get("Bill_Hash") == bill_hash), str(uuid.uuid4()))

    # Build output row
    output_row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash,
        "Total Use": total_use,
        **{f"{ct} Amount": round(amt, 2) for ct, amt in consolidated.items() if amt != 0}
    }
    return output_row

# --- Sheet Append Function ---
def append_row_to_sheet(row_dict):
    current_data = worksheet.get_all_values()
    headers = current_data[0] if current_data else []
    meta_cols = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash", "Total Use"]
    charge_cols = sorted([col for col in row_dict if col not in meta_cols])
    full_headers = meta_cols + charge_cols

    if full_headers != headers:
        worksheet.update("A1", [full_headers])
        if len(current_data) > 1:
            for i, row in enumerate(current_data[1:], start=2):
                row_dict_existing = dict(zip(headers, row))
                padded_row = [str(row_dict_existing.get(h, "")) for h in full_headers]
                worksheet.update(f"A{i}", [padded_row])

    row_values = [str(row_dict.get(h, "")) for h in full_headers]
    worksheet.append_row(row_values)

# --- Streamlit App Interface ---
st.title("Delmarva BillWatch")
st.write("Upload your PDF bill. Your deidentified utility charge information will be stored in Google Sheets.")
st.write("**Privacy Disclaimer:** By submitting your form, you agree that your response may be used to support an investigation into billing issues with Delmarva Power. Your information will not be shared publicly or sold. This form is for informational and organizational purposes only and does not constitute legal representation.")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf", accept_multiple_files=False)

if uploaded_file is not None:
    file_io = io.BytesIO(uploaded_file.read())
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    existing = worksheet.get_all_records()
    if any(r.get("Bill_Hash") == bill_hash for r in existing):
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        try:
            output_row = process_pdf(file_io)
            append_row_to_sheet(output_row)
            st.success("Thank you for your contribution!")
            st.write("Extracted Data:", output_row)  # Debugging preview
        except Exception as e:
            st.error(f"An error occurred: {e}")
