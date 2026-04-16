import sys
import re
import time
import pdfplumber
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import io

BILLING_MIN_AMOUNT = 3000
RECEIPT_MIN_AMOUNT = 500

JUNK_KEYWORDS = [
    "INTEREST", "CHARGE", "FEE", "PENAL", "WAIVER",
    "ADJUSTMENT", "REVERSAL", "GST", "TAX", "INSURANCE",
    "FORECLOSURE", "PRECLOSURE", "OVERDUE", "LATE",
    "BROKEN PERIOD", "BPI", "PROCESSING",
    "DISBURSAL", "DISBURSEMENT", "BOOKING", "MARGIN MONEY",
    "AMOUNT FINANCED", "PREMIUM", "INTEREST REVERSAL",
]

FORECLOSURE_KEYWORDS = [
    "FORECLOSURE", "PRECLOSURE", "LOAN CLOSURE",
    "OUTSTANDING PRINCIPLE", "OUSTANDING PRINCIPLE",
    "CLOSURE", "SETTLED", "SETTLEMENT",
]

DATE_PATTERN_ANY = r"\d{2}-[A-Za-z]{3}-?\d{4}"
DATE_LINE_START = r"^\d{2}-[A-Za-z]{3}-?\d{4}"


class ParseLogger:
    """Captures parse logs with timestamps for display in UI."""

    def __init__(self):
        self.logs = []
        self.start_time = time.time()

    def log(self, message, level="info"):
        elapsed = time.time() - self.start_time
        mins = int(elapsed // 60)
        secs = elapsed % 60
        timestamp = f"{mins:02d}:{secs:04.1f}"
        self.logs.append({
            "timestamp": timestamp,
            "message": message,
            "level": level,
        })

    def get_logs(self):
        return self.logs


def parse_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = ["%d-%b-%Y", "%d-%B-%Y", "%d-%b%Y", "%d-%B%Y", "%d/%m/%Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def normalize_date_str(date_str):
    if not date_str:
        return date_str
    m = re.match(r"(\d{2}-[A-Za-z]{3})(\d{4})$", date_str.strip())
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return date_str.strip()


def clean_amount(value):
    try:
        return float(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def is_junk_receipt(desc_upper):
    HIGH_PRIORITY_JUNK = [
        "DISBURSAL", "DISBURSEMENT", "BOOKING",
        "MARGIN MONEY", "AMOUNT FINANCED", "PREMIUM",
        "FORECLOSURE", "PRECLOSURE",
    ]
    for keyword in HIGH_PRIORITY_JUNK:
        if keyword in desc_upper:
            return True
    for keyword in JUNK_KEYWORDS:
        if keyword in desc_upper:
            if "RECEIPT" in desc_upper or "PAYMENT" in desc_upper or "COLLECTION" in desc_upper:
                return False
            return True
    return False


def is_real_bounce_reversal(desc_upper, debit_amount, emi_threshold=None):
    if "COLLECTION BOUNCED" in desc_upper:
        return True
    if "WAIVER" in desc_upper:
        return False
    if "BOUNCED" in desc_upper and ("BECUASE" in desc_upper or "BECAUSE" in desc_upper):
        return False
    if "GST" in desc_upper and "BOUNCE" in desc_upper:
        return False
    if "RETURN" in desc_upper and "CHARGE" in desc_upper:
        return False
    if emi_threshold and debit_amount < emi_threshold * 0.5:
        return False
    if debit_amount >= RECEIPT_MIN_AMOUNT:
        return True
    return False


def is_new_txn_start(line_str):
    s = line_str.strip()
    if not s:
        return False
    if re.match(r'^\d{2}-\s*$', s):
        return True
    if re.match(r'^\d{2}-[A-Za-z]{3}', s):
        return True
    m = re.match(r'^(\d{2})-\s+\S', s)
    if m:
        day_val = int(m.group(1))
        if 1 <= day_val <= 31:
            return True
    return False


def rebuild_broken_lines(lines):
    rebuilt = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            rebuilt.append(lines[i])
            i += 1
            continue

        partial_only = re.match(r'^(\d{2}-[A-Za-z]{3}-)\s*(.*?)$', line)
        if partial_only:
            partial_date = partial_only.group(1)
            partial_rest = partial_only.group(2).strip()

            if i + 1 < len(lines):
                next_l = lines[i + 1].strip()
                complete_val = re.match(r'^(\d{2}-[A-Za-z]{3}-?\d{4})\s+(.*)', next_l)
                if complete_val:
                    val_date = complete_val.group(1)
                    val_rest = complete_val.group(2).strip()

                    yr_extract = re.search(r'(\d{4})$', val_date)
                    if yr_extract:
                        txn_date = f"{partial_date}{yr_extract.group(1)}"

                        desc_parts = []
                        if partial_rest:
                            desc_parts.append(partial_rest)
                        if val_rest:
                            desc_parts.append(val_rest)

                        j = i + 2
                        while j < len(lines):
                            nxt = lines[j].strip()
                            if not nxt:
                                break
                            if is_new_txn_start(nxt):
                                break
                            desc_parts.append(nxt)
                            j += 1

                        all_desc = " ".join(desc_parts)
                        combined = f"{txn_date} {val_date} {all_desc}"
                        combined = re.sub(r'\s+', ' ', combined).strip()
                        rebuilt.append(combined)
                        i = j
                        continue

        day_start = re.match(r'^(\d{2})-\s*(.*?)$', line)

        if day_start:
            txn_day = day_start.group(1)
            day_rest = day_start.group(2).strip()

            found = False
            for look1 in range(1, 4):
                if i + look1 >= len(lines):
                    break
                line_v = lines[i + look1].strip()

                val_match = re.match(r'^(\d{2}-[A-Za-z]{3}-)\s*(.*)', line_v)
                if not val_match:
                    continue

                val_dd_mon = val_match.group(1)
                val_rest = val_match.group(2).strip()

                for look2 in range(look1 + 1, look1 + 4):
                    if i + look2 >= len(lines):
                        break
                    line_m = lines[i + look2].strip()

                    mon_match = re.match(r'^([A-Za-z]{3}-)\s*(.*)', line_m)
                    if not mon_match:
                        continue

                    txn_mon = mon_match.group(1)
                    mon_rest = mon_match.group(2).strip()

                    val_year = None
                    txn_year = None
                    last_consumed = look2
                    extra_parts = []

                    for look3 in range(look2 + 1, look2 + 4):
                        if i + look3 >= len(lines):
                            break
                        line_y = lines[i + look3].strip()

                        yr_match = re.match(r'^(\d{4})\s*(.*)', line_y)
                        if yr_match:
                            yr_val = yr_match.group(1)
                            yr_rest = yr_match.group(2).strip()
                            if val_year is None:
                                val_year = yr_val
                                if yr_rest:
                                    extra_parts.append(yr_rest)
                                last_consumed = look3
                            elif txn_year is None:
                                txn_year = yr_val
                                if yr_rest:
                                    extra_parts.append(yr_rest)
                                last_consumed = look3
                                break
                        else:
                            extra_parts.append(line_y)
                            last_consumed = look3

                    if val_year and txn_year is None:
                        txn_year = val_year

                    if val_year:
                        txn_date = f"{txn_day}-{txn_mon}{txn_year}"
                        val_date = f"{val_dd_mon}{val_year}"

                        desc_parts = []
                        if day_rest:
                            desc_parts.append(day_rest)
                        if val_rest:
                            desc_parts.append(val_rest)
                        if mon_rest:
                            desc_parts.append(mon_rest)
                        desc_parts.extend(extra_parts)

                        j = i + last_consumed + 1
                        while j < len(lines):
                            nxt = lines[j].strip()
                            if not nxt:
                                break
                            if is_new_txn_start(nxt):
                                break
                            desc_parts.append(nxt)
                            j += 1

                        all_desc = " ".join(desc_parts)
                        combined = f"{txn_date} {val_date} {all_desc}"
                        combined = re.sub(r'\s+', ' ', combined).strip()
                        rebuilt.append(combined)
                        i = j
                        found = True
                        break

                if found:
                    break

            if found:
                continue

        broken_match = re.match(r'^(\d{2})-\s+(\d{2})-(.*)$', line)
        if broken_match and i + 2 < len(lines):
            day1 = broken_match.group(1)
            day2 = broken_match.group(2)
            rest_of_first = broken_match.group(3).strip()
            data_line = lines[i + 1].strip()
            month_line = lines[i + 2].strip()
            month_match = re.match(r'^([A-Za-z]{3}-\d{4})\s+([A-Za-z]{3}-\d{4})(.*)$', month_line)
            if month_match:
                date1 = f"{day1}-{month_match.group(1)}"
                date2 = f"{day2}-{month_match.group(2)}"
                combined = f"{date1} {date2} {rest_of_first} {data_line} {month_match.group(3).strip()}"
                combined = re.sub(r'\s+', ' ', combined).strip()
                rebuilt.append(combined)
                i += 3
                continue
            single_month = re.match(r'^([A-Za-z]{3}-\d{4})(.*)$', month_line)
            if single_month:
                date1 = f"{day1}-{single_month.group(1)}"
                date2 = f"{day2}-{single_month.group(1)}"
                combined = f"{date1} {date2} {rest_of_first} {data_line} {single_month.group(2).strip()}"
                combined = re.sub(r'\s+', ' ', combined).strip()
                rebuilt.append(combined)
                i += 3
                continue

        broken_match2 = re.match(r'^(\d{2}-[A-Za-z]{3}-)\s+(\d{2}-[A-Za-z]{3}-)\s*(.*)$', line)
        if broken_match2:
            part1 = broken_match2.group(1)
            part2 = broken_match2.group(2)
            rest_of_first = broken_match2.group(3).strip()
            found = False
            for look in range(1, 4):
                if i + look < len(lines):
                    year_line = lines[i + look].strip()
                    year_match = re.match(r'^(\d{4})\s+(\d{4})\s*(.*)$', year_line)
                    if year_match:
                        date1 = f"{part1}{year_match.group(1)}"
                        date2 = f"{part2}{year_match.group(2)}"
                        middle = " ".join(lines[i + m].strip() for m in range(1, look))
                        combined = f"{date1} {date2} {rest_of_first} {middle} {year_match.group(3).strip()}"
                        combined = re.sub(r'\s+', ' ', combined).strip()
                        rebuilt.append(combined)
                        i += look + 1
                        found = True
                        break
                    year_match_s = re.match(r'^(\d{4})\s+(.*)$', year_line)
                    if year_match_s and not re.match(r'^\d{2}-', year_line):
                        y1 = year_match_s.group(1)
                        date1 = f"{part1}{y1}"
                        date2 = f"{part2}{y1}"
                        middle = " ".join(lines[i + m].strip() for m in range(1, look))
                        combined = f"{date1} {date2} {rest_of_first} {middle} {year_match_s.group(2).strip()}"
                        combined = re.sub(r'\s+', ' ', combined).strip()
                        rebuilt.append(combined)
                        i += look + 1
                        found = True
                        break
            if found:
                continue
            rebuilt.append(lines[i])
            i += 1
            continue

        match_fp = re.match(
            r'^(\d{2}-[A-Za-z]{3}-?\d{4})\s+(\d{2}-[A-Za-z]{3}-)\s*(.*)$', line
        )
        if match_fp:
            full_date = match_fp.group(1)
            partial = match_fp.group(2)
            rest = match_fp.group(3).strip()
            found = False
            for look in range(1, 4):
                if i + look < len(lines):
                    year_line = lines[i + look].strip()
                    year_match = re.match(r'^(\d{4})\s*(.*)$', year_line)
                    if year_match and not re.match(r'^\d{2}-', year_line):
                        date2 = f"{partial}{year_match.group(1)}"
                        middle = " ".join(lines[i + m].strip() for m in range(1, look))
                        combined = f"{full_date} {date2} {rest} {middle} {year_match.group(2).strip()}"
                        combined = re.sub(r'\s+', ' ', combined).strip()
                        rebuilt.append(combined)
                        i += look + 1
                        found = True
                        break
            if found:
                continue

        rebuilt.append(lines[i])
        i += 1
    return rebuilt


def is_knockoff_transaction(desc_upper):
    knockoff_patterns = ["KNOCK-OFF", "KNOCK OFF", "KNOCKOFF"]
    for pattern in knockoff_patterns:
        if pattern in desc_upper:
            return True
    return False


def cancel_reversal_deposit_pairs(receipts, bounces):
    receipts_out = receipts.copy()
    bounces_out = bounces.copy()

    bounce_indices_to_remove = set()
    receipt_indices_to_remove = set()

    for bi, bounce in enumerate(bounces_out):
        if bi in bounce_indices_to_remove:
            continue
        b_date = parse_date(bounce["bounce_date"])
        b_amt = bounce["amount"]
        b_desc = bounce.get("desc", "")
        if not b_date or b_amt < RECEIPT_MIN_AMOUNT:
            continue
        if "REVERSAL" not in b_desc:
            continue

        for ri, receipt in enumerate(receipts_out):
            if ri in receipt_indices_to_remove:
                continue
            r_date = parse_date(receipt["receipt_date"])
            r_amt = receipt["amount"]
            if not r_date:
                continue
            if r_date == b_date and abs(r_amt - b_amt) < 1.0:
                bounce_indices_to_remove.add(bi)
                receipt_indices_to_remove.add(ri)
                break

    cleaned_receipts = [r for i, r in enumerate(receipts_out) if i not in receipt_indices_to_remove]
    cleaned_bounces = [b for i, b in enumerate(bounces_out) if i not in bounce_indices_to_remove]
    return cleaned_receipts, cleaned_bounces


def check_knockoff_is_billing_receipt(effective_date, credit, billings_so_far):
    if credit < RECEIPT_MIN_AMOUNT:
        return False
    ko_date = parse_date(effective_date)
    if not ko_date:
        return False
    for bill in billings_so_far:
        bill_date = parse_date(bill["due_date"])
        if not bill_date:
            continue
        if bill_date == ko_date and abs(bill["emi"] - credit) < 1.0:
            return True
    return False


def extract_first_page_info(pdf_path, logger=None):
    info = {
        "client_name": "",
        "loan_account": "",
        "loan_amount": 0,
        "emi_amount": 0,
        "loan_start_date": "",
        "loan_start_date_obj": None,
        "tenure": 0,
        "product": "NA",
        "repayment_frequency": "Monthly",
        "instl_start_date": "",
        "instl_end_date": "",
        "loan_status": "",
        "total_outstanding": 0,
    }

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return info

        for page_idx in range(min(2, len(pdf.pages))):
            page = pdf.pages[page_idx]
            text = page.extract_text()
            if not text:
                continue

            if logger and page_idx == 0:
                logger.log(f"Extracted page 1", "info")

            lines = text.split("\n")

            if not info["client_name"]:
                for line in lines:
                    line_strip = line.strip()
                    if "Issue Date" in line_strip:
                        name_part = line_strip.split("Issue Date")[0].strip()
                        if name_part and len(name_part) > 2:
                            if "L O A N" not in name_part and "LOAN ACCOUNT" not in name_part.upper():
                                info["client_name"] = name_part
                                break
                    if line_strip.startswith("Mr ") or line_strip.startswith("Mrs ") or line_strip.startswith("Ms "):
                        info["client_name"] = line_strip.replace("Mr ", "").replace("Mrs ", "").replace("Ms ", "").strip()

            if not info["loan_account"]:
                header_match = re.search(r'(?:STATEMENT\s+FOR|FOR)\s+([\d\s]+)', text)
                if header_match:
                    acct = header_match.group(1).replace(" ", "").strip()
                    if len(acct) >= 6:
                        info["loan_account"] = acct
                agr_match = re.search(r'Agreement\s*Id[:\s]+(\S+)', text)
                if agr_match and not info["loan_account"]:
                    info["loan_account"] = agr_match.group(1).strip()

            if info["loan_amount"] == 0:
                sanc_match = re.search(r'(\d[\d,]+\.\d{2})\s+(\d[\d,]+\.\d{2})\s+[\d.]+\s+[\d.]+', text)
                if sanc_match:
                    info["loan_amount"] = clean_amount(sanc_match.group(1))

            if info["tenure"] == 0:
                tenure_match = re.search(r'Tenure[:\s]+(\d+)\s*Months', text, re.IGNORECASE)
                if tenure_match:
                    info["tenure"] = int(tenure_match.group(1))

            if not info["instl_start_date"]:
                instl_start = re.search(r'Instl\.?\s*Start\s*Date[:\s]+(\d{2}-[A-Za-z]{3}-\d{4})', text, re.IGNORECASE)
                if instl_start:
                    info["instl_start_date"] = instl_start.group(1)

            if not info["instl_end_date"]:
                instl_end = re.search(r'Instl\.?\s*End\s*Date[:\s]+(\d{2}-[A-Za-z]{3}-\d{4})', text, re.IGNORECASE)
                if instl_end:
                    info["instl_end_date"] = instl_end.group(1)

            if not info["loan_start_date"]:
                loan_date_match = re.search(r'^(\d{2}-[A-Za-z]{3}-\d{4})\s+[\d,]+\.?\d*\s+[\d,]+\.?\d*\s+[\d.]+', text, re.MULTILINE)
                if loan_date_match:
                    info["loan_start_date"] = loan_date_match.group(1)
                    info["loan_start_date_obj"] = parse_date(loan_date_match.group(1))

            if info["product"] == "NA":
                prod_match = re.search(r'Product[:\s]+(.+?)(?:\n|Scheme|$)', text, re.IGNORECASE)
                if prod_match:
                    product_val = prod_match.group(1).strip()
                    if product_val:
                        pv_upper = product_val.upper()
                        if "COMMERCIAL VEHICLE" in pv_upper:
                            info["product"] = "CV"
                        elif "TWO WHEELER" in pv_upper or "2 WHEELER" in pv_upper:
                            info["product"] = "TW"
                        elif "PERSONAL" in pv_upper:
                            info["product"] = "PL"
                        elif "HOME" in pv_upper:
                            info["product"] = "HL"
                        elif "GOLD" in pv_upper:
                            info["product"] = "GL"
                        elif "BUSINESS" in pv_upper:
                            info["product"] = "BL"
                        elif "LAP" in pv_upper or "LOAN AGAINST PROPERTY" in pv_upper:
                            info["product"] = "LAP"
                        else:
                            info["product"] = product_val

            if info["repayment_frequency"] == "Monthly":
                rep_match = re.search(r'Repayment\s*Frequency[:\s]+(\S+)', text, re.IGNORECASE)
                if rep_match:
                    info["repayment_frequency"] = rep_match.group(1).strip()

            if not info["loan_status"]:
                status_match = re.search(r'Loan\s*Status[:\s]+(\S+)', text, re.IGNORECASE)
                if status_match:
                    info["loan_status"] = status_match.group(1).strip()

            if info["emi_amount"] == 0:
                paid_match = re.search(r'(\d+)/([\d,]+\.?\d*)\s+\d+/[\d,]+\.?\d*\s+\d+/[\d,]+\.?\d*', text)
                if paid_match:
                    total_paid_amount = clean_amount(paid_match.group(2))
                    paid_count = int(paid_match.group(1))
                    if paid_count > 0:
                        info["emi_amount"] = round(total_paid_amount / paid_count)
                        # Extract Total Outstanding
            if info.get("total_outstanding", 0) == 0:
                text_upper_local = text.upper()
                
                # Method 1: "Total Outstanding" header found, grab the last number in same row area
                if "TOTAL OUTSTANDING" in text_upper_local:
                    lines_local = text.split("\n")
                    for li, line in enumerate(lines_local):
                        if "TOTAL OUTSTANDING" in line.upper():
                            # Check this line and next few lines for numbers
                            search_zone = line
                            for offset in range(1, 5):
                                if li + offset < len(lines_local):
                                    search_zone += " " + lines_local[li + offset]
                            # Find all numbers in search zone
                            nums = re.findall(r'[\d,]+\.?\d*', search_zone)
                            # Filter out small numbers and pick the largest reasonable one
                            candidates = []
                            for n in nums:
                                val = clean_amount(n)
                                if val > 100:  # Must be > 100 to be outstanding
                                    candidates.append(val)
                            if candidates:
                                # The last large number is usually Total Outstanding
                                info["total_outstanding"] = candidates[-1]
                            break
                
                # Method 2: Look for the row pattern from page 1/2
                # Pattern: numbers in a row where last one is total outstanding
                if info.get("total_outstanding", 0) == 0:
                    # Match row like: -10050 3 1907 158.05 100624 1500 8370.16 104886.94
                    # or: 0.00 0.00 5821.00 458071.00 473658.16
                    row_pattern = re.findall(
                        r'^[\s]*(-?[\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)\s*(?:(-?[\d,]+\.?\d*)\s*)?(?:(-?[\d,]+\.?\d*)\s*)?(?:(-?[\d,]+\.?\d*)\s*)?$',
                        text, re.MULTILINE
                    )
                    for match in row_pattern:
                        # Get all non-empty values
                        vals = [clean_amount(v) for v in match if v.strip()]
                        if len(vals) >= 5:
                            last_val = vals[-1]
                            # Outstanding should be > 1000 or exactly 0
                            if last_val > 1000 or (last_val == 0 and len(vals) >= 5):
                                # Check it's not a loan amount row (those are much bigger)
                                if last_val < info.get("loan_amount", float('inf')) * 2:
                                    info["total_outstanding"] = last_val
                                    break

                # Method 3: Direct pattern "Total Outstanding (Rs.)" followed by number
                if info.get("total_outstanding", 0) == 0:
                    direct = re.search(
                        r'Total\s+Outstanding\s*\(?Rs\.?\)?\s*[\n\s]*([\d,]+\.?\d*)',
                        text, re.IGNORECASE
                    )
                    if direct:
                        val = clean_amount(direct.group(1))
                        if val > 0:
                            info["total_outstanding"] = val
    
    info["repayment_mode"] = "NACH"

    if logger:
        name_display = info["client_name"] if info["client_name"] else "Unknown"
        logger.log(f"Extracted page 1 — client: {name_display}", "info")
        logger.log(
            f"Loan amount: ₹{info['loan_amount']:,.0f} · "
            f"EMI: ₹{info['emi_amount']:,.0f} · "
            f"Tenure: {info['tenure']} mo",
            "info"
        )

    return info


def extract_transactions_smart(pdf_path, logger=None):
    billings = []
    receipts = []
    bounces = []
    foreclosure_receipts = []
    refunds = []
    pending_knockoffs = []
    in_ledger = False
    has_foreclosure = False
    junk_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
            text_upper = text.upper()
            if any(kw in text_upper for kw in ["LOAN CLOSURE", "FORECLOSURE", "PRECLOSURE",
                                                 "CLOSED ON", "LOAN STATUS: CLOSED"]):
                has_foreclosure = True

            if not in_ledger:
                has_customer = "CUSTOMER TRANSACTION DETAILS" in text_upper
                has_debtors = "DEBTORS TRANSACTION DETAILS" in text_upper
                has_loan_acct = "LOAN ACCOUNT TRANSACTION DETAILS" in text_upper

                if has_customer or has_debtors:
                    in_ledger = True
                elif has_loan_acct:
                    continue
                else:
                    continue

            lines = text.split("\n")

            needs_rebuild = False
            for l in lines:
                ls = l.strip()
                if re.match(r'^\d{2}-[A-Za-z]{3}\d{4}', ls):
                    needs_rebuild = True; break
                if re.match(r'^\d{2}-\s*$', ls):
                    needs_rebuild = True; break
                if re.match(r'^\d{2}-[A-Za-z]{3}-\s*$', ls):
                    needs_rebuild = True; break
                if re.match(r'^\d{2}-[A-Za-z]{3}-\s+\d{2}-[A-Za-z]{3}-', ls):
                    needs_rebuild = True; break
                if re.match(r'^\d{2}-\s+\d{2}-', ls):
                    needs_rebuild = True; break
                if re.match(r'^\d{2}-[A-Za-z]{3}-?\d{4}\s+\d{2}-[A-Za-z]{3}-\s', ls):
                    needs_rebuild = True; break

            if needs_rebuild:
                lines = rebuild_broken_lines(lines)

            i = 0
            while i < len(lines):
                line = lines[i]
                if re.search(DATE_LINE_START, line.strip()):
                    combined_text = line
                    for offset in range(1, 6):
                        if i + offset < len(lines):
                            next_line = lines[i + offset]
                            if re.search(DATE_LINE_START, next_line.strip()):
                                break
                            combined_text += " " + next_line
                    combined_upper = combined_text.upper()

                    all_dates_raw = re.findall(DATE_PATTERN_ANY, combined_text)
                    all_dates = [normalize_date_str(d) for d in all_dates_raw]

                    if len(all_dates) >= 2:
                        effective_date = all_dates[1]
                    elif len(all_dates) == 1:
                        effective_date = all_dates[0]
                    else:
                        effective_date = None

                    amount_matches = re.findall(r"[\d,]+\.\d{2}", combined_text)
                    amounts = [clean_amount(x) for x in amount_matches]
                    debit = 0.0
                    credit = 0.0

                    is_collection_bounced = "COLLECTION BOUNCED" in combined_upper

                    if len(amounts) >= 3:
                        debit = amounts[-3]
                        credit = amounts[-2]
                        if is_collection_bounced:
                            debit = max(amounts[:-1])
                            credit = 0.0
                    elif len(amounts) == 2:
                        if is_collection_bounced:
                            debit = amounts[0]
                            credit = 0.0
                        elif "RECEIPT" in combined_upper or "PAYMENT" in combined_upper:
                            credit = amounts[0]
                            debit = 0.0
                        elif "BILLING" in combined_upper or "INSTALLMENT" in combined_upper:
                            debit = amounts[0]
                            credit = 0.0
                        elif "BOUNCE" in combined_upper:
                            debit = amounts[0]
                            credit = 0.0
                        else:
                            debit = amounts[0]
                            credit = amounts[1]
                    elif len(amounts) == 1:
                        if is_collection_bounced:
                            debit = amounts[0]
                        elif "BILLING" in combined_upper or (
                            "BOUNCE" in combined_upper and "RECEIPT" not in combined_upper
                        ):
                            debit = amounts[0]
                        else:
                            credit = amounts[0]

                    first_line_upper = line.upper().strip()

                    if is_knockoff_transaction(first_line_upper):
                        if check_knockoff_is_billing_receipt(effective_date, credit, billings):
                            receipts.append({
                                "receipt_date": effective_date,
                                "amount": credit,
                                "desc": combined_upper
                            })
                            i += 1
                            continue
                        else:
                            if credit > 0 and effective_date:
                                pending_knockoffs.append({
                                    "date": effective_date,
                                    "amount": credit,
                                    "desc": combined_upper
                                })
                            i += 1
                            continue

                    is_refund = False
                    if "REFUND" in combined_upper:
                        if (debit >= RECEIPT_MIN_AMOUNT
                                and credit == 0.0
                                and "RECEIPT" not in first_line_upper):
                            is_refund = True
                            refunds.append({
                                "refund_date": effective_date,
                                "amount": debit,
                                "desc": combined_upper
                            })
                        elif ("RECEIPT" in first_line_upper
                              and credit >= RECEIPT_MIN_AMOUNT):
                            is_refund = True

                    if is_refund:
                        i += 1
                        continue

                    is_credit_receipt = (
                        credit >= RECEIPT_MIN_AMOUNT
                        and debit == 0.0
                        and "RECEIPT" in first_line_upper
                        and "REVERSAL" not in first_line_upper
                        and "COLLECTION BOUNCED" not in first_line_upper
                        and "BILLING" not in first_line_upper
                        and "INSTALLMENT" not in first_line_upper
                    )

                    is_bounce = False
                    bounce_debit = debit if debit > 0 else credit

                    is_billing_txn = (
                        debit > BILLING_MIN_AMOUNT
                        and ("BILLING" in combined_upper or "INSTALLMENT" in combined_upper)
                        and not is_collection_bounced
                        and credit == 0.0
                    )

                    if not is_credit_receipt:
                        if is_billing_txn:
                            if "BOUNCE" in first_line_upper:
                                if is_real_bounce_reversal(first_line_upper, bounce_debit):
                                    is_bounce = True
                        else:
                            if "BOUNCE" in combined_upper:
                                if is_real_bounce_reversal(combined_upper, bounce_debit):
                                    is_bounce = True

                        if not is_billing_txn:
                            if "REVERSAL" in combined_upper and "RECEIPT" in combined_upper:
                                if "INTEREST REVERSAL" not in combined_upper:
                                    is_bounce = True

                    if is_collection_bounced:
                        is_bounce = True

                    if (not is_bounce
                            and not is_credit_receipt
                            and "RECEIPT" in first_line_upper
                            and "BILLING" not in first_line_upper
                            and "INSTALLMENT" not in first_line_upper
                            and debit >= RECEIPT_MIN_AMOUNT
                            and credit == 0.0):
                        if "COLLECTION BOUNCED" in combined_upper:
                            is_bounce = True
                        else:
                            is_bounce = True

                    type_text = first_line_upper
                    if i + 1 < len(lines):
                        type_text += " " + lines[i + 1].upper().strip()
                    is_booking_type = ("BOOKING" in type_text or "DISBURSAL" in type_text)

                    if is_bounce:
                        bounce_amt = debit if debit > 0 else credit
                        if bounce_amt > 0:
                            bounces.append({
                                "bounce_date": effective_date,
                                "amount": bounce_amt,
                                "desc": combined_upper
                            })
                            if logger:
                                logger.log(
                                    f"[Bounce] {effective_date} — collection bounced ₹{bounce_amt:,.0f}",
                                    "bounce"
                                )
                    elif credit > 0:
                        if is_junk_receipt(combined_upper):
                            junk_count += 1
                        elif credit < RECEIPT_MIN_AMOUNT:
                            junk_count += 1
                        else:
                            receipts.append({
                                "receipt_date": effective_date,
                                "amount": credit,
                                "desc": combined_upper
                            })
                    elif debit > BILLING_MIN_AMOUNT and (
                        "BILLING" in combined_upper or "INSTALLMENT" in combined_upper
                    ) and not is_booking_type:
                        billings.append({
                            "due_date": effective_date,
                            "emi": debit,
                            "desc": combined_upper
                        })
                i += 1

    if pending_knockoffs and billings:
        ko_by_date = defaultdict(list)
        for ko in pending_knockoffs:
            ko_by_date[ko["date"]].append(ko)

        for ko_date, ko_list in ko_by_date.items():
            combined_credit = sum(ko["amount"] for ko in ko_list)
            if combined_credit < RECEIPT_MIN_AMOUNT:
                continue
            ko_date_obj = parse_date(ko_date)
            if not ko_date_obj:
                continue
            for bill in billings:
                bill_date = parse_date(bill["due_date"])
                if not bill_date:
                    continue
                if bill_date == ko_date_obj and abs(bill["emi"] - combined_credit) < 1.0:
                    receipts.append({
                        "receipt_date": ko_date,
                        "amount": combined_credit,
                        "desc": "KNOCK-OFF GROUPED: " + " + ".join(
                            f"{ko['amount']:.2f}" for ko in ko_list)
                    })
                    break

    if receipts and bounces:
        receipts, bounces = cancel_reversal_deposit_pairs(receipts, bounces)

    if has_foreclosure and receipts and billings:
        avg_emi = sum(b["emi"] for b in billings) / len(billings)
        receipts_sorted = sorted(receipts, key=lambda x: parse_date(x["receipt_date"]) or datetime.min)
        to_remove = []
        for idx in range(len(receipts_sorted) - 1, max(len(receipts_sorted) - 3, -1), -1):
            r = receipts_sorted[idx]
            if r["amount"] > avg_emi * 1.5:
                foreclosure_receipts.append(r)
                to_remove.append(r)
        for r in to_remove:
            receipts.remove(r)

    if refunds:
        receipts = remove_refunded_receipts(receipts, refunds)

    if logger:
        logger.log(f"Found {len(billings)} billing rows, {len(receipts)} receipts", "info")
        if junk_count > 0:
            logger.log(f"Junk filtered: {junk_count} rows (fees, GST, interest)", "info")

    return billings, receipts, bounces, foreclosure_receipts


def remove_duplicates_and_clean(billings):
    unique_bills = {}
    for b in billings:
        dt = parse_date(b["due_date"])
        if not dt:
            continue
        key = dt.strftime("%Y-%m")
        if key not in unique_bills:
            unique_bills[key] = b
        else:
            if b["emi"] > unique_bills[key]["emi"]:
                unique_bills[key] = b
    return list(unique_bills.values())


def remove_bounced_receipts_smart(receipts, bounces):
    valid_receipts = receipts.copy()
    valid_receipts.sort(key=lambda x: parse_date(x["receipt_date"]) or datetime.min)
    indices_to_remove = set()

    bounces_sorted = sorted(bounces, key=lambda x: parse_date(x["bounce_date"]) or datetime.min)

    for bounce in bounces_sorted:
        b_date = parse_date(bounce["bounce_date"])
        b_amt = bounce["amount"]
        if not b_date:
            continue
        if b_amt < RECEIPT_MIN_AMOUNT:
            continue

        best_candidate_idx = -1
        best_score = (999, 999)

        for i, r in enumerate(valid_receipts):
            if i in indices_to_remove:
                continue
            r_date = parse_date(r["receipt_date"])
            if not r_date:
                continue
            amount_diff = abs(r["amount"] - b_amt)
            if amount_diff > 1.0:
                continue
            day_diff = abs((b_date - r_date).days)
            if day_diff > 15:
                continue
            if r_date > b_date:
                continue

            score = (0, day_diff)
            if score < best_score:
                best_score = score
                best_candidate_idx = i

        if best_candidate_idx != -1:
            indices_to_remove.add(best_candidate_idx)

    return [r for i, r in enumerate(valid_receipts) if i not in indices_to_remove]


def remove_refunded_receipts(receipts, refunds):
    valid_receipts = receipts.copy()
    valid_receipts.sort(key=lambda x: parse_date(x["receipt_date"]) or datetime.min)
    indices_to_remove = set()

    refunds_sorted = sorted(refunds, key=lambda x: parse_date(x["refund_date"]) or datetime.min)

    for refund in refunds_sorted:
        r_date = parse_date(refund["refund_date"])
        r_amt = refund["amount"]
        if not r_date:
            continue

        best_candidate_idx = -1

        for i in range(len(valid_receipts) - 1, -1, -1):
            if i in indices_to_remove:
                continue
            rcpt = valid_receipts[i]
            rcpt_date = parse_date(rcpt["receipt_date"])
            if not rcpt_date:
                continue
            amount_diff = abs(rcpt["amount"] - r_amt)
            if amount_diff > 1.0:
                continue
            day_diff = (r_date - rcpt_date).days
            if day_diff < 0 or day_diff > 30:
                continue
            best_candidate_idx = i
            break

        if best_candidate_idx != -1:
            indices_to_remove.add(best_candidate_idx)

    return [r for i, r in enumerate(valid_receipts) if i not in indices_to_remove]


def process_logic(billings, receipts, bounces, foreclosure_receipts, logger=None):
    billings = remove_duplicates_and_clean(billings)
    billings.sort(key=lambda x: parse_date(x["due_date"]) or datetime.min)

    clean_receipts = remove_bounced_receipts_smart(receipts, bounces)
    clean_receipts.sort(key=lambda x: parse_date(x["receipt_date"]) or datetime.min)

    receipt_pool = []
    for r in clean_receipts:
        r_date = parse_date(r["receipt_date"])
        if r_date and r["amount"] > 0.01:
            receipt_pool.append({
                "date_str": r["receipt_date"],
                "date_obj": r_date,
                "amount": r["amount"],
            })

    bill_list = []
    for bill in billings:
        due_date = parse_date(bill["due_date"])
        if due_date:
            bill_list.append({
                "due_date_str": bill["due_date"],
                "due_date_obj": due_date,
                "emi": bill["emi"],
                "deposit_amount": 0.0,
                "deposit_date_str": "",
                "deposit_date_obj": None,
                "receipts": [],
                "receipt_details": [],
                "date_distributed": False,
            })

    if not bill_list:
        return [], foreclosure_receipts, clean_receipts

    cumulative_emis = []
    cum = 0.0
    for bl in bill_list:
        cum += bl["emi"]
        cumulative_emis.append(cum)

    def find_window_bill(r_date):
        for bill_idx in range(len(bill_list)):
            if bill_idx == 0:
                w_start = datetime(2000, 1, 1)
            else:
                w_start = bill_list[bill_idx]["due_date_obj"]
            if bill_idx + 1 < len(bill_list):
                w_end = bill_list[bill_idx + 1]["due_date_obj"] - timedelta(days=1)
            else:
                w_end = datetime(2099, 12, 31)
            if w_start <= r_date <= w_end:
                return bill_idx
        if bill_list and r_date < bill_list[0]["due_date_obj"]:
            return 0
        return None

    cum_receipts_so_far = 0.0
    final_assignments = {}
    bill_assigned_amounts = [0.0] * len(bill_list)

    for rp_idx, rp in enumerate(receipt_pool):
        window_bill = find_window_bill(rp["date_obj"])
        if window_bill is None:
            continue

        cum_emis_to_bill = cumulative_emis[window_bill]
        emi_of_window = bill_list[window_bill]["emi"]

        is_advance = False

        if cum_receipts_so_far >= cum_emis_to_bill - 0.01:
            if rp["amount"] >= emi_of_window * 0.50:
                is_advance = True

        if not is_advance:
            if (bill_assigned_amounts[window_bill] >= bill_list[window_bill]["emi"] - 0.01
                    and abs(rp["amount"] - bill_list[window_bill]["emi"]) < 1.0):
                prev_fulfilled = True
                if window_bill > 0:
                    prev_idx = window_bill - 1
                    if bill_assigned_amounts[prev_idx] < bill_list[prev_idx]["emi"] - 0.01:
                        prev_fulfilled = False
                if prev_fulfilled:
                    is_advance = True

        if is_advance:
            if bill_assigned_amounts[window_bill] < bill_list[window_bill]["emi"] * 0.50:
                is_advance = False

        if is_advance:
            target = window_bill + 1
            if target < len(bill_list):
                final_assignments[rp_idx] = target
                bill_assigned_amounts[target] += rp["amount"]
            else:
                final_assignments[rp_idx] = window_bill
                bill_assigned_amounts[window_bill] += rp["amount"]
        else:
            final_assignments[rp_idx] = window_bill
            bill_assigned_amounts[window_bill] += rp["amount"]

        cum_receipts_so_far += rp["amount"]

    for rp_idx, bill_idx in final_assignments.items():
        rp = receipt_pool[rp_idx]
        bl = bill_list[bill_idx]
        bl["deposit_amount"] += rp["amount"]
        bl["receipts"].append(f"{rp['amount']:,.0f}({rp['date_str']})")
        bl["receipt_details"].append({
            "amount": rp["amount"],
            "date_str": rp["date_str"],
            "date_obj": rp["date_obj"],
        })

    # Date+Amount distribution to previous unpaid bills
    for bill_idx in range(1, len(bill_list)):
        bl = bill_list[bill_idx]
        if len(bl["receipt_details"]) < 2:
            continue

        bl["receipt_details"].sort(key=lambda x: x["date_obj"])

        unpaid_prev_indices = []
        check_idx = bill_idx - 1
        while check_idx >= 0:
            prev_bl = bill_list[check_idx]
            if (prev_bl["deposit_amount"] < prev_bl["emi"] * 0.01
                    and prev_bl["deposit_date_obj"] is None):
                unpaid_prev_indices.insert(0, check_idx)
                check_idx -= 1
            else:
                break

        if not unpaid_prev_indices:
            continue

        if len(unpaid_prev_indices) > 2:
            continue

        total_prev_emi_needed = sum(
            bill_list[pi]["emi"] for pi in unpaid_prev_indices
        )

        min_needed = total_prev_emi_needed + bl["emi"] * 0.50
        if bl["deposit_amount"] < min_needed:
            continue

        moved_rd_indices = set()

        for prev_idx in unpaid_prev_indices:
            prev_bl = bill_list[prev_idx]

            cumulative_for_prev = 0.0
            indices_for_this_prev = []

            for rd_idx, rd in enumerate(bl["receipt_details"]):
                if rd_idx in moved_rd_indices:
                    continue

                remaining_count = (len(bl["receipt_details"])
                                   - len(moved_rd_indices) - 1)
                if remaining_count < 1:
                    break

                already_moved_amount = sum(
                    bl["receipt_details"][mi]["amount"] for mi in moved_rd_indices
                )
                remaining_amount = (bl["deposit_amount"]
                                    - already_moved_amount - rd["amount"])
                if remaining_amount < bl["emi"] * 0.50:
                    break

                cumulative_for_prev += rd["amount"]
                indices_for_this_prev.append(rd_idx)

                if cumulative_for_prev >= prev_bl["emi"] * 0.50:
                    break

            if not indices_for_this_prev:
                continue

            for rd_idx in indices_for_this_prev:
                rd = bl["receipt_details"][rd_idx]
                move_amount = rd["amount"]

                prev_bl["deposit_amount"] += move_amount
                prev_bl["deposit_date_str"] = rd["date_str"]
                prev_bl["deposit_date_obj"] = rd["date_obj"]
                prev_bl["receipts"].append(f"{move_amount:,.0f}({rd['date_str']})")
                prev_bl["date_distributed"] = True

                bl["deposit_amount"] -= move_amount
                rcpt_str_to_remove = f"{move_amount:,.0f}({rd['date_str']})"
                if rcpt_str_to_remove in bl["receipts"]:
                    bl["receipts"].remove(rcpt_str_to_remove)

                moved_rd_indices.add(rd_idx)

        if moved_rd_indices:
            first_staying = None
            for rd_idx, rd in enumerate(bl["receipt_details"]):
                if rd_idx not in moved_rd_indices:
                    first_staying = rd
                    break

            if first_staying:
                bl["deposit_date_str"] = first_staying["date_str"]
                bl["deposit_date_obj"] = first_staying["date_obj"]
                bl["date_distributed"] = True

    for rp_idx, bill_idx in final_assignments.items():
        rp = receipt_pool[rp_idx]
        bl = bill_list[bill_idx]
        if bl["date_distributed"]:
            continue
        if bl["deposit_date_obj"] is None:
            bl["deposit_date_str"] = rp["date_str"]
            bl["deposit_date_obj"] = rp["date_obj"]
        elif bl["deposit_amount"] - rp["amount"] < bl["emi"] - 0.01:
            bl["deposit_date_str"] = rp["date_str"]
            bl["deposit_date_obj"] = rp["date_obj"]

    all_receipts_sorted = sorted(receipt_pool, key=lambda x: x["date_obj"])

    for bill_idx, bl in enumerate(bill_list):
        deposit_amount = bl["deposit_amount"]
        if deposit_amount >= bl["emi"] - 0.01:
            bl["status"] = "Paid"
        elif deposit_amount > 0.01:
            bl["status"] = "Partial"
        else:
            bl["status"] = "Unpaid"

    for bill_idx, bl in enumerate(bill_list):
        if bl["deposit_date_obj"] is not None:
            continue
        due = bl["due_date_obj"]
        for dep in all_receipts_sorted:
            if dep["date_obj"] >= due:
                bl["deposit_date_str"] = dep["date_str"]
                bl["deposit_date_obj"] = dep["date_obj"]
                break

    for bill_idx in range(len(bill_list) - 2, -1, -1):
        current = bill_list[bill_idx]
        next_bill = bill_list[bill_idx + 1]
        if (current["deposit_date_obj"] is not None
                and next_bill["deposit_date_obj"] is not None
                and current["deposit_date_obj"] > next_bill["deposit_date_obj"]):
            current["deposit_date_obj"] = next_bill["deposit_date_obj"]
            current["deposit_date_str"] = next_bill["deposit_date_str"]

    output = []
    sr_no = 1

    for bill_idx, bl in enumerate(bill_list):
        due_date_obj = bl["due_date_obj"]
        emi = bl["emi"]
        deposit_amount = bl["deposit_amount"]
        deposit_date_obj = bl["deposit_date_obj"]
        status = bl["status"]

        dpd = 0
        if deposit_date_obj and due_date_obj:
            dpd = (deposit_date_obj - due_date_obj).days
            if dpd < 0:
                dpd = 0
        elif status in ("Unpaid", "Partial") and due_date_obj:
            dpd = (datetime.now() - due_date_obj).days
            if dpd < 0:
                dpd = 0

        # Determine display status
        display_status = status
        if status == "Paid" and dpd > 0:
            display_status = "Late"
        elif status == "Unpaid":
            # Check if there was a bounce for this EMI period
            for bounce in bounces:
                b_date = parse_date(bounce["bounce_date"])
                if b_date and due_date_obj:
                    diff = abs((b_date - due_date_obj).days)
                    if diff <= 15 and abs(bounce["amount"] - emi) < 1.0:
                        display_status = "Bounced"
                        break

        output.append({
            "Sr No": sr_no,
            "Due Date": due_date_obj,
            "EMI": round(emi),
            "Dep. Date": deposit_date_obj,
            "Dep. Amt": round(deposit_amount),
            "DPD": dpd,
            "Status": display_status,
        })
        sr_no += 1

    return output, foreclosure_receipts, clean_receipts


def generate_all_months(start_date, end_date):
    months = []
    current = datetime(start_date.year, start_date.month, 1)
    end_first = datetime(end_date.year, end_date.month, 1)
    while current <= end_first:
        months.append(current.strftime("%Y-%m"))
        current += relativedelta(months=1)
    return months


def process_pdf(pdf_file, logger=None):
    """
    Main entry point for processing a PDF file.
    Returns a dict with all results needed by the UI.
    """
    if logger is None:
        logger = ParseLogger()

    page1_info = extract_first_page_info(pdf_file, logger)

    billings, receipts, bounces, foreclosure_receipts = extract_transactions_smart(pdf_file, logger)

    if not billings and not receipts:
        logger.log("No data found in PDF", "error")
        return {
            "page1_info": page1_info,
            "final_data": [],
            "foreclosure_receipts": [],
            "clean_receipts": [],
            "bounces": bounces,
            "billings": billings,
            "logger": logger,
            "total_bounces": len(bounces),
            "total_receipts_amount": 0,
        }

    final_data, fc_receipts, clean_receipts = process_logic(
        billings, receipts, bounces, foreclosure_receipts, logger
    )

    if page1_info["emi_amount"] == 0 and billings:
        page1_info["emi_amount"] = round(billings[0]["emi"])

    if page1_info["tenure"] == 0:
        page1_info["tenure"] = len(final_data)

    total_receipts_amount = sum(r["amount"] for r in clean_receipts)

    logger.log("Done — writing to Excel", "success")

    return {
        "page1_info": page1_info,
        "final_data": final_data,
        "foreclosure_receipts": fc_receipts,
        "clean_receipts": clean_receipts,
        "bounces": bounces,
        "billings": billings,
        "logger": logger,
        "total_bounces": len(bounces),
        "total_receipts_amount": total_receipts_amount,
    }


def generate_excel(result):
    """Generate Excel file in memory and return bytes."""
    output_buffer = io.BytesIO()

    final_data = result["final_data"]
    page1_info = result["page1_info"]
    fc_receipts = result["foreclosure_receipts"]
    clean_receipts = result["clean_receipts"]
    billings = result["billings"]
    bounces = result["bounces"]

    total_bounces = len(bounces)
    total_emis = len(final_data)

    # Build monthly receipt groups
    monthly_groups = defaultdict(list)
    for r in clean_receipts:
        r_date = parse_date(r["receipt_date"])
        if not r_date:
            continue
        key = r_date.strftime("%Y-%m")
        monthly_groups[key].append({
            "date_obj": r_date,
            "amount": r["amount"],
        })

    for key in monthly_groups:
        monthly_groups[key].sort(key=lambda x: x["date_obj"])

    all_dates_for_range = []
    for b in billings:
        bd = parse_date(b["due_date"])
        if bd:
            all_dates_for_range.append(bd)
    for r in clean_receipts:
        rd = parse_date(r["receipt_date"])
        if rd:
            all_dates_for_range.append(rd)

    if all_dates_for_range:
        range_start = min(all_dates_for_range)
        range_end = max(all_dates_for_range)
        all_month_keys = generate_all_months(range_start, range_end)
    else:
        all_month_keys = sorted(monthly_groups.keys())

    writer = pd.ExcelWriter(output_buffer, engine="xlsxwriter")
    workbook = writer.book

    # ===================== SHEET 1: EMI REPORT =====================
    worksheet = workbook.add_worksheet("EMI Report")
    writer.sheets["EMI Report"] = worksheet

    # Formats
    lbl_fmt = workbook.add_format({
        "bold": True, "font_size": 10, "align": "left",
        "valign": "vcenter", "border": 1, "bg_color": "#D9E1F2",
    })
    val_fmt = workbook.add_format({
        "font_size": 10, "align": "left",
        "valign": "vcenter", "border": 1,
    })
    val_num_fmt = workbook.add_format({
        "font_size": 10, "align": "left", "num_format": "#,##0",
        "valign": "vcenter", "border": 1,
    })
    val_date_fmt_header = workbook.add_format({
        "font_size": 10, "align": "left", "num_format": "DD/MMM/YYYY",
        "valign": "vcenter", "border": 1,
    })
    rlbl_fmt = workbook.add_format({
        "bold": True, "font_size": 10, "align": "left",
        "valign": "vcenter", "border": 1, "bg_color": "#E2EFDA",
    })
    rval_fmt = workbook.add_format({
        "font_size": 10, "align": "left",
        "valign": "vcenter", "border": 1,
    })
    rval_num_fmt = workbook.add_format({
        "font_size": 10, "align": "left", "num_format": "#,##0",
        "valign": "vcenter", "border": 1,
    })
    rval_dec_fmt = workbook.add_format({
        "font_size": 10, "align": "left", "num_format": "0.0",
        "valign": "vcenter", "border": 1,
    })
    tbl_hdr_fmt = workbook.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "valign": "vcenter", "border": 1,
        "text_wrap": True, "font_size": 10,
    })
    center_fmt = workbook.add_format({"align": "center", "border": 1, "font_size": 10})
    date_fmt = workbook.add_format({"num_format": "DD. MMM. YYYY", "align": "center", "border": 1, "font_size": 10})
    money_fmt = workbook.add_format({"num_format": "#,##0", "align": "right", "border": 1, "font_size": 10})
    pct_fmt = workbook.add_format({"num_format": "0%", "align": "center", "border": 1, "font_size": 10})
    int_fmt = workbook.add_format({"num_format": "0", "align": "center", "border": 1, "font_size": 10})

    fc_fmt = workbook.add_format({
        "align": "center", "border": 1, "bg_color": "#FF6600",
        "font_color": "white", "bold": True, "font_size": 10,
    })
    fc_money_fmt = workbook.add_format({
        "num_format": "#,##0", "align": "right", "border": 1,
        "bg_color": "#FF6600", "font_color": "white", "bold": True, "font_size": 10,
    })
    fc_date_fmt = workbook.add_format({
        "num_format": "DD. MMM. YYYY", "align": "center", "border": 1,
        "bg_color": "#FF6600", "font_color": "white", "bold": True, "font_size": 10,
    })

    DATA_START_ROW = 12
    LAST_DATA_ROW = DATA_START_ROW + total_emis - 1

    first_data_1idx = DATA_START_ROW + 1
    last_data_1idx = LAST_DATA_ROW + 1
    last_12_start_1idx = max(first_data_1idx, last_data_1idx - 11)

    # Left side header
    header_left = [
        ("Client Name", page1_info["client_name"], "text"),
        ("Financier name", "MANAPPURAM FINANCE LIMITED", "text"),
        ("Asset Financed", "NA", "text"),
        ("Type of Finance", page1_info["product"], "text"),
        ("Repayment Mode", "NACH", "text"),
        ("Rgn No", "NA", "text"),
        ("Loan a/c no.", page1_info["loan_account"], "text"),
        ("Loan Amount", page1_info["loan_amount"], "number"),
        ("Total Receivable", "=D10*I1", "formula"),
        ("EMI Amt", page1_info["emi_amount"], "number"),
        ("Loan Start Date", page1_info["loan_start_date"], "date"),
    ]

    for row_idx, (label, value, vtype) in enumerate(header_left):
        worksheet.merge_range(row_idx, 0, row_idx, 2, label, lbl_fmt)
        if vtype == "formula":
            worksheet.merge_range(row_idx, 3, row_idx, 4, "", val_num_fmt)
            worksheet.write_formula(row_idx, 3, value, val_num_fmt)
        elif vtype == "number":
            worksheet.merge_range(row_idx, 3, row_idx, 4, value, val_num_fmt)
        elif vtype == "date":
            dt_obj = parse_date(value) if isinstance(value, str) else None
            if dt_obj:
                worksheet.merge_range(row_idx, 3, row_idx, 4, "", val_date_fmt_header)
                worksheet.write_datetime(row_idx, 3, dt_obj, val_date_fmt_header)
            else:
                worksheet.merge_range(row_idx, 3, row_idx, 4, value, val_fmt)
        else:
            worksheet.merge_range(row_idx, 3, row_idx, 4, value, val_fmt)

    # Right side header
    header_right = [
        ("Total EMI", page1_info["tenure"], "number"),
        ("No. of EMI Paid", total_emis, "number"),
        ("Balance Tenor", "=I1-I2", "formula"),
        ("Total Delay", f"=SUM(I{first_data_1idx}:I{last_data_1idx})", "formula_num"),
        ("Peak Delay", f"=MAX(I{first_data_1idx}:I{last_data_1idx})", "formula_num"),
        ("AVG Delay", f"=AVERAGE(I{first_data_1idx}:I{last_data_1idx})", "formula_dec"),
        ("Track Status", '=IF(OR(I5>60,I6>12),"PTR",IF(I5>30,"GTR",IF(I5<=30,"ETR")))', "formula_text"),
        ("Total no. of EMI Bounces", total_bounces, "number"),
        ("Total AMT Paid", f"=SUM(E{first_data_1idx}:E{last_data_1idx})", "formula_num"),
        ("Last 12 Mnth Peak Delay", f"=MAX(I{last_12_start_1idx}:I{last_data_1idx})", "formula_num"),
        ("Last 12 Mnth AVG Delay", f"=AVERAGE(I{last_12_start_1idx}:I{last_data_1idx})", "formula_dec"),
    ]

    for row_idx, (label, value, vtype) in enumerate(header_right):
        worksheet.merge_range(row_idx, 5, row_idx, 7, label, rlbl_fmt)
        if vtype == "number":
            worksheet.write_number(row_idx, 8, value, rval_num_fmt)
        elif vtype == "formula":
            worksheet.write_formula(row_idx, 8, value, rval_fmt)
        elif vtype == "formula_num":
            worksheet.write_formula(row_idx, 8, value, rval_num_fmt)
        elif vtype == "formula_dec":
            worksheet.write_formula(row_idx, 8, value, rval_dec_fmt)
        elif vtype == "formula_text":
            worksheet.write_formula(row_idx, 8, value, rval_fmt)
        else:
            worksheet.write(row_idx, 8, str(value), rval_fmt)

    # Table headers
    HEADER_ROW = 11
    col_headers = [
        "Sr. No.", "Due Date", "EMI", "Dep. Date", "Dep. Amt",
        "% of EMI Amount", "Short By / Excess By", "Balance", "Delay By"
    ]
    for col_idx, header in enumerate(col_headers):
        worksheet.write(HEADER_ROW, col_idx, header, tbl_hdr_fmt)

    # Data rows
    for row_idx, row_data in enumerate(final_data):
        excel_row = DATA_START_ROW + row_idx
        r = excel_row + 1

        worksheet.write_number(excel_row, 0, row_data["Sr No"], center_fmt)

        due_val = row_data["Due Date"]
        if due_val:
            worksheet.write_datetime(excel_row, 1, due_val, date_fmt)
        else:
            worksheet.write_blank(excel_row, 1, None, date_fmt)

        worksheet.write_number(excel_row, 2, row_data["EMI"], money_fmt)

        dep_date_val = row_data["Dep. Date"]
        if dep_date_val:
            worksheet.write_datetime(excel_row, 3, dep_date_val, date_fmt)
        else:
            worksheet.write_blank(excel_row, 3, None, date_fmt)

        worksheet.write_number(excel_row, 4, row_data["Dep. Amt"], money_fmt)

        worksheet.write_formula(excel_row, 5, f"=E{r}/C{r}", pct_fmt)
        worksheet.write_formula(excel_row, 6, f'=IF(C{r}="","",C{r}-E{r})', money_fmt)

        if row_idx % 2 == 0:
            worksheet.write_formula(excel_row, 7, f"=G{r}", money_fmt)
        else:
            worksheet.write_formula(excel_row, 7, f'=IF(C{r}="","",H{r - 1}+G{r})', money_fmt)

        formula_delay = (
            f'=IF(D{r}="",'
            f'IF(B{r}="",0,MAX(0,TODAY()-B{r})),'
            f'MAX(0,D{r}-B{r}))'
        )
        worksheet.write_formula(excel_row, 8, formula_delay, int_fmt)

    # Foreclosure rows
    if fc_receipts:
        fc_start_row = LAST_DATA_ROW + 2
        for fc in fc_receipts:
            fc_date = parse_date(fc["receipt_date"])
            fc_amount = fc["amount"]
            worksheet.write_string(fc_start_row, 0, "", fc_fmt)
            if fc_date:
                worksheet.write_datetime(fc_start_row, 1, fc_date, fc_date_fmt)
            else:
                worksheet.write_string(fc_start_row, 1, "", fc_fmt)
            worksheet.write_string(fc_start_row, 2, "", fc_fmt)
            if fc_date:
                worksheet.write_datetime(fc_start_row, 3, fc_date, fc_date_fmt)
            else:
                worksheet.write_string(fc_start_row, 3, "", fc_fmt)
            worksheet.write_number(fc_start_row, 4, round(fc_amount), fc_money_fmt)
            worksheet.write_string(fc_start_row, 5, "", fc_fmt)
            worksheet.write_string(fc_start_row, 6, "", fc_fmt)
            worksheet.write_string(fc_start_row, 7, "", fc_fmt)
            worksheet.write_string(fc_start_row, 8, "Foreclosed", fc_fmt)
            fc_start_row += 1

    # Column widths
    worksheet.set_column(0, 0, 8)
    worksheet.set_column(1, 1, 16)
    worksheet.set_column(2, 2, 12)
    worksheet.set_column(3, 3, 16)
    worksheet.set_column(4, 4, 12)
    worksheet.set_column(5, 5, 16)
    worksheet.set_column(6, 6, 20)
    worksheet.set_column(7, 7, 12)
    worksheet.set_column(8, 8, 12)
    worksheet.set_row(HEADER_ROW, 30)

    # ===================== SHEET 2: RECEIPTS SUMMARY =====================
    ws_rcpt = workbook.add_worksheet("Receipts Summary")
    writer.sheets["Receipts Summary"] = ws_rcpt

    rcpt_title_fmt = workbook.add_format({
        "bold": True, "font_size": 14, "align": "center",
        "valign": "vcenter", "border": 0, "font_color": "#1F4E79",
    })
    rcpt_hdr_fmt = workbook.add_format({
        "bold": True, "bg_color": "#2E75B6", "font_color": "white",
        "align": "center", "valign": "vcenter", "border": 1,
        "text_wrap": True, "font_size": 10,
    })
    rcpt_month_fmt = workbook.add_format({
        "font_size": 10, "align": "center", "valign": "vcenter",
        "border": 1, "bold": True, "bg_color": "#D6E4F0",
    })
    rcpt_date_fmt = workbook.add_format({
        "num_format": "DD-MMM-YYYY", "align": "center",
        "border": 1, "font_size": 10,
    })
    rcpt_money_fmt = workbook.add_format({
        "num_format": "#,##0", "align": "right",
        "border": 1, "font_size": 10,
    })
    rcpt_sr_fmt = workbook.add_format({
        "align": "center", "border": 1, "font_size": 10,
    })
    rcpt_nil_month_fmt = workbook.add_format({
        "font_size": 10, "align": "center", "valign": "vcenter",
        "border": 1, "bold": True, "bg_color": "#FCE4D6",
        "font_color": "#C00000",
    })
    rcpt_nil_text_fmt = workbook.add_format({
        "font_size": 10, "align": "center", "valign": "vcenter",
        "border": 1, "italic": True, "bg_color": "#FCE4D6",
        "font_color": "#C00000",
    })
    rcpt_nil_amt_fmt = workbook.add_format({
        "font_size": 10, "align": "right", "valign": "vcenter",
        "border": 1, "num_format": "#,##0", "bg_color": "#FCE4D6",
        "font_color": "#C00000",
    })
    rcpt_total_lbl_fmt = workbook.add_format({
        "bold": True, "font_size": 10, "align": "center",
        "valign": "vcenter", "border": 1, "bg_color": "#FFF2CC",
    })
    rcpt_total_amt_fmt = workbook.add_format({
        "bold": True, "font_size": 10, "num_format": "#,##0",
        "align": "right", "valign": "vcenter", "border": 1,
        "bg_color": "#FFF2CC",
    })
    rcpt_total_count_fmt = workbook.add_format({
        "bold": True, "font_size": 10, "align": "center",
        "valign": "vcenter", "border": 1, "bg_color": "#FFF2CC",
    })
    rcpt_grand_lbl_fmt = workbook.add_format({
        "bold": True, "font_size": 11, "align": "center",
        "valign": "vcenter", "border": 2, "bg_color": "#2E75B6",
        "font_color": "white",
    })
    rcpt_grand_amt_fmt = workbook.add_format({
        "bold": True, "font_size": 11, "num_format": "#,##0",
        "align": "right", "valign": "vcenter", "border": 2,
        "bg_color": "#2E75B6", "font_color": "white",
    })
    rcpt_grand_count_fmt = workbook.add_format({
        "bold": True, "font_size": 11, "align": "center",
        "valign": "vcenter", "border": 2, "bg_color": "#2E75B6",
        "font_color": "white",
    })

    ws_rcpt.merge_range(0, 0, 0, 4, "ALL RECEIPTS - MONTH / YEAR WISE SUMMARY", rcpt_title_fmt)

    rcpt_headers = ["Sr. No.", "Month / Year", "Receipt Date", "Amount (₹)", "Receipts in Month"]
    for col_idx, hdr in enumerate(rcpt_headers):
        ws_rcpt.write(2, col_idx, hdr, rcpt_hdr_fmt)

    row = 3
    sr = 1
    grand_total = 0.0
    grand_count = 0

    for month_key in all_month_keys:
        mk_date = datetime.strptime(month_key, "%Y-%m")
        month_label = mk_date.strftime("%b-%Y")

        if month_key in monthly_groups:
            rcpts_in_month = monthly_groups[month_key]
            month_total = sum(r["amount"] for r in rcpts_in_month)
            month_count = len(rcpts_in_month)

            first_row_of_month = row

            for idx, rcpt in enumerate(rcpts_in_month):
                ws_rcpt.write_number(row, 0, sr, rcpt_sr_fmt)
                if idx == 0 and month_count == 1:
                    ws_rcpt.write_string(row, 1, month_label, rcpt_month_fmt)
                ws_rcpt.write_datetime(row, 2, rcpt["date_obj"], rcpt_date_fmt)
                ws_rcpt.write_number(row, 3, rcpt["amount"], rcpt_money_fmt)
                if idx == 0 and month_count == 1:
                    ws_rcpt.write_number(row, 4, month_count, rcpt_sr_fmt)
                sr += 1
                row += 1

            if month_count > 1:
                last_row_of_month = row - 1
                ws_rcpt.merge_range(first_row_of_month, 1, last_row_of_month, 1,
                                    month_label, rcpt_month_fmt)
                ws_rcpt.merge_range(first_row_of_month, 4, last_row_of_month, 4,
                                    month_count, rcpt_sr_fmt)

            ws_rcpt.write_string(row, 0, "", rcpt_total_lbl_fmt)
            ws_rcpt.write_string(row, 1, f"Total ({month_label})", rcpt_total_lbl_fmt)
            ws_rcpt.write_string(row, 2, "", rcpt_total_lbl_fmt)
            ws_rcpt.write_number(row, 3, month_total, rcpt_total_amt_fmt)
            ws_rcpt.write_number(row, 4, month_count, rcpt_total_count_fmt)
            row += 1

            grand_total += month_total
            grand_count += month_count
        else:
            ws_rcpt.write_string(row, 0, "-", rcpt_nil_month_fmt)
            ws_rcpt.write_string(row, 1, month_label, rcpt_nil_month_fmt)
            ws_rcpt.write_string(row, 2, "No Receipt", rcpt_nil_text_fmt)
            ws_rcpt.write_number(row, 3, 0, rcpt_nil_amt_fmt)
            ws_rcpt.write_number(row, 4, 0, rcpt_nil_month_fmt)
            row += 1

    row += 1
    ws_rcpt.merge_range(row, 0, row, 2, "GRAND TOTAL", rcpt_grand_lbl_fmt)
    ws_rcpt.write_number(row, 3, grand_total, rcpt_grand_amt_fmt)
    ws_rcpt.write_number(row, 4, grand_count, rcpt_grand_count_fmt)

    ws_rcpt.set_column(0, 0, 8)
    ws_rcpt.set_column(1, 1, 16)
    ws_rcpt.set_column(2, 2, 16)
    ws_rcpt.set_column(3, 3, 15)
    ws_rcpt.set_column(4, 4, 18)
    ws_rcpt.set_row(0, 25)
    ws_rcpt.set_row(2, 22)

    writer.close()
    output_buffer.seek(0)
    return output_buffer.getvalue()
