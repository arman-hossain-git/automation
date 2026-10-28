"""
ui/documentation_ui.py
======================
Comprehensive User Guide & Technical Documentation View.
Provides rich, user-friendly instructions on operating procedures, credentials,
CSV file formats, directory structures, and troubleshooting.

Designed for high contrast, visual clarity, and layout consistency with all application tabs.
"""

import os
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

from ui.theme import (
    BG_MAIN, BG_CARD, BG_CARD_ALT, BG_LOG,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, TEXT_ACCENT,
    ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER, ACCENT_INFO,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD, FONT_SMALL, FONT_SMALL_BOLD, FONT_LOG,
    apply_notebook_style, bind_hover_effect
)

# ---------------------------------------------------------------------------
# Documentation Content Constants
# ---------------------------------------------------------------------------

OVERVIEW_TEXT = """\
================================================================================
 🚀 UNIFIED SYSTEM AUTOMATION SUITE - OVERVIEW & QUICK START GUIDE
================================================================================

Welcome to the Unified System Automation Suite. This software consolidates all your
web automation workflows into 3 powerful, modular automation engines.

--------------------------------------------------------------------------------
1. THE 3 CORE AUTOMATION MODULES
--------------------------------------------------------------------------------

📥  1. FILE EXPORTER (modules/file_exporter.py)
    • Exports & downloads files from PCR Educator, Orbund, or any web portal.
    • Presets Available:
      - Dynamic Scraper (Auto-detect): Automatically finds file tables & links.
      - PCR Action Files (Admissions): Scrapes admissions Action Files grids.
      - PCR Dashboard Files (Main): Scrapes main student portal document grids.
      - MyPath Document Generator: Dynamically selects program dropdown options,
        triggers "Generate" buttons, and captures generated PDF preview modals.
    • Supports file extensions, category names, and file size filtering.

📤  2. FILE UPLOADER (modules/file_uploader.py)
    • Uploads local student folders & files directly into PCR Files & Documents.
    • Target Folder Options:
      - (None): Uploads directly to Files & Documents root folder.
      - Admissions Inquiry / Students Inquiry: Uploads under specific target subfolder.
      - Custom Folder: Creates or navigates to custom specified subfolder.
    • Preserves local folder taxonomy and logs all uploads with timestamps.

🎓  3. GRADE SUBMISSION (modules/grade_submission.py)
    • Submits final grade status ('Yes') and date/score per student.
    • Dynamic Field Detection: Automatically detects whether a student record
      requires an 'Adjust Score' free-text field or a 'Grade' dropdown menu.

--------------------------------------------------------------------------------
2. END-TO-END QUICK START STEPS
--------------------------------------------------------------------------------
  Step 1 ➔ Prepare your Credentials file (.txt) with login credentials.
  Step 2 ➔ Prepare your Record Links CSV (.csv) containing Student IDs and Profile URLs.
  Step 3 ➔ Launch the dashboard and select your desired module from the left menu.
  Step 4 ➔ Click 'Browse...' to select your Credentials file and Links CSV file.
  Step 5 ➔ (For File Exporter/Uploader) Select your Save Files or Source Files folder.
  Step 6 ➔ Click '▶ Start Automation'. Monitor live status, progress bar, performance
           KPI panel, and live log console.
  Step 7 ➔ Click '✓ Verify Results' when complete to audit disk files against log CSVs.
"""

CREDENTIALS_EXPLANATION = """\
================================================================================
 🔑 CREDENTIALS & CONFIGURATION FILE FORMAT (credentials.txt)
================================================================================

The automation suite reads login details and optional XPath overrides from a plain
text file (.txt). Lines starting with '#' are comments. Use 'key=value' format.

--------------------------------------------------------------------------------
ANNOTATED CREDENTIALS TEMPLATE
--------------------------------------------------------------------------------
# ==================================================================
# Credentials & Configuration File (.txt)
# ==================================================================

# ---- REQUIRED FIELDS ----
username=your_username_here
password=your_password_here
login_url=https://pcr.example.com/login.aspx

# ---- OPTIONAL: LOGIN XPATH OVERRIDES (leave blank for auto-detection) ----
username_xpath=
password_xpath=
login_btn_xpath=
role_xpath=
role=

# ---- OPTIONAL: GRADE SUBMISSION XPATH OVERRIDES ----
student_xpath=
user_xpath=
final_grade_xpath=
final_grade_received_date_xpath=
update_xpath=
adjust_score_xpath=
grade_xpath=
submit_xpath=
save_class_xpath=
regular_student_list_xpath=
waiting_student_list_xpath=
withdrawn_student_list_xpath=
edit_regular_xpath=
edit_waiting_xpath=
edit_withdrawn_xpath=

# ---- OPTIONAL: FILE EXPORTER / UPLOADER XPATH OVERRIDES ----
three_dot_menu_xpath=
files_documents_menu_xpath=
new_folder_toolbar_button_xpath=
folder_name_input_xpath=
save_button_xpath=
upload_tool_button_xpath=
file_input_xpath=
upload_submit_xpath=
"""

