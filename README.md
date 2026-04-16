# 📄 PDF to EMI Report Engine 

Live Web App: https://emi-loan-repayment-tracker-7pbpkpnqbdhfpecnkhdpoc.streamlit.app/

## 🚀 Project Overview
In the commercial vehicle lending sector, tracking Days Past Due (DPD) and delinquency requires parsing complex, unstructured Loan Account Statements. Manually reviewing these 10-30 page PDFs to reconcile billings, bounces, and receipts takes approximately **45 minutes per account**.

I designed and developed this **Automated Loan Statement Analysis System** to parse unstructured ledger data, apply complex financial reconciliation rules, and generate a standardized Excel report in **under 30 seconds**.

**Domain Expert + Developer:** This tool relies on deep domain expertise in lending operations to programmatically handle edge cases like NACH bounces, same-day reversals, advance payment distributions, and accounting "knock-offs".

## 📈 Business Impact
* **Efficiency:** Reduced processing time from 45 minutes to < 1 minute per account (98% reduction).
* **Accuracy:** Eliminated human error in DPD calculation and bounce detection.
* **Scalability:** A single operator can now process 400+ accounts per day instead of 10.
* **Compliance & Risk:** Provides standardized, audit-ready data for NPA (Non-Performing Asset) classification.

## 🧠 Key Features & Edge Cases Handled
Unlike standard OCR tools, this engine utilizes raw Python logic to reconstruct broken text and classify transactions based on banking behavior:
* **Broken Date Reconstruction:** Merges dates that PDF extractors split across multiple lines (e.g., `"11-"` and `"SEP-2021"`).
* **Bounce & Reversal Matching:** Identifies bounded payments (NACH/Cheque) and dynamically matches them to their originating receipt to prevent false "Paid" statuses.
* **Advance Payment Waterfall:** If a borrower pays multiple EMIs at once, the engine aggregates the funds and distributes them to future unpaid months logically.
* **Knock-Off & Refund Handling:** Ignores internal accounting "knock-off" adjustments while accurately processing overpayment refunds.
* **Dynamic DPD Calculation:** Automatically calculates exactly how many days a payment was delayed past the due date.

## 💡 Architecture & Modularity
**Note on specific use-case:** This current iteration of the parsing engine is custom-tailored to the highly specific, unstructured layout of the **Manappuram Finance Statement of Account (SOA) PDF**. 
However, the underlying **ETL (Extract, Transform, Load) framework** is entirely modular. Because the engine separates the extraction phase (`pdfplumber`) from the business logic phase (Pandas & Regex), 
the same architectural pipeline can be easily adapted to process ledger formats from any major bank or NBFC (e.g., HDFC, ICICI, Bajaj Finance) simply by adjusting the target coordinates and regex patterns.

## 🛠️ Technology Stack
* **Python:** Core logic and text processing.
* **pdfplumber:** High-fidelity text extraction from complex table structures.
* **Pandas:** Data manipulation, aggregation, and structural formatting.
* **Regex (re):** Advanced pattern matching for broken dates and specific financial jargon.
* **Streamlit:** Frontend web framework for easy user interaction.
* **XlsxWriter:** Excel generation with custom cell formatting, conditional highlighting, and frozen panes.

## 💻 How to Run Locally

1. Clone the repository:
   ```bash
   git clone [https://github.com/yourusername/emi-pdf-engine.git](https://github.com/yourusername/emi-pdf-engine.git)
   cd emi-pdf-engine
2. Install dependencies: pip install -r requirements.txt
3. Run the Streamlit app: streamlit run app.py
4. Usage: Upload a Loan Account Statement PDF via the web interface and click "Download Excel Report" once processed
Developed by Mihir Yadav | Finance Operations & Data Analytics
