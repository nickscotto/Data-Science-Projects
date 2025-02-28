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

# --- Google Sheets Setup ---
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)

SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
worksheet = spreadsheet.sheet1

# --- Helper: Reassemble Table Rows Using Word Clustering (unchanged) ---
def extract_table_rows(page, tolerance=5):
    words = page.extract_words()
    rows = {}
    for word in words:
        key = round(word['top'] / tolerance) * tolerance
        rows.setdefault(key, []).append(word)
    sorted_rows = []
    for key in sorted(rows.keys()):
        row_words = sorted(rows[key], key=lambda w: w['x0'])
        row_text = " ".join(w['text'] for w in row_words)
        sorted_rows.append(row_text)
    return sorted_rows

# --- Helper: Extract the Amount from a Row (unchanged) ---
def extract_amount_from_row(row):
    m = re.search(r'per\s+k(?:Wh|W)\s+(-?[\d,]+(?:\.\d+)?)', row, re.IGNORECASE)
    if m:
        raw_amount = m.group(1).replace(",", "")
        try:
            return float(raw_amount)
        except:
            pass
    tokens = row.split()
    for token in reversed(tokens):
        token_clean = token.strip("$").replace(",", "")
        try:
            return float(token_clean)
        except:
            continue
    return None

# --- Helper: Standardize Charge Type Names (unchanged) ---
def standardize_charge_type(charge_type):
    charge_type = charge_type.strip()
    if "Total Electric Charges" in charge_type:
        return "Total Electric Charges"
    if "First" in charge_type:
        charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type).strip()
        if "Distribution" in charge_type:
            return "Distribution Charge First kWh"
        elif "Transmission" in charge_type:
            return "Transmission Charge First kWh"
        else:
            return charge_type
    elif "Last" in charge_type:
        charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type).strip()
        if "Distribution" in charge_type:
            return "Distribution Charge Last kWh"
        elif "Transmission" in charge_type:
            return "Transmission Charge Last kWh"
        else:
            return charge_type
    charge_type = re.sub(r'\b(First|Last|Next)\b', '', charge_type).strip()
    charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type).strip()
    return charge_type

# --- Key Helper: Extract Total Use (unchanged) ---
def extract_total_use_from_pdf(file_io):
    with pdfplumber.open(file_io) as pdf:
        if len(pdf.pages) < 2:
            return ""
        page = pdf.pages[1]
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            if "meter energy" in line.lower() and "number total" in line.lower():
                if i + 1 < len(lines) and "number type" in lines[i + 1].lower() and "days use" in lines[i + 1].lower():
                    if i + 2 < len(lines):
                        tokens = lines[i + 2].split()
                        for j in range(len(tokens) - 1, -1, -1):
                            if tokens[j].isdigit():
                                return tokens[j]
        for line in lines:
            tokens = line.split()
            if (len(tokens) >= 6 and tokens[0].startswith("1ND") and "kWh" in " ".join(tokens)):
                for j in range(len(tokens) - 1, -1, -1):
                    if tokens[j].isdigit():
                        return tokens[j]
        return ""