CSV_EXPLANATION = """\
================================================================================
 📊 CSV FILE FORMAT SPECIFICATIONS
================================================================================

The suite automatically parses comma (,) and semicolon (;) delimited CSV files.
Headers are case-insensitive and whitespace-flexible.

--------------------------------------------------------------------------------
1. RECORD LINKS CSV (for File Exporter & File Uploader)
--------------------------------------------------------------------------------
Required Columns:
  • Student / Record ID Column:  studentId, id, student, recordid, record
  • Link / URL Column:           link, url, studentlink, recordlink, pageurl

Example CSV Content:
studentId,link
1001,https://pcr.example.com/StudentProfile.aspx?id=1001
1002,https://pcr.example.com/StudentProfile.aspx?id=1002
1003,https://pcr.example.com/StudentProfile.aspx?id=1003

--------------------------------------------------------------------------------
2. GRADE SUBMISSION CSV (for Grade Submission Module)
--------------------------------------------------------------------------------
Required Columns:
  • studentId:                Student identification string/number
  • link:                     Direct URL to student profile
  • finalGradeReceivedDate:   Date string (e.g. 2026-07-31)
  • score:                    Score or Grade string (e.g. 95 or A)

Example CSV Content:
studentId,link,finalGradeReceivedDate,score
1001,https://pcr.example.com/StudentProfile.aspx?id=1001,2026-07-31,95
1002,https://pcr.example.com/StudentProfile.aspx?id=1002,2026-07-31,A+
1003,https://pcr.example.com/StudentProfile.aspx?id=1003,2026-07-31,88

--------------------------------------------------------------------------------
3. UN-SUBMIT GRADES CSV (for Un-submit Grades Module)
--------------------------------------------------------------------------------
Required Columns:
  • student (or studentId, id): Student identification string/number
  • link (or url):              Direct URL to student profile
  Note: There are NO finalGradeReceivedDate, score, or grade columns needed!

Example CSV Content:
student,link
1001,https://pcr.example.com/StudentProfile.aspx?id=1001
1002,https://pcr.example.com/StudentProfile.aspx?id=1002
1003,https://pcr.example.com/StudentProfile.aspx?id=1003

--------------------------------------------------------------------------------
4. STUDENT REMOVAL CSV (for Student Removal Module)
--------------------------------------------------------------------------------
Required Columns:
  • studentId (or id, student): Student identification string/number
  • link (or url, classlink):   Direct URL to the class page containing the student list

Action:
  • Opens class page, clicks Students tab, locates student in Regular, Waiting, or Withdrawn list.
  • Clicks Remove / Delete, confirms any alert or dialog, and saves the class to commit changes.
  • Optimizes navigation by grouping rows by class link to eliminate redundant page loads.

Example CSV Content:
studentId,link
1001,https://pcr.example.com/ClassProfile.aspx?id=501
1002,https://pcr.example.com/ClassProfile.aspx?id=501
1003,https://pcr.example.com/ClassProfile.aspx?id=502
"""

DIRECTORY_STRUCTURE_EXPLANATION = """\
================================================================================
 📁 LOCAL DIRECTORY STRUCTURE GUIDE
================================================================================

--------------------------------------------------------------------------------
1. FILE UPLOADER SOURCE DIRECTORY STRUCTURE
--------------------------------------------------------------------------------
To upload files for multiple students, structure your local Source Directory so that
each student has a dedicated folder matching their Student ID in the CSV:

  Source_Directory/
  ├── 1001/                        <-- Must match studentId in CSV
  │   ├── Admissions Inquiry/      <-- Category folder name on PCR
  │   │   ├── Application.pdf
  │   │   └── Transcript.docx
  │   └── Profile Documents/       <-- Category folder name on PCR
  │       └── ID_Card.png
  ├── 1002/                        <-- Must match studentId in CSV
  │   └── Students Inquiry/
  │       └── Verification_Form.pdf
  └── 1003/
      └── Admissions Inquiry/
          └── Resume.pdf

Rules:
  • Each immediate subfolder under Source Directory MUST be named with the exact Student ID.
  • Subfolders inside each Student ID folder represent the folder category on PCR.
  • Files inside category subfolders are uploaded into that category automatically.

--------------------------------------------------------------------------------
2. FILE EXPORTER DESTINATION DIRECTORY STRUCTURE
--------------------------------------------------------------------------------
When exporting files, the File Exporter organizes downloaded files as follows:

  Save_Directory/
  ├── 1001/                        <-- Created automatically per student
  │   ├── Action Files/
  │   │   ├── Application.pdf
  │   │   └── Transcript.docx
  │   └── Dashboard Files/
  │       └── Profile_Export.pdf
  ├── automation_execution_log.csv <-- Execution log CSV
  └── automation_debug.log         <-- Detailed log file
"""

