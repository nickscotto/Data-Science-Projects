import streamlit as st
import io
import uuid
import hashlib
from dateutil.parser import parse as date_parse
import pdfplumber
import openai
import json
import gspread
from google.oauth2.service_account import Credentials

# --- Google Sheets Setup ---
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
worksheet = spreadsheet.sheet1

# --- OpenAI Setup ---
client = openai.OpenAI(api_key=st.secrets["openai"])  # Initialize client with API key

# --- Helper: Standardize Charge Type Names ---
def standardize_charge_type(charge_type):
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
    charge_type = charge_type.lower()
    for key, value in charge_mapping.items():
        if key in charge_type:
            return value
    return charge_type.title()

# --- Helper: Extract Text Between Markers ---
def extract_charge_sections(file_io):
    sections = []
    with pdfplumber.open(file_io) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += page.extract_text(layout=True) or ""
        
        lines = full_text.splitlines()
        start_marker = "Type of charge How we calculate this charge Amount($)"
        end_markers = [
            "Total Electric Charges - Residential Heating",
            "Total Electric Delivery Charges",
            "Total Electric Supply Charges"
        ]
        
        in_section = False
        section_text = ""
        for line in lines:
            if start_marker in line:
                in_section = True
                section_text = ""
                continue
            if in_section and any(end_marker in line for end_marker in end_markers):
                in_section = False
                if section_text.strip():
                    sections.append(section_text.strip())
                continue
            if in_section:
                section_text += line + "\n"
    
    return sections

# --- OpenAI Processing ---
def process_with_openai(section_text):
    prompt = """
    You are an expert at extracting data from utility bill tables. Given the following text from a PDF bill charge section (between 'Type of charge How we calculate this charge Amount($)' and a 'Total' line), extract all rows as a structured table. Each row has three columns:
    - "Charge_Type": The name of the charge (e.g., "Customer Charge", "Distribution Charge First 500 kWh")
    - "Calculation": The calculation description (e.g., "12032 kWh X $0.0000950 per kWh")
    - "Amount": The dollar amount (e.g., 1.14, as a float)

    Return the result as a JSON object with a "Charges" key containing a list of dictionaries with "Charge_Type", "Calculation", and "Amount" keys. Do not include the total lines.

    Here’s the text:
    {text}
    """
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a precise data extraction tool."},
            {"role": "user", "content": prompt.format(text=section_text)}
        ],
        temperature=0.0,
        response_format={"type": "json_object"}  # Requires openai>=1.2.0
    )
    
    return json.loads(response.choices[0].message.content)

# --- Main Processing Function ---
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    
    # Extract metadata manually
    full_text = ""
    with pdfplumber.open(file_io) as pdf:
        for page in pdf.pages:
            full_text += page.extract_text(layout=True) or ""
    lines = full_text.splitlines()
    metadata = {"Bill_Month_Year": "", "Account_Number": "", "Total_Use": ""}
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
        if "total use" in line_lower or "electricity you used" in line_lower:
            for token in line.split():
                if token.replace(",", "").isdigit():
                    metadata["Total_Use"] = token.replace(",", "")
                    break

    file_io.seek(0)
    
    # Extract and process charge sections
    charge_sections = extract_charge_sections(file_io)
    all_charges = []
    for section in charge_sections:
        llm_result = process_with_openai(section)
        all_charges.extend(llm_result["Charges"])

    # Consolidate charges (only for Amount, keep Calculation separate)
    consolidated = {}
    charge_details = []
    for c in all_charges:
        ct = standardize_charge_type(c["Charge_Type"])
        amt = float(c["Amount"])
        calc = c["Calculation"]
        consolidated[ct] = consolidated.get(ct, 0) + amt
        charge_details.append({"Charge_Type": ct, "Calculation": calc, "Amount": amt})

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
        "Total Use": metadata.get("Total_Use", ""),
        **{f"{ct} Amount": round(amt, 2) for ct, amt in consolidated.items() if amt != 0}
    }
    # Add calculations as lists
    calc_dict = {}
    for detail in charge_details:
        ct = detail["Charge_Type"]
        if ct not in calc_dict:
            calc_dict[ct] = []
        calc_dict[ct].append(detail["Calculation"])
    for ct, calcs in calc_dict.items():
        output_row[f"{ct} Calculations"] = "; ".join(calcs)
    
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
            st.write("Extracted Data:", output_row)
        except Exception as e:
            st.error(f"An error occurred: {e}")