# --- Updated: Extract Charges from PDF (Fix Negative Amounts) ---
def extract_charges_from_pdf(file_io):
    rows_out = []
    
    with pdfplumber.open(file_io) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            text_lower = text.lower()

            # Try to detect tables with specific headers
            tables = page.find_tables({
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "snap_tolerance": 5,
                "join_tolerance": 5,
                "edge_min_length": 3,
                "intersection_tolerance": 5,
            })
            
            for table in tables:
                extracted_table = table.extract()
                if extracted_table and len(extracted_table) > 1:
                    headers = [h.lower() if h else "" for h in extracted_table[0]]
                    if "type of charge" in headers and "amount($)" in headers:
                        st.write(f"Page {page_num}: Found charges table with headers: {headers}")
                        type_idx = headers.index("type of charge")
                        calc_idx = headers.index("how we calculate this charge") if "how we calculate this charge" in headers else -1
                        amount_idx = headers.index("amount($)")
                        table_bbox = table.bbox
                        st.write(f"Page {page_num}: Table bounds: {table_bbox}")

                        for i, row in enumerate(extracted_table[1:], start=1):
                            if len(row) <= max(type_idx, calc_idx, amount_idx):
                                continue
                            charge_type = row[type_idx].strip() if row[type_idx] else ""
                            amount_text = row[amount_idx].strip() if row[amount_idx] else ""
                            
                            try:
                                amount = float(amount_text.replace("$", "").replace(",", "").replace("−", "-"))
                                calc_text = row[calc_idx].strip() if calc_idx >= 0 and row[calc_idx] else ""
                                rate_match = re.search(r'X\s+\$([\d\.]+(?:[−-])?)', calc_text)
                                rate_val = rate_match.group(1) if rate_match else ""
                                row_data = {
                                    "Charge_Type": charge_type,
                                    "Rate": rate_val,
                                    "Amount": amount
                                }
                                rows_out.append(row_data)
                                st.write(f"Page {page_num}: Extracted row: {row_data}")
                                if "total" in charge_type.lower() and i == len(extracted_table[1:]):
                                    st.write(f"Page {page_num}: Last row is a total, ending table: {charge_type}")
                            except (ValueError, TypeError):
                                st.write(f"Page {page_num}: Failed to parse amount from '{amount_text}' in row: {row}")
                                continue

            # Fallback: Text-based extraction with improved parsing
            if "delivery charges" in text_lower or "supply charges" in text_lower:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                in_table = False
                current_charge = ""
                st.write(f"Page {page_num}: Processing lines for fallback:")
                for i, line in enumerate(lines):
                    if "type of charge" in line.lower() and "amount($)" in line.lower():
                        in_table = True
                        st.write(f"Page {page_num}: Detected table header in text: {line}")
                        continue
                    if in_table:
                        st.write(f"Page {page_num}: Processing line: {line}")
                        # Preprocess line to replace − with - before matching
                        cleaned_line = re.sub(r'−', '-', line)
                        amount_match = re.search(r'(-?\d{1,3}(?:,\d{3})*\.\d{2})$', cleaned_line)
                        if amount_match:
                            amount_text = amount_match.group(0).replace(",", "")
                            try:
                                amount = float(amount_text)
                                combined_line = current_charge + " " + line if current_charge else line
                                rate_match = re.search(r'X\s+\$([\d\.]+(?:[−-])?)', combined_line)
                                rate_val = rate_match.group(1) if rate_match else ""
                                charge_type_end = combined_line.find("X $") if rate_val else amount_match.start()
                                charge_type = combined_line[:charge_type_end].strip() if charge_type_end > 0 else combined_line.strip()
                                row_data = {
                                    "Charge_Type": charge_type,
                                    "Rate": rate_val,
                                    "Amount": amount
                                }
                                rows_out.append(row_data)
                                st.write(f"Page {page_num}: Fallback extracted row: {row_data}")
                                if "total" in charge_type.lower() and (i == len(lines) - 1 or not any("charge" in next_line.lower() or "total" in next_line.lower() for next_line in lines[i+1:])):
                                    st.write(f"Page {page_num}: Last total row detected, ending table: {charge_type}")
                                    in_table = False
                                current_charge = ""
                            except (ValueError, TypeError):
                                st.write(f"Page {page_num}: Fallback failed to parse amount from '{amount_text}' in line: {cleaned_line}")
                                continue
                        else:
                            current_charge = current_charge + " " + line if current_charge else line

            # Look for the final "Total Electric Charges" line across all pages
            if "total electric charges" in text_lower:
                for line in lines:
                    if "total electric charges" in line.lower():
                        st.write(f"Page {page_num}: Processing final total line: {line}")
                        cleaned_line = re.sub(r'−', '-', line)
                        amount_match = re.search(r'(-?\d{1,3}(?:,\d{3})*\.\d{2})$', cleaned_line)
                        if amount_match:
                            amount_text = amount_match.group(0).replace(",", "")
                            try:
                                amount = float(amount_text)
                                # Check for existing Total Electric Charges and update if variant found
                                total_exists = next((r for r in rows_out if "total electric charges" in r["Charge_Type"].lower()), None)
                                if total_exists:
                                    total_exists["Amount"] = amount
                                    total_exists["Charge_Type"] = "Total Electric Charges"  # Standardize to base name
                                    st.write(f"Page {page_num}: Updated existing Total Electric Charges to: {amount}")
                                else:
                                    row_data = {
                                        "Charge_Type": "Total Electric Charges",
                                        "Rate": "",
                                        "Amount": amount
                                    }
                                    rows_out.append(row_data)
                                    st.write(f"Page {page_num}: Extracted final total row: {row_data}")
                            except (ValueError, TypeError):
                                st.write(f"Page {page_num}: Failed to parse final total from '{amount_text}' in line: {cleaned_line}")

    if not rows_out:
        st.write("No charges tables found in the PDF.")
    else:
        st.write(f"Total charges extracted: {len(rows_out)}")
    return rows_out