MODULES_GUIDE_TEXT = """\
================================================================================
 🛠️ DETAILED MODULE OPERATING MANUALS
================================================================================

--------------------------------------------------------------------------------
MODULE 1: FILE EXPORTER (📥)
--------------------------------------------------------------------------------
Purpose: Scrapes and downloads files from any student profile, grid, or document generator.

Presets:
  1. Dynamic Scraper (Auto-detect): Scrapes file links sitting on any page or table.
  2. PCR Action Files (Admissions): Scrapes admissions Action Files grids.
  3. PCR Dashboard Files (Main): Scrapes main student portal document grids.
  4. MyPath Document Generator: Selects program dropdown options, triggers
     Generate buttons, and captures preview PDF modals.

How to Operate:
  1. Select Credentials file (.txt) and Record Links CSV (.csv).
  2. Select Save Files Directory.
  3. Select an Exporter Preset or specify custom Segment Names.
  4. Click '▶ Start Automation'.
  5. Use '✓ Verify Results' to audit downloaded files against the execution log CSV.

--------------------------------------------------------------------------------
MODULE 2: FILE UPLOADER (📤)
--------------------------------------------------------------------------------
Purpose: Uploads local document folders to PCR Educator Files & Documents.

How to Operate:
  1. Select Credentials file (.txt) and Student Links CSV (.csv).
  2. Select Source Files Directory (structured by Student ID subfolders).
  3. Choose Target Folder (Root, Admissions Inquiry, Students Inquiry, or custom).
  4. Click '▶ Start Automation'.
  5. The uploader creates required folders and uploads files asynchronously.

--------------------------------------------------------------------------------
MODULE 3: GRADES (🎓)
--------------------------------------------------------------------------------
Purpose: Provides two sub-features for student final grades management:
  • Sub-feature 1: Submit Grades (posts 'Yes', grade received date, and scores).
  • Sub-feature 2: Un-submit Grades (reverts status by marking 'No' and submitting).

How to Operate:
  1. Open the 'Grades' module from the sidebar.
  2. Choose the active sub-feature:
     - Click '🎓 Submit Grades' to enter grades, dates, and scores.
       (CSV requires: studentId, link, finalGradeReceivedDate, score).
     - Click '↩️ Un-submit Grades' to revert previously submitted grades.
       (CSV requires only: student and link).
  3. Select your Credentials (.txt) and Links CSV (.csv).
  4. Click '▶ Start Automation'.
"""

# ---------------------------------------------------------------------------
# PDF -> Excel Extractor  —  Complete layman-friendly user guide
# ---------------------------------------------------------------------------

