import os
import sys
import time
import csv
from datetime import datetime, timedelta
import threading
import pandas as pd
import customtkinter as ctk
from tkinter import filedialog, messagebox

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class AutomationDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Student Grade Automation Dashboard")
        self.geometry("1100x750")

        # Paths & Data
        self.credentials_path = ""
        self.csv_path = ""
        self.config = {}
        self.driver = None

        # Threading & Control
        self.is_running = False
        self.abort_requested = False
        self.automation_thread = None

        # Statistics Tracking
        self.start_time = None
        self.completed_count = 0
        self.total_count = 0
        self.avg_time_per_item = 0.0

        # Build UI
        self._create_widgets()
        self._start_timer_loop()

    def _create_widgets(self):
        # Header / File Selection Section
        file_frame = ctk.CTkFrame(self)
        file_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(file_frame, text="Credentials File:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.lbl_cred_path = ctk.CTkLabel(file_frame, text="No file selected", text_color="gray")
        self.lbl_cred_path.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        btn_cred = ctk.CTkButton(file_frame, text="Browse...", command=self.load_credentials, width=100)
        btn_cred.grid(row=0, column=2, padx=10, pady=10)

        ctk.CTkLabel(file_frame, text="Links CSV File:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.lbl_csv_path = ctk.CTkLabel(file_frame, text="No file selected", text_color="gray")
        self.lbl_csv_path.grid(row=1, column=1, padx=10, pady=10, sticky="w")
        btn_csv = ctk.CTkButton(file_frame, text="Browse...", command=self.load_csv, width=100)
        btn_csv.grid(row=1, column=2, padx=10, pady=10)

        file_frame.columnconfigure(1, weight=1)

        # Control Buttons
        btn_frame = ctk.CTkFrame(self)
        btn_frame.pack(fill="x", padx=15, pady=5)

        self.btn_start = ctk.CTkButton(btn_frame, text="Start Automation", command=self.start_automation, fg_color="green", hover_color="darkgreen")
        self.btn_start.pack(side="left", padx=15, pady=10, expand=True, fill="x")

        self.btn_abort = ctk.CTkButton(btn_frame, text="Abort Process", command=self.abort_automation, fg_color="red", hover_color="darkred", state="disabled")
        self.btn_abort.pack(side="left", padx=15, pady=10, expand=True, fill="x")

        self.btn_verify = ctk.CTkButton(btn_frame, text="Verify Logs", command=self.verify_logs, fg_color="orange", hover_color="darkorange")
        self.btn_verify.pack(side="left", padx=15, pady=10, expand=True, fill="x")

        # Status & Statistics Panel
        stats_frame = ctk.CTkFrame(self)
        stats_frame.pack(fill="x", padx=15, pady=10)

        self.lbl_status = ctk.CTkLabel(stats_frame, text="Status: IDLE", font=("Arial", 16, "bold"), text_color="lightgray")
        self.lbl_status.grid(row=0, column=0, columnspan=2, padx=15, pady=5, sticky="w")

        self.progress_bar = ctk.CTkProgressBar(stats_frame)
        self.progress_bar.grid(row=1, column=0, columnspan=2, padx=15, pady=10, sticky="ew")
        self.progress_bar.set(0)

        self.lbl_start_time = ctk.CTkLabel(stats_frame, text="Start Time: --")
        self.lbl_start_time.grid(row=2, column=0, padx=15, pady=2, sticky="w")

        self.lbl_elapsed = ctk.CTkLabel(stats_frame, text="Elapsed Time: 00:00:00")
        self.lbl_elapsed.grid(row=2, column=1, padx=15, pady=2, sticky="w")

        self.lbl_eta = ctk.CTkLabel(stats_frame, text="Approx. Finishing Time: --")
        self.lbl_eta.grid(row=3, column=0, padx=15, pady=2, sticky="w")

        self.lbl_counts = ctk.CTkLabel(stats_frame, text="Progress: 0 / 0 Completed (0 Remaining)")
        self.lbl_counts.grid(row=3, column=1, padx=15, pady=2, sticky="w")

        stats_frame.columnconfigure(0, weight=1)
        stats_frame.columnconfigure(1, weight=1)

        # Live Console Output Panel
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=15, pady=10)

        ctk.CTkLabel(log_frame, text="Live Operation Logs:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        self.log_textbox = ctk.CTkTextbox(log_frame, state="disabled", font=("Courier", 12))
        self.log_textbox.pack(fill="both", expand=True, padx=10, pady=5)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, message):
        """Appends log text to live GUI box and external log files.
        Safe to call from a background thread: GUI updates are marshalled
        onto the main thread via `after(0, ...)`.
        """
        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        formatted_message = f"{timestamp} {message}\n"

        def _update_box():
            self.log_textbox.configure(state="normal")
            self.log_textbox.insert("end", formatted_message)
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")

        # Tkinter is not thread-safe; schedule the widget update on the main loop
        self.after(0, _update_box)

        # Write to Text Log File alongside Links.csv
        if self.csv_path:
            base_dir = os.path.dirname(self.csv_path)
            log_file = os.path.join(base_dir, "automation_activity.log")
            try:
                with open(log_file, "a") as f:
                    f.write(formatted_message)
            except Exception as e:
                print(f"Error writing to log file: {e}")

    def log_csv_status(self, student_id, link, status, details):
        """Logs operation status to an execution CSV file."""
        if not self.csv_path:
            return
        base_dir = os.path.dirname(self.csv_path)
        csv_log_file = os.path.join(base_dir, "execution_results.csv")
        file_exists = os.path.isfile(csv_log_file)

        try:
            with open(csv_log_file, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "StudentId", "Link", "Status", "Details"])
                writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), student_id, link, status, details])
        except Exception as e:
            self.log(f"Error appending to CSV log: {e}")

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------
    def load_credentials(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if path:
            self.credentials_path = path
            self.lbl_cred_path.configure(text=os.path.basename(path), text_color="white")
            self.parse_credentials()

    def parse_credentials(self):
        try:
            self.config = {}
            with open(self.credentials_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        self.config[k.strip()] = v.strip()
            self.log("Successfully loaded credentials file.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse credentials: {e}")

    def load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if path:
            self.csv_path = path
            self.lbl_csv_path.configure(text=os.path.basename(path), text_color="white")
            self.log("Loaded Links CSV file.")

    def update_status(self, text, color="white"):
        self.after(0, lambda: self.lbl_status.configure(text=f"Status: {text}", text_color=color))

    def _start_timer_loop(self):
        """Updates duration/ETA elements per second."""
        if self.is_running and self.start_time:
            elapsed = datetime.now() - self.start_time
            self.lbl_elapsed.configure(text=f"Elapsed Time: {str(elapsed).split('.')[0]}")

            if self.completed_count > 0 and self.total_count > 0:
                self.avg_time_per_item = elapsed.total_seconds() / self.completed_count
                remaining_count = self.total_count - self.completed_count
                remaining_seconds = remaining_count * self.avg_time_per_item
                finish_datetime = datetime.now() + timedelta(seconds=remaining_seconds)
                self.lbl_eta.configure(text=f"Approx. Finishing Time: {finish_datetime.strftime('%H:%M:%S (%Y-%m-%d)')}")

        self.after(1000, self._start_timer_loop)

    # ------------------------------------------------------------------
    # Automation control
    # ------------------------------------------------------------------
    def start_automation(self):
        if not self.credentials_path or not self.csv_path:
            messagebox.showwarning("Warning", "Please upload both Credentials and CSV files first.")
            return

        self.is_running = True
        self.abort_requested = False
        self.btn_start.configure(state="disabled")
        self.btn_abort.configure(state="normal")
        self.btn_verify.configure(state="disabled")

        self.start_time = datetime.now()
        self.lbl_start_time.configure(text=f"Start Time: {self.start_time.strftime('%H:%M:%S')}")

        self.automation_thread = threading.Thread(target=self._run_automation_process, daemon=True)
        self.automation_thread.start()

    def abort_automation(self):
        if self.is_running:
            self.abort_requested = True
            self.log("Abort requested by user! Halting process...")
            self.update_status("ABORTING...", "orange")

    # ------------------------------------------------------------------
    # Robust click helper
    # ------------------------------------------------------------------
    def safe_click(self, wait, xpath, description="element", retries=3):
        """
        Clicks an element reliably even when another element (sticky header,
        overlay, off-screen table cell, etc.) is intercepting the click point.

        FAST PATH: if the element has an inline `onclick="..."` handler (as
        this site's buttons do, e.g. onclick="EditStudents('2', 'withdrawn...')"),
        we invoke that JS function directly instead of simulating a mouse
        click. This skips all of: waiting for the element to register as
        "clickable" (visible + enabled), scrolling it into view, and any
        click-interception retries — since we're calling the underlying
        function rather than interacting with the rendered element at all.
        This is where most of the per-row delay was coming from.

        FALLBACK (elements without an inline onclick, e.g. plain form
        submit inputs or anchor-based navigation):
          1. Locate + wait for clickability.
          2. Scroll the element to the vertical center of the viewport
             (not just into view) so fixed/sticky headers and footers can't
             cover it.
          3. Try a native Selenium click.
          4. If that raises ElementClickInterceptedException, fall back to a
             JavaScript click, which clicks the DOM node directly and is
             immune to viewport/overlap issues.
          5. Retry the whole cycle on StaleElementReferenceException (DOM
             re-rendered between locating and clicking).
        """
        last_exception = None
        for attempt in range(1, retries + 1):
            try:
                # presence_of_element_located, not element_to_be_clickable —
                # we don't need visibility/enabled state for the onclick
                # fast path, and checking it would cost the same time we're
                # trying to save.
                elem = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))

                onclick_code = elem.get_attribute("onclick")
                if onclick_code:
                    try:
                        # Bind `this` to the element so any onclick code
                        # that references `this` behaves the same as a real
                        # click would.
                        self.driver.execute_script(
                            "(function(){" + onclick_code + "}).call(arguments[0]);", elem
                        )
                        return elem
                    except Exception:
                        self.log(f"Direct onclick invocation failed on {description}; falling back to simulated click.")
                        # fall through to the click-based path below

                # No inline onclick (or it failed) — fall back to a real
                # simulated click, which does need the element to be
                # visible/enabled first.
                elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    elem,
                )
                time.sleep(0.12)  # let scroll settle

                try:
                    elem.click()
                    return elem
                except ElementClickInterceptedException:
                    self.log(f"Native click intercepted on {description}; retrying with JS click.")
                    self.driver.execute_script("arguments[0].click();", elem)
                    return elem

            except StaleElementReferenceException:
                last_exception = sys.exc_info()[1]
                self.log(f"Stale element while clicking {description}; re-locating (attempt {attempt}/{retries}).")
                time.sleep(0.5)
                continue
            except ElementClickInterceptedException as e:
                last_exception = e
                self.log(f"Click still intercepted on {description}; retrying (attempt {attempt}/{retries}).")
                time.sleep(0.5)
                continue

        # All retries exhausted — raise the last error so the outer per-row
        # try/except can log it and move on to the next row.
        raise last_exception if last_exception else Exception(f"Failed to click {description}")

    def select_student_row(self, wait, list_xpath, student_id, category_label, timeout=10):
        """
        Clicks the specific row/option for `student_id` inside the given
        list container (Regular/Waiting/Withdrawn). This is required before
        the Edit button becomes clickable — the site disables Edit until a
        student is actually selected in the list.

        We target the innermost element under list_xpath whose text contains
        the studentId AND whose children do NOT also contain it — this
        avoids selecting a large wrapping <div>/<tr> and instead clicks the
        actual row/label/option the site expects.

        Responsive tables commonly render a hidden duplicate of the same
        cell (e.g. a mobile-only copy with a 'd-none'/'hidden' class), and
        `find_element`/`presence_of_element_located` will happily return
        that hidden copy since it only checks DOM presence, not visibility —
        that caused "element not interactable" errors. So here we fetch ALL
        matches and pick the first one that is actually visible/displayed.
        """
        row_xpath = (
            f"{list_xpath}//*[contains(normalize-space(.), '{student_id}') "
            f"and not(.//*[contains(normalize-space(.), '{student_id}')])]"
        )

        last_exception = None
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                candidates = self.driver.find_elements(By.XPATH, row_xpath)
                visible_candidates = [c for c in candidates if c.is_displayed()]

                if not visible_candidates:
                    time.sleep(0.15)
                    continue

                row_elem = visible_candidates[0]

                # FAST PATH: the leaf element we matched is often just a
                # text label — the actual selection handler is commonly on
                # the row/li/tr that wraps it. Check the leaf first, then
                # walk up to the nearest ancestor carrying an onclick, and
                # invoke it directly via JS instead of simulating a click.
                onclick_code = row_elem.get_attribute("onclick")
                onclick_target = row_elem
                if not onclick_code:
                    ancestor = self.driver.execute_script(
                        "return arguments[0].closest('[onclick]');", row_elem
                    )
                    if ancestor:
                        onclick_target = ancestor
                        onclick_code = ancestor.get_attribute("onclick")

                if onclick_code:
                    try:
                        self.driver.execute_script(
                            "(function(){" + onclick_code + "}).call(arguments[0]);", onclick_target
                        )
                        self.log(f"Selected Student ID {student_id} in the '{category_label}' list.")
                        return row_elem
                    except Exception:
                        pass  # fall through to simulated click below

                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    row_elem,
                )
                time.sleep(0.12)

                try:
                    row_elem.click()
                except Exception:
                    # Covers ElementClickInterceptedException and
                    # ElementNotInteractableException — JS click dispatches
                    # directly on the node and sidesteps both overlap and
                    # "not currently visible" quirks caused by
                    # animations/transitions still in progress.
                    self.driver.execute_script("arguments[0].click();", row_elem)

                self.log(f"Selected Student ID {student_id} in the '{category_label}' list.")
                time.sleep(0.2)
                return row_elem

            except StaleElementReferenceException:
                last_exception = sys.exc_info()[1]
                time.sleep(0.3)
                continue

        raise Exception(
            f"Could not select a visible row for Student ID {student_id} within the "
            f"'{category_label}' list box (found candidates but none were visible/interactable): {last_exception}"
        )

    def safe_send_keys(self, wait, xpath, value, description="field", timeout=12, blur_after=True):
        """
        Locates a field and types `value` into it, but first validates the
        element is actually a text-enterable field (<input> or <textarea>).

        This exists because a misconfigured XPath in credentials.txt can
        point at the wrong element on the page (e.g. accidentally reusing a
        <select>'s XPath for a date field) — calling .clear()/.send_keys()
        on a <select> raises a bare "invalid element state" error from
        Selenium with no indication of what actually went wrong. Here we
        catch that case ourselves and raise a message that tells the user
        exactly which config key to fix.

        IMPORTANT: this polls via find_elements() rather than wait.until(),
        because wait.until() blocks for its own internal timeout (15s) on
        every single call — if the very first poll attempt found nothing,
        the whole function would silently eat the full 15s and then raise a
        bare, uncaught TimeoutException with no useful message, well past
        our intended `timeout` budget. Polling directly keeps us inside the
        budget we actually asked for and lets us raise a clear diagnosis.

        If `blur_after` is True (default), we click outside the field right
        after typing — this fires the page's own onChange/onBlur handlers,
        which many forms rely on to recalculate or reveal other dependent
        fields (e.g. entering a score that the page then reflects elsewhere
        before you're allowed to submit).
        """
        end_time = time.time() + timeout
        last_exception = None
        while time.time() < end_time:
            try:
                matches = self.driver.find_elements(By.XPATH, xpath)
                if not matches:
                    time.sleep(0.2)
                    continue

                elem = matches[0]
                tag = (elem.tag_name or "").lower()
                input_type = (elem.get_attribute("type") or "").lower()

                if tag == "select":
                    raise Exception(
                        f"The XPath configured for '{description}' points at a <select> dropdown, "
                        f"not a text field. Double-check credentials.txt — this likely means the "
                        f"XPath was copy-pasted from another field by mistake."
                    )
                if tag not in ("input", "textarea"):
                    raise Exception(
                        f"The XPath configured for '{description}' points at a <{tag}> element, "
                        f"which cannot accept typed text. Please verify the XPath in credentials.txt."
                    )
                if input_type in ("checkbox", "radio", "button", "submit", "hidden"):
                    raise Exception(
                        f"The XPath configured for '{description}' points at an <input type=\"{input_type}\">, "
                        f"which cannot accept typed text. Please verify the XPath in credentials.txt."
                    )

                if not elem.is_displayed():
                    time.sleep(0.2)
                    continue

                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    elem,
                )
                time.sleep(0.15)
                elem.clear()
                elem.send_keys(value)

                if blur_after:
                    self.click_outside_field()

                return elem

            except StaleElementReferenceException:
                last_exception = sys.exc_info()[1]
                time.sleep(0.2)
                continue

        raise Exception(
            f"Could not locate/interact with '{description}' (xpath: {xpath}) within {timeout}s after "
            f"the previous step. Possible causes: the site took longer than expected to reveal this field, "
            f"the XPath doesn't match this student's record/category, or this field genuinely doesn't apply "
            f"here (e.g. some categories may not expose an adjustable score). "
            f"Last error seen: {last_exception}"
        )

    def click_outside_field(self):
        """
        Blurs the currently focused input and clicks the document body,
        mimicking a user clicking outside a field. Many forms only
        recalculate/reveal dependent fields on blur/change — not on every
        keystroke — so this needs to happen before we try to submit.
        Done via JS rather than an actual coordinate click so it can't be
        intercepted by an overlay and doesn't depend on anything being
        visible at a particular screen position.
        """
        try:
            self.driver.execute_script(
                "if (document.activeElement) { document.activeElement.blur(); } "
                "document.body.click();"
            )
        except Exception:
            pass
        time.sleep(0.3)  # give the page's JS a moment to react

    def resolve_score_or_grade_field(self, timeout=6):
        """
        After clicking Update, the modal exposes either an 'Adjust Score'
        field or a 'Grade' field depending on the student's record/category
        — not both. This polls for whichever one actually appears (visible
        in the DOM) and returns the matching field + submit button XPaths,
        so the rest of the flow doesn't need to know which one applies.

        Falls back gracefully: if grade_xpath / the submit-button configs
        aren't present in credentials.txt, only the Adjust Score path is
        attempted (preserving old behavior for existing configs).
        """
        adjust_score_xpath = self.config.get("adjust_score_xpath", "")
        grade_xpath = self.config.get("grade_xpath", "")
        score_submit_xpath = self.config.get("score_submit_xpath", self.config.get("submit_xpath", ""))
        grade_submit_xpath = self.config.get("grade_submit_xpath", "")

        end_time = time.time() + timeout
        while time.time() < end_time:
            if adjust_score_xpath:
                matches = self.driver.find_elements(By.XPATH, adjust_score_xpath)
                if any(e.is_displayed() for e in matches):
                    return adjust_score_xpath, score_submit_xpath, "Adjust Score"

            if grade_xpath:
                matches = self.driver.find_elements(By.XPATH, grade_xpath)
                if any(e.is_displayed() for e in matches):
                    return grade_xpath, grade_submit_xpath, "Grade"

            time.sleep(0.2)

        raise Exception(
            "Could not find either an 'Adjust Score' field (adjust_score_xpath) or a "
            "'Grade' field (grade_xpath) after clicking Update. Check that both XPaths "
            "are correctly set in credentials.txt, and that at least one applies to "
            "this student's record/category."
        )

    def safe_select_dropdown(self, xpath, value, description="dropdown", timeout=12, blur_after=True):
        """
        Selects the option whose visible text matches `value` in a <select>
        dropdown (used for the Grade field, which — unlike Adjust Score —
        is a dropdown rather than a free-text input).

        Tries an exact match via Select.select_by_visible_text() first; if
        that fails (e.g. due to surrounding whitespace in the option text),
        falls back to a trimmed comparison across all options before giving
        up with a clear error listing what options actually exist.
        """
        end_time = time.time() + timeout
        last_exception = None
        while time.time() < end_time:
            try:
                matches = self.driver.find_elements(By.XPATH, xpath)
                if not matches:
                    time.sleep(0.2)
                    continue

                elem = matches[0]
                tag = (elem.tag_name or "").lower()

                if tag != "select":
                    raise Exception(
                        f"The XPath configured for '{description}' points at a <{tag}> element, "
                        f"not a <select> dropdown. Please verify grade_xpath in credentials.txt."
                    )

                if not elem.is_displayed():
                    time.sleep(0.2)
                    continue

                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    elem,
                )
                time.sleep(0.15)

                select_elem = Select(elem)
                try:
                    select_elem.select_by_visible_text(value)
                except NoSuchElementException:
                    # Fall back to a trimmed comparison in case the option
                    # text has extra whitespace that a strict match misses.
                    matched = None
                    for opt in select_elem.options:
                        if opt.text.strip() == value.strip():
                            matched = opt
                            break
                    if matched is None:
                        available = [opt.text for opt in select_elem.options]
                        raise Exception(
                            f"No option matching '{value}' found in '{description}' dropdown. "
                            f"Available options: {available}"
                        )
                    matched.click()

                if blur_after:
                    self.click_outside_field()

                return elem

            except StaleElementReferenceException:
                last_exception = sys.exc_info()[1]
                time.sleep(0.2)
                continue

        raise Exception(
            f"Could not locate/interact with '{description}' (xpath: {xpath}) within {timeout}s. "
            f"Last error seen: {last_exception}"
        )

    # ------------------------------------------------------------------
    # Main automation loop
    # ------------------------------------------------------------------
    def _run_automation_process(self):
        try:
            self.update_status("INITIALIZING DRIVER...", "yellow")
            df = pd.read_csv(self.csv_path)
            self.total_count = len(df)
            self.completed_count = 0
            self.lbl_counts.configure(text=f"Progress: 0 / {self.total_count} Completed ({self.total_count} Remaining)")

            options = webdriver.ChromeOptions()
            options.add_argument("--start-maximized")
            self.driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(self.driver, 15)

            # Step 1: Login
            url = self.config.get("url", "")
            if not url.startswith("http"):
                url = "https://" + url

            self.log(f"Navigating to login page: {url}")
            self.driver.get(url)

            self.log("Entering login credentials...")
            wait.until(EC.element_to_be_clickable((By.XPATH, self.config["user_xpath"]))).send_keys(self.config["user"])
            wait.until(EC.element_to_be_clickable((By.XPATH, self.config["password_xpath"]))).send_keys(self.config["password"])

            if "role_xpath" in self.config and self.config["role_xpath"]:
                select_elem = Select(wait.until(EC.presence_of_element_located((By.XPATH, self.config["role_xpath"]))))
                select_elem.select_by_visible_text(self.config["role"])

            login_url_before = self.driver.current_url
            self.safe_click(wait, self.config["login_xpath"], "Login button")

            try:
                WebDriverWait(self.driver, 8).until(lambda d: d.current_url != login_url_before)
            except TimeoutException:
                pass  # some sites redirect via JS without a full URL change; continue anyway

            self.log("Login submitted successfully.")

            # Step 2: Iterate Links CSV
            for index, row in df.iterrows():
                if self.abort_requested:
                    self.log("Automation aborted before completing all entries.")
                    break

                student_id = str(row["studentId"]).strip()
                link = str(row["link"]).strip()
                date_val = str(row["finalGradeReceivedDate"]).strip()
                score_val = str(row["score"]).strip()

                self.update_status(f"PROCESSING ({index+1}/{self.total_count})", "#3399FF")
                self.log(f"--- [Row {index+1}] Processing Student ID: {student_id} ---")

                try:
                    self.driver.get(link)

                    # Click Students Tab
                    self.safe_click(wait, self.config["student_xpath"], "Students tab")

                    # Determine List Category & Edit Button.
                    # Poll (fast) instead of a blind sleep — returns the
                    # instant a match is found, but still tolerates the tab
                    # content still rendering on a slower page load.
                    target_edit_xpath = None
                    target_list_xpath = None
                    category_found = None

                    lists = [
                        ("Regular", self.config["regular_student_list_xpath"], self.config["edit_regular_xpath"]),
                        ("Waiting", self.config["waiting_student_list_xpath"], self.config["edit_waiting_xpath"]),
                        ("Withdrawn", self.config["withdrawn_student_list_xpath"], self.config["edit_withdrawn_xpath"])
                    ]

                    poll_end = time.time() + 6
                    while time.time() < poll_end and not category_found:
                        for cat, list_xpath, edit_xpath in lists:
                            try:
                                elem = self.driver.find_element(By.XPATH, list_xpath)
                                if student_id in elem.text:
                                    category_found = cat
                                    target_edit_xpath = edit_xpath
                                    target_list_xpath = list_xpath
                                    break
                            except NoSuchElementException:
                                continue
                        if not category_found:
                            time.sleep(0.15)

                    if not category_found:
                        raise Exception(f"Student ID {student_id} not located in Regular, Waiting, or Withdrawn list.")

                    self.log(f"Student ID {student_id} identified under '{category_found}' list.")

                    # Select the specific student row inside the list box.
                    # The Edit button carries a "disable" CSS class until a
                    # student row is selected, so click the row first —
                    # safe_click's JS-click fallback handles the button
                    # itself regardless of that class.
                    self.select_student_row(wait, target_list_xpath, student_id, category_found)

                    self.safe_click(wait, target_edit_xpath, f"Edit button ({category_found})")
                    self.log("Opened 'Edit Status' modal.")

                    # Select 'Yes' in Final Grade Dropdown
                    fg_dropdown = Select(wait.until(EC.presence_of_element_located((By.XPATH, self.config["final_grade_xpath"]))))
                    fg_dropdown.select_by_visible_text("Yes")

                    # Input Date
                    self.safe_send_keys(wait, self.config["final_grade_received_date_xpath"], date_val, "Final Grade Received Date field")

                    # Hit Update Button
                    self.safe_click(wait, self.config["update_xpath"], "Update button")
                    self.log("Submitted updated grade status.")

                    # Determine whether this student's record exposes an
                    # 'Adjust Score' field or a 'Grade' field, and use the
                    # matching submit button for whichever one applies.
                    field_xpath, field_submit_xpath, field_label = self.resolve_score_or_grade_field()

                    # Grade is a <select> dropdown — pick the option whose
                    # text matches the score column. Adjust Score is a free
                    # text input — type into it. Either way, we click
                    # outside afterward so the page's own onChange/onBlur
                    # logic recalculates/reveals any dependent fields before
                    # we submit.
                    if field_label == "Grade":
                        self.safe_select_dropdown(field_xpath, score_val, f"{field_label} field")
                    else:
                        self.safe_send_keys(wait, field_xpath, score_val, f"{field_label} field")

                    # Submit final changes using the submit button that
                    # corresponds to whichever field we actually used
                    self.safe_click(wait, field_submit_xpath, f"{field_label} submit button")
                    self.log(f"Successfully posted score ({score_val}) via {field_label} field for Student ID {student_id}.")

                    self.log_csv_status(student_id, link, "SUCCESS", f"Updated score and grade successfully via {field_label} field.")

                except Exception as ex:
                    err_msg = f"Failed processing Student ID {student_id}: {str(ex)}"
                    self.log(f"ERROR: {err_msg}")
                    self.log_csv_status(student_id, link, "FAILED", str(ex))

                # Update Counters & Progress Bar
                self.completed_count += 1
                rem = self.total_count - self.completed_count
                self.progress_bar.set(self.completed_count / self.total_count)
                self.lbl_counts.configure(text=f"Progress: {self.completed_count} / {self.total_count} Completed ({rem} Remaining)")

            if not self.abort_requested:
                self.update_status("FINISHED SUCCESSFULLY", "lightgreen")
                self.log("All execution steps finished successfully!")
            else:
                self.update_status("ABORTED", "red")

        except Exception as e:
            self.log(f"CRITICAL EXCEPTION: {e}")
            self.update_status("ERROR OCCURRED", "red")

        finally:
            if self.driver:
                self.driver.quit()
                self.log("Browser instance closed.")
            self.is_running = False
            self.btn_start.configure(state="normal")
            self.btn_abort.configure(state="disabled")
            self.btn_verify.configure(state="normal")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def verify_logs(self):
        """Cross-checks execution results against input CSV file entries."""
        if not self.csv_path:
            messagebox.showwarning("Warning", "Select Links.csv file first.")
            return

        base_dir = os.path.dirname(self.csv_path)
        csv_log_file = os.path.join(base_dir, "execution_results.csv")

        if not os.path.isfile(csv_log_file):
            messagebox.showinfo("Verification Report", "No execution log exists yet to verify against!")
            return

        try:
            df_input = pd.read_csv(self.csv_path)
            df_logs = pd.read_csv(csv_log_file)

            df_input["studentId"] = df_input["studentId"].astype(str).str.strip()
            df_logs["StudentId"] = df_logs["StudentId"].astype(str).str.strip()

            processed_ids = set(df_logs[df_logs["Status"] == "SUCCESS"]["StudentId"])
            all_ids = set(df_input["studentId"])

            matched = all_ids.intersection(processed_ids)
            missing = all_ids - processed_ids

            report = (
                f"=== LOG VERIFICATION REPORT ===\n\n"
                f"Total Input Rows in Links.csv: {len(all_ids)}\n"
                f"Successfully Processed Rows: {len(matched)}\n"
                f"Missing or Failed Rows: {len(missing)}\n\n"
            )
            if missing:
                report += f"Unprocessed/Failed Student IDs: {', '.join(missing)}"
            else:
                report += "All rows in Links.csv have been successfully processed!"

            self.log("--- Executed Verification Check ---")
            self.log(report.replace("\n", " "))
            messagebox.showinfo("Verification Results", report)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to verify logs: {e}")


if __name__ == "__main__":
    app = AutomationDashboard()
    app.mainloop()