# --- Extract Metadata from PDF (unchanged) ---
def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Account_Number": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = ""
        for page in pdf.pages:
            text += page.extract_text() or ""  # Concatenate text from all pages
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        st.write("Metadata lines checked:", lines)  # Debug all lines
        for line in lines:
            match = re.search(r"(?:Account\s*(?:number|#)\s*:?\s*|\bAcct\s*#?\s*)(\d{4}\s*\d{4}\s*\d{3})", line, re.IGNORECASE)
            if match:
                metadata["Account_Number"] = match.group(1).replace(" ", "")
                st.write(f"Extracted Account Number (not stored): {metadata['Account_Number']}")
                break
        for line in lines:
            match = re.search(r"Bill\s*Issue\s*date:\s*(.+)", line, re.IGNORECASE)
            if match:
                date_text = match.group(1).strip()
                try:
                    parsed_date = date_parse(date_text, fuzzy=True)
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                    st.write(f"Extracted Bill Month Year: {metadata['Bill_Month_Year']}")
                except Exception as e:
                    st.write(f"Failed to parse date from '{date_text}': {e}")
                break
    return metadata

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    total_use = extract_total_use_from_pdf(file_io)

    st.write("Extracted Charges:", charges)

    consolidated = {}
    for c in charges:
        ct = standardize_charge_type(c["Charge_Type"])
        amt = c["Amount"]
        rate_val = c["Rate"]
        if ct in consolidated:
            consolidated[ct]["Amount"] += amt
            if not consolidated[ct]["Rate"] and rate_val:
                consolidated[ct]["Rate"] = rate_val
        else:
            consolidated[ct] = {"Amount": amt, "Rate": rate_val}

    st.write("Consolidated Charges:", consolidated)

    account_number = metadata.get("Account_Number", "").replace(" ", "")
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    if account_number:
        if account_number in st.session_state.customer_ids:
            user_id = st.session_state.customer_ids[account_number]
        else:
            user_id = str(uuid.uuid4())
            st.session_state.customer_ids[account_number] = user_id
            st.write(f"New User_ID generated for Account_Number {account_number}: {user_id}")
    else:
        user_id = str(uuid.uuid4())
        st.write(f"No Account_Number found, generated new User_ID: {user_id}")

    existing = worksheet.get_all_records()
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())

    output_row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash,
        "Total Use": total_use
    }
    for ct in consolidated:
        if consolidated[ct]["Amount"] != 0:
            output_row[f"{ct} Amount"] = consolidated[ct]["Amount"]
        if consolidated[ct]["Rate"]:
            output_row[f"{ct} Rate"] = consolidated[ct]["Rate"]

    st.write("Final Output Row:", output_row)
    return output_row

# --- Sheet Append Function (unchanged) ---
def append_row_to_sheet(row_dict):
    meta_cols = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash", "Total Use"]
    current_data = worksheet.get_all_values()
    if current_data:
        headers = current_data[0]
        existing_rows = current_data[1:]
    else:
        headers = []
        existing_rows = []

    charge_cols = [col for col in row_dict if col not in meta_cols]
    existing_charge_cols = [col for col in headers if col not in meta_cols]
    new_charge_cols = [col for col in charge_cols if col not in existing_charge_cols]
    all_charge_cols = existing_charge_cols + new_charge_cols
    full_headers = meta_cols + all_charge_cols

    if full_headers != headers:
        worksheet.update("A1", [full_headers])
        if existing_rows:
            for i, row in enumerate(existing_rows, start=2):
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
    file_bytes = uploaded_file.read()
    file_io = io.BytesIO(file_bytes)
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    existing = worksheet.get_all_records()
    duplicate = any(r.get("Bill_Hash") == bill_hash for r in existing)
    if duplicate:
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(file_io)
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