PDF_EXTRACTOR_DOC = """\
================================================================================
 📄 PDF → EXCEL EXTRACTOR  —  COMPLETE USER GUIDE
================================================================================

What this tool does:
  Reads a folder full of PDF files that all follow the same layout
  (e.g. student transcripts, invoices, reports) and pulls structured
  data out of each one automatically, writing every field into a single
  Excel workbook (.xlsx) so you can sort, filter, and analyse them.

  You do not need to know programming.  The only thing you configure is
  a JSON "recipe" (called a preset) that tells the tool where to find
  each piece of data inside the PDF text.

================================================================================
 STEP 1 — GATHER YOUR FILES
================================================================================

  1. Put ALL the PDFs you want to process into ONE folder on your computer.
       • Every PDF in that folder must follow the SAME layout.
       • Sub-folders are ignored — only PDFs sitting directly in the folder
         are picked up.

  Example folder layout:

     C:\\Transcripts\\Batch_July_2026\\
     ├── student_1001.pdf
     ├── student_1002.pdf
     ├── student_1003.pdf
     └── ...more PDFs...

================================================================================
 STEP 2 — OPEN THE PDF → EXCEL EXTRACTOR TAB
================================================================================

  In the left-hand navigation panel of this dashboard, click:
    "📄 PDF → Excel Extractor"

  You will see three inner tabs:
    ⚙️  Setup & Configuration   ← where you fill in paths and the recipe
    📊  Live Activity & Logs    ← shows progress while it runs
    🔍  Visual Config Verify    ← lets you check the recipe against a real PDF

================================================================================
 STEP 3 — FILL IN THE PATHS  (Setup & Configuration tab)
================================================================================

  ① Source Folder (PDFs to process)
     Click "Browse..." and navigate to the folder containing your PDFs.
     The tool will process every .pdf file in that folder.

  ② Output Excel File (.xlsx)
     Click "Save As..." and choose where you want the output workbook saved
     and what to name it (e.g. extracted_data.xlsx).

     The tool also writes two log files next to this Excel file:
       • pdf_extractor_debug.log         — detailed step-by-step log
       • pdf_extractor_execution_log.csv — one row per PDF (success / error)

================================================================================
 STEP 4 — CHOOSE OR LOAD AN EXTRACTION PRESET
================================================================================

  A "preset" is a saved recipe that tells the tool what to look for.

  Option A — Use a built-in example:
    1. In the "Extraction Config Preset" dropdown, select:
       "Example - Bayamon Transcript"
    2. Click "Load".
    3. The JSON recipe for that transcript layout loads into the editor box.
       If your PDFs look like those transcripts, you are ready to run.

  Option B — Start from a blank template:
    1. Click "Reset to Blank Template".
    2. Edit the fields in the JSON editor (see STEP 5 below).
    3. Click "Save As..." next to the preset dropdown and give it a name
       so you can reuse it next time.

  Option C — Visual point-and-click setup (recommended for beginners):
    Switch to the 🔍 Visual Config Verify tab — see STEP 6.

================================================================================
 STEP 5 — UNDERSTANDING THE JSON CONFIG RECIPE
================================================================================

  The recipe is written in JSON format inside the text editor box.
  Each section controls a different part of the extraction.
  Here is a plain-English explanation of every field:

  ─────────────────────────────────────────────────────────────────────────────
  "record_separator"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    The exact text that marks the START of each record inside the PDF.
    If your PDF contains multiple records (e.g. one PDF with 50
    transcripts, each starting with the college name), set this to that
    repeated text.  The tool splits the whole PDF text at every occurrence
    of this separator and processes each chunk as a separate record.

  When to leave it blank:
    Leave it as "" (empty) if each individual PDF file IS one record
    (most common case — one PDF per student, one PDF per invoice, etc.).

  Example:
    "record_separator": "Bayamon Community College"
    → Every time that text appears, the tool starts a new record.

    "record_separator": ""
    → Each whole PDF is treated as one record.

  ─────────────────────────────────────────────────────────────────────────────
  "exclude_patterns"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    Lines of text you want the tool to IGNORE completely.
    Useful for recurring headers, footers, phone numbers, addresses,
    and decorative lines that appear on every page but should not be
    captured as data.

  Format:  A list of text patterns (can be plain text or regex).

  Example:
    "exclude_patterns": [
      "Page 1 of",
      "Confidential",
      "\\(787\\) 780-4242"
    ]

  Leave as [] if you do not want to exclude anything.

  ─────────────────────────────────────────────────────────────────────────────
  "name_detection"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    Finds the person's name (or any single identifying label) in the text
    and copies it into a column in the Excel file.

  Fields inside name_detection:
    output_column  The Excel column heading for this value.
                   Example: "student_name"

    pattern        A text pattern describing what the name looks like.
                   The part inside the FIRST set of (  ) is what gets
                   captured into the Excel cell.
                   Example: "^([A-Za-z]+, [A-Za-z]+)$"
                   → Captures "Last, First" style names on their own line.

    stop_at        The tool stops searching for the name once it sees
                   this text — prevents false matches further down the page.
                   Example: "Program Description:"
                   Leave as "" to search the whole record.

  Simplest possible setting (captures the first non-blank line as the name):
    "name_detection": {
        "output_column": "name",
        "pattern": "^(.+)$",
        "stop_at": ""
    }

  ─────────────────────────────────────────────────────────────────────────────
  "address_block"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    Captures one or more lines of address text that appear right after
    the name.  The tool collects lines until it hits any of the
    stop_prefixes listed.

  Fields:
    output_column  The Excel column heading.  Example: "address"
    stop_prefixes  A list of line beginnings that signal the address
                   has ended.
                   Example: ["Program Description:", "First Term:", "Status:"]

  Set to null if your PDFs have no address block:
    "address_block": null

  ─────────────────────────────────────────────────────────────────────────────
  "summary_fields"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    Captures individual values that appear somewhere in the PDF text —
    things like totals, GPAs, overall scores, dates, or reference numbers.

  Format:  A list of {name, pattern} pairs.
    name     The Excel column name for this value.
    pattern  A text pattern; the value inside the first (  ) is captured.

  Example:
    "summary_fields": [
        { "name": "cumulative_gpa",  "pattern": "GPA:\\s*(\\d+\\.\\d+)" },
        { "name": "total_credits",   "pattern": "Total Credits:\\s*(\\d+)" }
    ]

  Leave as [] if you have no summary/total fields to extract.

  ─────────────────────────────────────────────────────────────────────────────
  "data_rows"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    Captures REPEATING detail rows — like rows in a table (courses,
    transactions, line items etc.).  The tool scans every line of the
    record text and captures lines that match the pattern.

  Fields:
    pattern  A text pattern where each (  ) group maps to one column.
             Groups are matched left-to-right.
             Example: "^(\\d{2}/\\d{2}/\\d{4})\\s+([A-Z]+\\d+)\\s+(\\S+)$"
             → Captures date, course code, grade from each matching line.

    columns  A list of Excel column names, one per capture group.
             Example: ["term_date", "course_code", "grade"]

  Set to null if your PDFs have no repeating detail rows:
    "data_rows": null

  ─────────────────────────────────────────────────────────────────────────────
  "repeating_sections"
  ─────────────────────────────────────────────────────────────────────────────
  What it does:
    Captures structured blocks that repeat within a record — for example,
    a "Program Description:" block that appears multiple times per student.
    Each occurrence is numbered (_1, _2, _3 ...) in the output columns.

    This is an advanced field.  For most use-cases, summary_fields and
    data_rows are sufficient.  Leave as [] unless you specifically need
    numbered repeating blocks.

================================================================================
 STEP 6 — VERIFY THE CONFIG VISUALLY  (🔍 Visual Config Verify tab)
================================================================================

  This tab lets you SEE the raw text extracted from one of your PDFs and
  CONFIRM that your recipe is pointing at the right lines — without running
  the full batch first.

  ─────────────────────────────────────────────────────────────────────────────
  Load a sample PDF
  ─────────────────────────────────────────────────────────────────────────────
    • Click "🔀 Random Sample" to automatically pick a random PDF from
      your source folder, OR
    • Click "📂 Pick PDF" to hand-pick any specific PDF file.

    → The LEFT panel fills with the extracted text, one line per row,
      with line numbers on the left side.

  ─────────────────────────────────────────────────────────────────────────────
  Read the colour coding
  ─────────────────────────────────────────────────────────────────────────────
    Colour              What it means
    ──────────────────────────────────────────────────────────────────────────
    🟡 Amber/Yellow     This line matches your record_separator.
                        The tool treats it as the START of a new record.

    🟢 Green            This line matched a name_detection or data_rows
                        pattern.  Data WILL be captured from this line.

    ⚫ Grey             This line matches an exclude_pattern.
                        It will be IGNORED during extraction.

    ⬜ White (default)  This line is not matched by any current rule.

    🔵 Blue highlight   The line you have currently CLICKED / selected.

  ─────────────────────────────────────────────────────────────────────────────
  Click a line and assign it to a field
  ─────────────────────────────────────────────────────────────────────────────
    1. Click any line in the left viewer.
       The line text appears in the "Assign Selected Line" box on the right.

    2. Pick what that line represents using the radio buttons:

         Record Separator          → sets record_separator in the config
         Name Detection            → builds a name_detection pattern
         Address Block             → adds a stop_prefix to address_block
         Summary / Totals line     → adds a summary_fields entry
         Data Row (repeating)      → generates a starter data_rows pattern
         Exclude / Ignore          → adds the line to exclude_patterns

    3. Click "⚙ Apply to Config JSON".
       The JSON editor on the Setup tab updates instantly.
       The line viewer refreshes with updated colours.

  Notes on each assignment:
    • Record Separator  — the line text is used exactly as-is.
    • Name Detection    — an exact-match pattern is auto-built; refine it
                          in the JSON editor if the name format varies.
    • Data Row          — a generic pattern with one \\S+ group per token
                          is generated; you should refine it in the editor
                          (e.g. replace \\S+ with \\d+\\.?\\d* for numbers).
    • Summary Field     — you will be prompted to enter the column name.
    • Address Block     — the line is added as a stop_prefix (the address
                          ends when this line is reached).
    • Exclude           — the line text is escaped and added to the list.

  ─────────────────────────────────────────────────────────────────────────────
  Run the extraction preview
  ─────────────────────────────────────────────────────────────────────────────
    Click "▶ Run Extraction Preview" to test your current config against
    the loaded sample PDF.  The RIGHT panel shows what was found:
      • How many records were detected
      • The name, address, summary fields, and data row count per record
      • A sample of the first few captured data rows

    If the result does not look right:
      • Zero records found  → record_separator does not match the PDF text.
                              Look for the repeated header in the viewer and
                              click it → assign as Record Separator → Apply.
      • Name shows N/A      → name_detection.pattern does not match.
                              Click the name line → assign as Name Detection
                              → Apply.
      • Data rows = 0       → data_rows.pattern does not match any lines.
                              Click a detail row → assign as Data Row →
                              Apply, then refine the pattern in the editor.

  Repeat steps until the preview looks correct, then switch back to the
  Setup & Configuration tab and click ▶ Start Automation.

================================================================================
 STEP 7 — RUN THE FULL EXTRACTION
================================================================================

  Once the preview looks correct:
    1. Make sure Source Folder and Output Excel File are both filled in.
    2. Click "▶ Start Automation" at the top of the screen.
    3. The tool switches to the 📊 Live Activity & Logs tab automatically.
    4. Watch the progress bar and the live log console.
       Each PDF file shows up as a log entry as it is processed.
    5. When finished, the status bar reads:
         "Completed! X row(s) from Y file(s)."

  What gets written to disk:
  ┌───────────────────────────────────────────────────────────────────────┐
  │  extracted_data.xlsx              ← The main output workbook          │
  │  pdf_extractor_debug.log          ← Full step-by-step text log        │
  │  pdf_extractor_execution_log.csv  ← One row per PDF processed         │
  └───────────────────────────────────────────────────────────────────────┘

  All three files land in the same folder as your output .xlsx file.

  Execution log CSV columns:
  ┌──────────────────┬──────────────────────────────────────────────────────┐
  │ Column           │ What it contains                                     │
  ├──────────────────┼──────────────────────────────────────────────────────┤
  │ timestamp        │ Date and time when that file was processed           │
  │ source_file      │ The PDF filename                                     │
  │ status           │ success / no_records / error                         │
  │ records_found    │ How many records were found inside the PDF           │
  │ rows_written     │ How many Excel rows came from this PDF               │
  │ error_message    │ Any error that occurred (blank if successful)        │
  └──────────────────┴──────────────────────────────────────────────────────┘

================================================================================
 STEP 8 — VERIFY THE OUTPUT
================================================================================

  Click "✓ Verify Results" (top of the screen) after the extraction finishes.
  A pop-up report shows:
    • How many PDFs are in the source folder
    • How many are represented in the output Excel file
    • Any PDFs that produced zero rows (these may need attention)

  You can also:
    • Open pdf_extractor_execution_log.csv in Excel for the per-file
      status breakdown with timestamps.
    • Open pdf_extractor_debug.log in Notepad for the full trace of
      every step the tool performed.
    • Click "📄 Open Log File" on the Live Activity tab to jump straight
      to the debug log.
    • Click "📂 Open Output Folder" to open the folder in Windows Explorer.

================================================================================
 PRESET MANAGEMENT — SAVING AND REUSING YOUR CONFIG
================================================================================

  Once you have a working config, save it as a preset so you don't need
  to re-enter it in future sessions:

  Saving:
    1. On the Setup & Configuration tab, click "Save As..." next to the
       preset dropdown.
    2. Enter a meaningful name (e.g. "Monthly Invoice Report" or
       "Student Transcripts - Bayamon").
    3. Click OK.  The preset is saved to the pdf_extractor_configs folder.

  Loading next time:
    1. Open the tool.
    2. Select your preset name from the dropdown.
    3. Click "Load".  The JSON recipe loads instantly.

  Built-in presets (always available):
    "Example - Bayamon Transcript"
    → A fully working example for a Bayamon Community College transcript.
      Extracts: student name, address, program sections, attendance course
      rows (date / course code / grade / hours etc.), and cumulative GPA.
      Load this to see a complete real-world config and use it as a
      starting template for your own documents.

  Deleting a preset:
    Select it from the dropdown and click "Delete".
    Note: this action cannot be undone.

================================================================================
 TROUBLESHOOTING — COMMON PROBLEMS AND FIXES
================================================================================

  PROBLEM : "No PDF files found in folder"
  FIX      : Make sure the Source Folder path you entered actually contains
             .pdf files (check the spelling of the folder path).
             Sub-folders are not scanned — move all PDFs to the root of
             the source folder.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : "No data extracted from any PDF"
  FIX      : Your record_separator probably does not match any text in the
             PDFs.  Open the 🔍 Visual Config Verify tab, load a sample PDF,
             and look in the viewer for the line that marks the start of each
             record.  Click that line, choose "Record Separator", click Apply.
             If record_separator is meant to be blank (one record per PDF),
             make sure it is set to "" (empty string) in the JSON.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : Name column shows N/A for every record
  FIX      : The name_detection.pattern does not match the name line in your
             PDF.  In the 🔍 Verify tab, click the line containing the name,
             choose "Name Detection", click Apply.  This auto-builds an exact
             pattern from that line.  If names vary in format, adjust the
             pattern in the JSON editor to be more general.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : Data rows column shows 0 rows (no detail rows extracted)
  FIX      : The data_rows.pattern does not match the detail lines.  In the
             🔍 Verify tab, click one of the repeating detail lines, choose
             "Data Row (repeating)", click Apply.  Then refine the generated
             pattern in the JSON editor:
               • Replace \\S+ with \\d{2}/\\d{2}/\\d{4} for date fields
               • Replace \\S+ with \\d+\\.?\\d* for decimal number fields
               • Replace \\S+ with [A-Z]{1,2} for letter grade fields

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : "JSON error" when clicking Validate JSON
  FIX      : The config text has a syntax error.  Common causes:
               • Missing comma between two items in a list or object
               • Unmatched { } or [ ] brackets
               • Backslash must be written as \\\\ inside JSON strings
                 (e.g. \\d becomes \\\\d in the JSON editor)
             Click "Reset to Blank Template" to start fresh if needed.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : Output Excel has duplicate or garbled column names
  FIX      : Check that every field name in "columns" (data_rows) and
             "name" (summary_fields) is unique and contains no spaces.
             Use underscores: "final_grade" not "final grade".
             Also make sure "output_column" in name_detection and
             address_block do not clash with any other column names.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : Address is blank or capturing wrong lines
  FIX      : Adjust the stop_prefixes list in address_block.  In the
             🔍 Verify tab, click the first line AFTER the address ends,
             choose "Address Block", click Apply — this adds it as a stop.
             The address block captures lines from the line after the name
             up to (but not including) the first stop_prefix it encounters.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : Lines that should be excluded are still appearing in output
  FIX      : Add them to exclude_patterns.  In the 🔍 Verify tab, click
             the unwanted line, choose "Exclude / Ignore this line pattern",
             click Apply.  Grey lines in the viewer confirm they are excluded.
             Note: excluding a line does not remove it from the raw PDF text —
             it just strips it when the tool is looking for field values.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : pdf_extractor_debug.log or execution CSV not appearing
  FIX      : Both files are written next to your output .xlsx file.
             Make sure you have write permission for that folder.
             If the extraction failed before processing any PDF, the CSV
             may not be created — check the live log console on the
             📊 Live Activity & Logs tab for error details.

  ─────────────────────────────────────────────────────────────────────────────

  PROBLEM : Tool seems to hang / progress bar stops moving
  FIX      : Some very large or complex PDFs take longer to extract.
             Check the live log to see which file it is on.
             If it is genuinely stuck, click "■ Abort Process" — the tool
             will finish the current file and stop cleanly.
             The execution log CSV will show which files completed before
             the abort so you know where to resume.

================================================================================
 QUICK REFERENCE — CONFIG FIELDS AT A GLANCE
================================================================================

  Field                   Required?  What it controls
  ─────────────────────── ─────────  ────────────────────────────────────────
  record_separator        No*        Text marking start of each record in PDF
                                     (*Leave blank = one record per PDF file)
  exclude_patterns        No         Lines to strip/ignore during extraction
  name_detection          No         How to find and capture the record name
  address_block           No         Lines to capture as the address block
  repeating_sections      No         Numbered repeating structured blocks
  summary_fields          No         Single values scattered in the text
  data_rows               No         Repeating detail rows (table-like lines)

  All fields are optional.  Only add what your PDFs actually contain.

================================================================================
 OUTPUT EXCEL STRUCTURE
================================================================================

  The output workbook has ONE sheet.  Each row represents one data row
  (from data_rows) or one record (if no data_rows are configured).

  Columns appear in this order:
    1. Name column          (from name_detection.output_column)
    2. Address column       (from address_block.output_column, if configured)
    3. source_file          (which PDF this row came from)
    4. page_number          (page number within the PDF)
    5. Section count cols   (e.g. program_count — one per repeating section)
    6. Summary field cols   (one per entry in summary_fields)
    7. Data row cols        (one per entry in data_rows.columns)
    8. Repeating section    (e.g. program_1, first_term_1, status_1,
       field columns          program_2, first_term_2, status_2 ... etc.)
"""


