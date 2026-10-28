"""
core/learning_system.py
========================
Adaptive Learning & Self-Optimization Engine.

Learns performance patterns, successful DOM selectors, page latencies,
and record attributes across all automation modules after every run.
Persists learning data to learning_data.json and applies optimizations to future runs.

Learning is:
  - Domain-scoped: XPath success rates and page latencies are stored per website domain.
  - Student-scoped: Per-student structural insights (field types, list placement, folder state).
  - Run-history: Last 50 run summaries with speed/success metrics.
"""

import os
import json
import datetime
import urllib.parse
import logging
import threading
import time
import copy

from core.paths import get_persistent_dir

logger = logging.getLogger(__name__)

LEARNING_FILE = os.path.join(get_persistent_dir(), "learning_data.json")

DEFAULT_LEARNING_DATA = {
    "version": "1.1",
    "total_runs": 0,
    "total_items_processed": 0,
    "total_files_handled": 0,
    # domain -> { 'xpaths': { key: { xpath: success_count } }, 'latencies': { op: avg_sec } }
    "domain_profiles": {},
    # student_id -> { 'grade_field_type': 'Grade'|'Adjust Score', 'list_category': 'Regular', ... }
    "record_insights": {},
    # list of last 50 run summary dicts
    "run_history": [],
    # key (usually domain) -> { off_failures, off_successes, on_failures, on_successes, confirmed_required }
    # Tracks whether a given site/domain only works with 'Auto-open DevTools' checked,
    # so the system can auto-apply that setting on future runs instead of the user
    # having to remember to check the box every time.
    "devtools_requirements": {},
    # preset_name -> { 'verified_runs': count, 'field_confidence': { field: score }, 'tuning_history': [...] }
    "pdf_extractor_profiles": {},
    # data_harmonizer_profiles: learned column associations, composite rules, and verified fingerprints
    "data_harmonizer_profiles": {
        "column_associations": {},   # source_col -> { target_col: { 'confirmed': count, 'rejected': count } }
        "composite_rules": {},       # target_col -> { 'columns': [...], 'rule': 'concat', 'confirmed': count }
        "fingerprint_runs": {},      # src_fp:sample_fp -> { 'verified_runs': count, 'last_used': timestamp }
    },
}


