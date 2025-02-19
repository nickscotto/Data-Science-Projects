import streamlit as st
import io
import os
import re
import uuid
import hashlib
from datetime import datetime
import pdfplumber
import pandas as pd

# --- File Paths (using relative paths for Codespace persistence) ---
EXCEL_PATH = "output.xlsx"
MAPPING_PATH = "customer_ids.csv"

# --- Helper: Standardize charge type names ---
def standardize_charge_type(charge_type):
    """
    Remove numeric kWh values from the charge type string.
    E.g., "Distribution Charge Last 2190 kWh" becomes "Distribution Charge Last kWh".
    """
    standardized = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type, flags=re.IGNORECASE)
    return standardized.strip()

# --- PDF Extraction Functions ---
def extract_charges_from_pdf(file_bytes):
    """
    Extract charge rows from pages 2 and 3.
    Returns a list of dicts with keys: Charge_Type, Rate, Amount.
    """
    rows = []
    regex_pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?"
        r"\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    with pdfplumber.open(file_bytes) as pdf:
        # Process pages 2 and 3 (0-indexed)
        for page_index in [1, 2]:
            if page_index < len(pdf.pages):
                text = pdf.pages[page_index].extract_text() or ""
                lines = text.splitlines()
                header_found = False
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if not header_found and "Type of charge" in line and "Amount($" in line:
                        header_found = True
                        continue
                    if header_found:
                        match = re.match(regex_pattern, line)
                        if match:
                            desc = match.group("desc").strip()
                            rate_val = match.group("rate") or ""
                            amount_str = match.group("amount").replace(",", "").replace("−", "")
                            try:
                                amount = float(amount_str)
                            except ValueError:
                                continue
                            lower_desc = desc.lower()
                            if any(keyword in lower_desc for keyword in ["page", "year", "meter", "temp", "date"]):
                                continue
                            rows.append({
                                "Charge_Type": desc,
                                "Rate": rate_val,
                                "Amount": amount
                            })
    return rows

def extract_metadata_from_pdf(file_bytes):
    """
    Extract metadata from page 1.
    Finds a line like "January 2025" (converted to "MM-YYYY") and assumes the next non-empty line is the person's name.
    Returns a dict with:
      - "Bill_Month_Year": formatted as "MM-YYYY"
      - "Person": the extracted name.
    """
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = text.splitlines()
        month_regex = r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$"
        for i, line in enumerate(lines):
            candidate = line.strip()
            if re.match(month_regex, candidate, re.IGNORECASE):
                try:
                    parsed_date = datetime.strptime(candidate, "%B %Y")
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except Exception:
                    metadata["Bill_Month_Year"] = candidate
                # Next non-empty line assumed to be the person's name.
                for j in range(i+1, len(lines)):
                    candidate2 = lines[j].strip()
                    if candidate2:
                        metadata["Person"] = candidate2
                        break
                break
    return metadata

# --- Mapping Persistence Functions ---
def load_customer_ids(mapping_path):
    """Load the Person-to-User_ID mapping from CSV, if available."""
    if os.path.exists(mapping_path):
        df_map = pd.read_csv(mapping_path)
        return dict(zip(df_map["Person"], df_map["User_ID"]))
    else:
        return {}

def save_customer_ids(mapping, mapping_path):
    """Save the Person-to-User_ID mapping to CSV."""
    df_map = pd.DataFrame(mapping.items(), columns=["Person", "User_ID"])
    os.makedirs(os.path.dirname(mapping_path) or ".", exist_ok=True)
    df_map.to_csv(mapping_path, index=False)

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    """
    Process a PDF bill (file-like object) and return a row dictionary.
    The row includes:
      - User_ID (unique per Person)
      - Bill_ID (unique per bill, determined by bill hash)
      - Bill_Month_Year
      - Bill_Hash (for duplicate detection)
      - Standardized charge columns (with Amount and Rate)
    (The Person field is used internally for mapping but is not output.)
    """
    # Compute a hash of the file for duplicate detection.
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    
    # Consolidate charges (standardizing keys)
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
    
    # Load or initialize the customer mapping.
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = load_customer_ids(MAPPING_PATH)
    
    person = metadata.get("Person", "")
    if person in st.session_state.customer_ids:
        user_id = st.session_state.customer_ids[person]
    else:
        user_id = str(uuid.uuid4())
        st.session_state.customer_ids[person] = user_id
        save_customer_ids(st.session_state.customer_ids, MAPPING_PATH)
    
    # Check for duplicate bill using bill_hash.
    bill_id = None
    if "df" in st.session_state and not st.session_state.df.empty and "Bill_Hash" in st.session_state.df.columns:
        duplicates = st.session_state.df[st.session_state.df["Bill_Hash"] == bill_hash]
        if not duplicates.empty:
            bill_id = duplicates.iloc[0]["Bill_ID"]
    
    if not bill_id:
        bill_id = str(uuid.uuid4())
    
    # Build the row (do not include Person in the output).
    row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash
    }
    for ct, data in consolidated.items():
        if data["Amount"] != 0:
            row[f"{ct} Amount"] = data["Amount"]
        if data["Rate"]:
            row[f"{ct} Rate"] = data["Rate"]
    return row

# --- Function to Save Data ---
def save_excel_to_disk(df, excel_file=EXCEL_PATH):
    """Save the DataFrame as an Excel file to a relative path."""
    os.makedirs(os.path.dirname(excel_file) or ".", exist_ok=True)
    df.to_excel(excel_file, index=False)

# --- Streamlit App Interface ---
st.title("Delmarva BillWatch")
st.write("Upload your PDF bill. Your deidentified utility charge information will be added to our secured database for analysis.")

# Load existing Excel file (if any) from the Codespace.
if "df" not in st.session_state:
    if os.path.exists(EXCEL_PATH):
        st.session_state.df = pd.read_excel(EXCEL_PATH)
    else:
        st.session_state.df = pd.DataFrame()

# Allow only one PDF upload at a time.
uploaded_file = st.file_uploader("Choose a PDF file", type="pdf", accept_multiple_files=False)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    file_io = io.BytesIO(file_bytes)
    # Check duplicate by computing bill hash.
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    if "Bill_Hash" in st.session_state.df.columns and (st.session_state.df["Bill_Hash"] == bill_hash).any():
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        row = process_pdf(file_io)
        st.session_state.df = pd.concat([st.session_state.df, pd.DataFrame([row])], ignore_index=True)
        save_excel_to_disk(st.session_state.df, excel_file=EXCEL_PATH)
        st.success("Thank you for your contribution!")