class DocumentationUI(tk.Frame):
    """User Documentation View styled consistently with all application frames."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG_MAIN)
        self._build_ui()

    def _build_ui(self):
        # 1. Header Banner
        header_frame = tk.Frame(self, bg=BG_MAIN)
        header_frame.pack(fill="x", padx=20, pady=(12, 6))

        tk.Label(
            header_frame, text="📖 Documentation & User Operating Manual",
            font=FONT_TITLE, fg=TEXT_PRIMARY, bg=BG_MAIN, anchor="w"
        ).pack(fill="x")

        tk.Label(
            header_frame,
            text="Comprehensive guide covering operating procedures, credentials setup, CSV formats, directory layouts, and troubleshooting.",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_MAIN, anchor="w"
        ).pack(fill="x", pady=(2, 0))

        # 2. Main Notebook Container (Styled consistently with card containers)
        notebook_frame = tk.LabelFrame(
            self, text=" System Manuals & Reference Guides ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=10, pady=8
        )
        notebook_frame.pack(fill="both", expand=True, padx=20, pady=(4, 15))

        # Apply high-contrast Notebook tab styling
        apply_notebook_style()

        notebook = ttk.Notebook(notebook_frame)
        notebook.pack(fill="both", expand=True, padx=5, pady=5)

        # Tab 1: Overview
        self._create_doc_tab(notebook, " 🚀 Overview & Quick Start ", OVERVIEW_TEXT)

        # Tab 2: Credentials (.txt)
        tab_cred = tk.Frame(notebook, bg=BG_CARD, padx=10, pady=10)
        notebook.add(tab_cred, text=" 🔑 Credentials (.txt) ")

        cred_txt = scrolledtext.ScrolledText(
            tab_cred, bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, font=FONT_LOG, wrap="none", bd=0, padx=12, pady=10
        )
        cred_txt.pack(fill="both", expand=True, pady=(0, 10))
        cred_txt.insert("end", CREDENTIALS_EXPLANATION)
        cred_txt.config(state="disabled")

        btn_bar = tk.Frame(tab_cred, bg=BG_CARD)
        btn_bar.pack(fill="x")

        btn_copy = tk.Button(
            btn_bar, text="📋 Copy Credentials Template", font=FONT_BODY_BOLD,
            bg=ACCENT_PRIMARY, fg="white", bd=0, padx=14, pady=5, cursor="hand2",
            command=self._copy_cred_template
        )
        btn_copy.pack(side="left", padx=(0, 10))
        bind_hover_effect(btn_copy, ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER)

        btn_save = tk.Button(
            btn_bar, text="💾 Save Template File...", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", bd=0, padx=14, pady=5, cursor="hand2",
            command=self._save_cred_template
        )
        btn_save.pack(side="left")
        bind_hover_effect(btn_save, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

        # Tab 3: CSV Specifications
        self._create_doc_tab(notebook, " 📊 CSV Specifications ", CSV_EXPLANATION)

        # Tab 4: Directory Structure
        self._create_doc_tab(notebook, " 📁 Directory Layout ", DIRECTORY_STRUCTURE_EXPLANATION)

        # Tab 5: Detailed Module Guides
        self._create_doc_tab(notebook, " 🛠️ Module Manuals ", MODULES_GUIDE_TEXT)

        # Tab 6: PDF → Excel Extractor complete guide
        self._create_doc_tab(notebook, " 📄 PDF → Excel Extractor ", PDF_EXTRACTOR_DOC)

    def _create_doc_tab(self, notebook, title: str, text_content: str, is_code: bool = False):
        """Helper to create a standardized, high-contrast scrollable documentation tab."""
        tab_frame = tk.Frame(notebook, bg=BG_CARD, padx=10, pady=10)
        notebook.add(tab_frame, text=title)

        font = FONT_LOG if is_code else FONT_BODY
        txt_widget = scrolledtext.ScrolledText(
            tab_frame, bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, font=font, wrap="word", bd=0, padx=12, pady=10
        )
        txt_widget.pack(fill="both", expand=True)
        txt_widget.insert("end", text_content)
        txt_widget.config(state="disabled")
        return tab_frame

    def _copy_cred_template(self):
        start_idx = CREDENTIALS_EXPLANATION.find("# =====")
        template_str = CREDENTIALS_EXPLANATION[start_idx:] if start_idx != -1 else CREDENTIALS_EXPLANATION

        self.clipboard_clear()
        self.clipboard_append(template_str)
        messagebox.showinfo("Copied", "Credentials template copied to clipboard!")

    def _save_cred_template(self):
        start_idx = CREDENTIALS_EXPLANATION.find("# =====")
        template_str = CREDENTIALS_EXPLANATION[start_idx:] if start_idx != -1 else CREDENTIALS_EXPLANATION

        path = filedialog.asksaveasfilename(
            defaultextension=".txt", initialfile="credentials.txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(template_str)
                messagebox.showinfo("Saved", f"Credentials template saved to:\n{path}")
            except Exception as e:
                messagebox.showerror("Save Error", f"Could not save file: {e}")