class LearningEngine:
    """
    Singleton engine managing self-learning and optimization across all modules.

    Usage in any module:
        from core.learning_system import learning_engine

        # Record something learned:
        learning_engine.record_successful_xpath(url, 'login_button', xpath_used)
        learning_engine.record_latency(url, 'page_load', 3.2)
        learning_engine.record_student_insight(student_id, 'list_category', 'Regular')

        # Apply what was learned:
        learned_xpaths = learning_engine.get_learned_xpaths(url, 'login_button')
        timeout = learning_engine.get_optimal_timeout(url, 'page_load', default_timeout=15.0)
        field_type = learning_engine.get_student_insight(student_id, 'grade_field_type')
    """

    _instance = None
    _lock: threading.RLock
    _save_lock: threading.Lock
    _last_save_time: float
    _dirty: bool
    _data: dict

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()   # guards all _data access
            cls._instance._save_lock = threading.Lock() # guards disk writes
            cls._instance._last_save_time = 0.0
            cls._instance._dirty = False
            cls._instance._data = {k: v for k, v in DEFAULT_LEARNING_DATA.items()}
            cls._instance.load()
        return cls._instance

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Load persisted learning data from disk."""
        if os.path.isfile(LEARNING_FILE):
            try:
                with open(LEARNING_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Deep-merge top-level keys to avoid losing new default keys
                for k, v in DEFAULT_LEARNING_DATA.items():
                    if k not in saved:
                        saved[k] = v
                with self._lock:
                    self._data = saved
            except Exception as e:
                logger.warning(f"Could not load learning_data.json: {e}")
        return self._data

    def save(self, force: bool = False) -> bool:
        """Persist current learning data to disk (thread-safe, rate-limited, and atomic).

        Serializes with json.dumps directly while holding _lock so concurrent worker
        updates cannot mutate the dictionary during serialization (avoiding
        'dictionary changed size during iteration'). Throttled to at most once
        every 1.5 seconds unless *force* is True.
        """
        now = time.time()
        with self._lock:
            self._dirty = True
            if not force and (now - getattr(self, '_last_save_time', 0.0)) < 1.5:
                return True  # throttled; will be flushed on next interval or at run finish
            try:
                serialized = json.dumps(self._data, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Could not serialize learning_data: {e}")
                return False

        save_lock = getattr(self, '_save_lock', None)
        if save_lock is None:
            save_lock = threading.Lock()
            self._save_lock = save_lock

        with save_lock:
            tmp_file = LEARNING_FILE + f".tmp.{os.getpid()}"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    f.write(serialized)

                # On Windows, os.replace() can raise [WinError 5] (Access is
                # denied) if another thread has the destination file open.
                # Retry a few times with a short back-off before giving up.
                last_err = None
                for attempt in range(6):
                    try:
                        os.replace(tmp_file, LEARNING_FILE)
                        last_err = None
                        break
                    except OSError as replace_err:
                        last_err = replace_err
                        time.sleep(0.05 * (attempt + 1))  # 50ms, 100ms, 150ms…

                if last_err is not None:
                    raise last_err

                self._last_save_time = time.time()
                self._dirty = False
                return True
            except Exception as e:
                logger.warning(f"Could not save learning_data.json: {e}")
                # Clean up the temp file if the rename ultimately failed
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except OSError:
                    pass
                return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract domain hostname from URL for site-specific learning."""
        if not url:
            return "global"
        try:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            parsed = urllib.parse.urlparse(url)
            return parsed.netloc or "global"
        except Exception:
            return "global"

    def _get_domain_profile(self, domain: str) -> dict:
        profiles = self._data.setdefault("domain_profiles", {})
        return profiles.setdefault(domain, {"xpaths": {}, "latencies": {}})

    # ------------------------------------------------------------------
    # XPath / Selector Learning
    # ------------------------------------------------------------------

    def record_successful_xpath(self, url: str, key: str, xpath: str):
        """Record that a specific XPath succeeded for element *key* on domain of *url*."""
        domain = self.extract_domain(url)
        with self._lock:
            profile = self._get_domain_profile(domain)
            key_map = profile["xpaths"].setdefault(key, {})
            key_map[xpath] = key_map.get(xpath, 0) + 1
        self.save()

    def record_failed_xpath(self, url: str, key: str, xpath: str):
        """
        Record that an XPath failed. Decrements success count (floor 0) so
        proven-bad xpaths sink in ranking over time.
        """
        domain = self.extract_domain(url)
        with self._lock:
            profile = self._get_domain_profile(domain)
            key_map = profile["xpaths"].setdefault(key, {})
            current = key_map.get(xpath, 0)
            key_map[xpath] = max(0, current - 1)
        self.save()

    def get_learned_xpaths(self, url: str, key: str) -> list:
        """Return XPaths for *key* on domain of *url* sorted by past success frequency (best first)."""
        domain = self.extract_domain(url)
        with self._lock:
            key_map = self._data.get("domain_profiles", {}).get(domain, {}).get("xpaths", {}).get(key, {})
            return [xpath for xpath, _ in sorted(key_map.items(), key=lambda x: x[1], reverse=True)]

    # ------------------------------------------------------------------
    # Domain Strategy & Site Structure Learning
    # ------------------------------------------------------------------

    def record_domain_strategy(self, url: str, strategy_key: str, strategy_value):
        """
        Record a structural strategy learned for domain of *url*.
        e.g., 'interaction_mode': 'static_links' | 'generate_documents' | 'hybrid'
              'category_column': 2 | 3
              'has_raddock': True | False
        """
        domain = self.extract_domain(url)
        with self._lock:
            profile = self._get_domain_profile(domain)
            strategies = profile.setdefault("strategies", {})
            strategies[strategy_key] = strategy_value
        self.save()

    def get_domain_strategy(self, url: str, strategy_key: str, default=None):
        """Retrieve dynamic strategy setting for domain of *url*."""
        domain = self.extract_domain(url)
        with self._lock:
            return (
                self._data.get("domain_profiles", {})
                .get(domain, {})
                .get("strategies", {})
                .get(strategy_key, default)
            )

    # ------------------------------------------------------------------
    # Timing & Latency Learning
    # ------------------------------------------------------------------

    def record_latency(self, url: str, operation: str, duration_sec: float):
        """
        Record operation execution time (seconds) using exponential moving average.
        70% weight on historical average, 30% on new observation.
        """
        domain = self.extract_domain(url)
        with self._lock:
            profile = self._get_domain_profile(domain)
            latencies = profile.setdefault("latencies", {})
            current = latencies.get(operation, duration_sec)
            latencies[operation] = round((current * 0.7) + (duration_sec * 0.3), 3)
        self.save()

    def get_optimal_timeout(self, url: str, operation: str, default_timeout: float = 15.0) -> float:
        """
        Return dynamic timeout based on learned domain latency.
        Uses 2.5x the moving average, clamped between 5s and the user's configured
        ceiling (Settings -> 'Maximum Wait Time per Action'). This is a MAXIMUM
        bound only -- the caller's WebDriverWait still returns the instant its
        condition is satisfied, so real waits are almost always shorter.
        Falls back to *default_timeout* if no learning data exists.
        """
        ceiling = self._get_wait_ceiling()
        domain = self.extract_domain(url)
        with self._lock:
            avg_sec = self._data.get("domain_profiles", {}).get(domain, {}).get("latencies", {}).get(operation)
        if avg_sec and avg_sec > 0:
            return max(5.0, min(ceiling, round(avg_sec * 2.5, 1)))
        return min(default_timeout, ceiling)

    @staticmethod
    def _get_wait_ceiling() -> float:
        """Read the user-configured maximum wait ceiling from Settings.
        Imported lazily to avoid a circular import at module load time."""
        try:
            from core.settings_manager import settings
            return float(settings.get("max_action_wait_seconds", 20))
        except Exception:
            return 20.0

    def get_wait_ceiling(self, floor: float = 0.0) -> float:
        """Public accessor for the user's configured maximum-wait ceiling
        (Settings -> 'Maximum Wait Time per Action'), optionally raised to
        *floor* for a specific flow known to need more room. This is always
        an upper bound -- callers should use it as a WebDriverWait timeout,
        which already returns as soon as its condition is met."""
        return max(floor, self._get_wait_ceiling())

    # ------------------------------------------------------------------
    # DevTools Requirement Learning
    # ------------------------------------------------------------------
    # Some sites only behave correctly when Chrome is launched with
    # '--auto-open-devtools-for-tabs' (e.g. because the page's JS behaves
    # differently once the DevTools/CDP protocol is attached). Rather than
    # relying on the user to remember to check that box every time, each
    # run's outcome is recorded here. Once the evidence is unambiguous
    # (multiple failures with it off, zero successes with it off, and at
    # least one confirmed success with it on) the requirement is marked
    # 'confirmed' and future runs auto-apply it.

    def record_devtools_outcome(self, key: str, devtools_used: bool, success: bool):
        """Record whether a run succeeded or failed with DevTools on/off for *key*
        (typically a domain, e.g. via extract_domain(login_url))."""
        if not key:
            return
        with self._lock:
            reqs = self._data.setdefault("devtools_requirements", {})
            entry = reqs.setdefault(key, {
                "off_failures": 0, "off_successes": 0,
                "on_failures": 0, "on_successes": 0,
                "confirmed_required": False,
            })
            if devtools_used:
                entry["on_successes" if success else "on_failures"] += 1
            else:
                entry["off_successes" if success else "off_failures"] += 1

            # Promote to confirmed once the pattern is unambiguous
            if entry["off_failures"] >= 2 and entry["off_successes"] == 0 and entry["on_successes"] >= 1:
                entry["confirmed_required"] = True

            # Drop override if site starts working without DevTools
            if not devtools_used and success:
                entry["confirmed_required"] = False

            entry["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def should_force_devtools(self, key: str) -> bool:
        """True if past runs confirm *key* only works with DevTools open."""
        if not key:
            return False
        with self._lock:
            return bool(self._data.get("devtools_requirements", {}).get(key, {}).get("confirmed_required", False))

    def get_devtools_requirement(self, key: str) -> dict:
        """Return the raw devtools-requirement record for *key* (for display in the Learning UI)."""
        with self._lock:
            return dict(self._data.get("devtools_requirements", {}).get(key, {}))

    def get_all_devtools_requirements(self) -> dict:
        """Return all learned DevTools requirements, keyed by domain."""
        with self._lock:
            return dict(self._data.get("devtools_requirements", {}))

    def resolve_devtools(self, requested: bool, key: str, log_func=None) -> bool:
        """Decide the effective DevTools setting for a run about to start.

        - If the user/UI already requested True, keep it True (never downgrade
          an explicit choice).
        - If False, but this *key* (domain) has a confirmed history of only
          working with DevTools open, auto-enable it (unless the user has
          turned off 'auto_apply_learned_devtools' in Settings) and log why.
        - Otherwise, leave it False.
        """
        _log = log_func or (lambda l, m: None)
        if requested:
            return True
        try:
            from core.settings_manager import settings
            auto_apply = bool(settings.get("auto_apply_learned_devtools", True))
        except Exception:
            auto_apply = True
        if auto_apply and self.should_force_devtools(key):
            info = self.get_devtools_requirement(key)
            _log('warning',
                 f"[LEARNING] Auto-enabling 'Auto-open DevTools' for '{key}' -- previous runs "
                 f"failed {info.get('off_failures', 0)}x with it unchecked and succeeded with it "
                 f"checked. Check the box yourself to silence this, or disable auto-apply in Settings.")
            return True
        return False

    # ------------------------------------------------------------------
    # Record / Student Insights
    # ------------------------------------------------------------------

    def record_student_insight(self, student_id: str, key: str, value):
        """
        Persist a structural insight learned about a specific student record.

        Common keys:
          - 'list_category'    : 'Regular' | 'Waiting' | 'Withdrawn'
          - 'grade_field_type' : 'Adjust Score' | 'Grade'
          - 'has_files'        : True | False
          - 'folder_exists_{name}' : True | False
        """
        sid = str(student_id).strip()
        with self._lock:
            self._data.setdefault("record_insights", {}).setdefault(sid, {})[key] = value
        self.save()

    def get_student_insight(self, student_id: str, key: str, default=None):
        """Retrieve a previously learned insight for a student record."""
        sid = str(student_id).strip()
        with self._lock:
            return self._data.get("record_insights", {}).get(sid, {}).get(key, default)

    def get_all_student_insights(self) -> dict:
        """Return all per-student insight records."""
        with self._lock:
            return dict(self._data.get("record_insights", {}))

    # ------------------------------------------------------------------
    # Run Summary & History
    # ------------------------------------------------------------------

    def record_run_summary(
        self,
        module_name: str,
        total_items: int = 0,
        success_count: int = 0,
        total_files: int = 0,
        duration_sec: float = 0.0,
        **kwargs,
    ):
        """
        Append a completed run summary and update global counters.
        Call this at the end of every module run().
        """
        if "total_count" in kwargs and not total_items:
            total_items = kwargs["total_count"]
        if "duration" in kwargs and not duration_sec:
            duration_sec = kwargs["duration"]
        avg_speed = round(duration_sec / total_items, 2) if total_items > 0 else 0.0
        summary = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "module": module_name,
            "total_items": total_items,
            "success_count": success_count,
            "failed_count": total_items - success_count,
            "total_files": total_files,
            "duration_sec": round(duration_sec, 1),
            "avg_speed_sec_per_item": avg_speed,
            "success_rate_pct": round((success_count / total_items * 100) if total_items > 0 else 0, 1),
        }
        with self._lock:
            self._data["total_runs"] = self._data.get("total_runs", 0) + 1
            self._data["total_items_processed"] = self._data.get("total_items_processed", 0) + total_items
            self._data["total_files_handled"] = self._data.get("total_files_handled", 0) + total_files
            history = self._data.setdefault("run_history", [])
            history.append(summary)
            if len(history) > 50:
                self._data["run_history"] = history[-50:]
        self.save(force=True)

    def get_run_history(self) -> list:
        """Return list of run summaries (newest last)."""
        with self._lock:
            return list(self._data.get("run_history", []))

    # ------------------------------------------------------------------
    # Summary Stats
    # ------------------------------------------------------------------

    def get_summary_stats(self) -> dict:
        """Return high-level summary of all learned patterns and metrics."""
        with self._lock:
            domain_profiles = self._data.get("domain_profiles", {})
            domain_count = len(domain_profiles)
            total_patterns = sum(len(d.get("xpaths", {})) for d in domain_profiles.values())
            total_xpath_hits = sum(
                sum(count for count in key_map.values())
                for d in domain_profiles.values()
                for key_map in d.get("xpaths", {}).values()
            )
            return {
                "total_runs": self._data.get("total_runs", 0),
                "total_items_processed": self._data.get("total_items_processed", 0),
                "total_files_handled": self._data.get("total_files_handled", 0),
                "domain_count": domain_count,
                "learned_patterns": total_patterns,
                "total_xpath_hits": total_xpath_hits,
                "learned_students": len(self._data.get("record_insights", {})),
            }

    def get_all_kpis(self) -> dict:
        """Alias for get_summary_stats() ensuring robust compatibility."""
        return self.get_summary_stats()

    def get_domain_profiles(self) -> dict:
        """Return raw domain profiles for display in the Learning UI."""
        with self._lock:
            return dict(self._data.get("domain_profiles", {}))

    # ------------------------------------------------------------------
    # Data Harmonizer Adaptive Learning
    # ------------------------------------------------------------------

    def get_harmonizer_confidence(self, source_col: str, target_col: str) -> tuple[float, int]:
        """Calculates learned confidence score [0.0, 1.0] and confirmation count for a column pair."""
        with self._lock:
            profiles = self._data.get("data_harmonizer_profiles", {})
            associations = profiles.get("column_associations", {})
            src_norm = str(source_col).strip().lower()
            tgt_norm = str(target_col).strip().lower()

            target_stats = associations.get(src_norm, {}).get(tgt_norm)
            if not target_stats:
                return 0.0, 0

            confirmed = target_stats.get("confirmed", 0)
            rejected = target_stats.get("rejected", 0)
            total = confirmed + rejected
            if total <= 0:
                return 0.0, 0

            confidence = confirmed / total
            return float(confidence), int(confirmed)

    def record_harmonizer_feedback(
        self,
        source_col: str,
        target_col: str,
        confirmed: bool = True,
        rule: str = "direct",
        params: dict | None = None
    ):
        """Records user confirmation or override for an adaptive column mapping."""
        with self._lock:
            profiles = self._data.setdefault("data_harmonizer_profiles", {})
            associations = profiles.setdefault("column_associations", {})
            src_norm = str(source_col).strip().lower()
            tgt_norm = str(target_col).strip().lower()

            target_map = associations.setdefault(src_norm, {})
            stats = target_map.setdefault(tgt_norm, {"confirmed": 0, "rejected": 0, "rule": rule})

            if confirmed:
                stats["confirmed"] = stats.get("confirmed", 0) + 1
                stats["rule"] = rule
                if params:
                    stats["params"] = params
            else:
                stats["rejected"] = stats.get("rejected", 0) + 1

        self.save(force=True)

    def record_harmonizer_run(
        self,
        source_fp: str,
        sample_fp: str,
        verified_mappings: list,
        rows_processed: int = 0
    ):
        """Records a successful data harmonization run for adaptive fingerprint recognition."""
        with self._lock:
            profiles = self._data.setdefault("data_harmonizer_profiles", {})
            fp_runs = profiles.setdefault("fingerprint_runs", {})
            key = f"{source_fp}:{sample_fp}"
            entry = fp_runs.setdefault(key, {"verified_runs": 0, "mappings": []})
            entry["verified_runs"] = entry.get("verified_runs", 0) + 1
            entry["last_used"] = datetime.datetime.now().isoformat()
            entry["mappings"] = verified_mappings

            self._data["total_runs"] = self._data.get("total_runs", 0) + 1
            self._data["total_items_processed"] = self._data.get("total_items_processed", 0) + rows_processed

        self.save(force=True)

    def get_harmonizer_profile_stats(self) -> dict:
        """Returns statistics on learned data harmonization profiles."""
        with self._lock:
            profiles = self._data.get("data_harmonizer_profiles", {})
            assocs = profiles.get("column_associations", {})
            total_associations = sum(len(tgts) for tgts in assocs.values())
            fp_runs = profiles.get("fingerprint_runs", {})
            return {
                "total_learned_mappings": total_associations,
                "recognized_fingerprints": len(fp_runs),
                "profiles": profiles
            }

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def clear_learning_memory(self):
        """Reset all learned data to a clean slate."""
        with self._lock:
            self._data = {k: v for k, v in DEFAULT_LEARNING_DATA.items()}
        self.save(force=True)


# Module-level singleton instance — import and use this everywhere
learning_engine = LearningEngine()
