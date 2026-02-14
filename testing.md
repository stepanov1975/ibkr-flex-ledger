## TEST INTEGRITY
1. **Rule of Atomicity:** Every new logical branch or bugfix must have a corresponding test case.
2. **Naming Convention:** Filenames must describe the *feature domain*, not the development status.
   * **Pattern:** `test_<module>_<specific_functionality>.py`
   * **Bad:** `test_new_features.py`, `test_bugfix_123.py` (Vague/Temporal)
   * **Good:** `test_cpu_scaling_max_limits.py` (Descriptive/Permanent)

## JAVASCRIPT TESTING
1. **Framework:** Jest with jsdom for DOM simulation.
2. **Test Location:** All JavaScript tests reside in `tests/js/` directory.
3. **Running Tests:**
   * Command: `npm test`
   * This runs Jest which executes all `*.test.js` files in `tests/js/`.
4. **Setup File:** `tests/js/setup.js` runs before each test and provides:
   * Mock for `tinymce` (external library)
   * Mock for `TinyMCEUtils` (our wrapper module)
   * Mock for `AutoSave` module
   * Mock for `fetch` API
   * Mock for `window.confirm`
   * Mock for `window.REPORT_CONFIG`
5. **Module Pattern:** JavaScript modules use IIFE pattern and export via `module.exports` for Node.js/Jest compatibility:
   ```javascript
   const ModuleName = (function() {
       // Private implementation
       return { /* public API */ };
   })();
   if (typeof module !== 'undefined' && module.exports) {
       module.exports = ModuleName;
   }
   ```
6. **Test Structure:**
   * Use `describe()` blocks to group related tests by feature.
   * Use `beforeEach()` to set up DOM fixtures via `document.body.innerHTML`.
   * Call module's `initialize()` function after setting up DOM.
7. **DOM Testing:**
   * Create minimal DOM structures needed for the test.
   * Use `document.querySelector()` to verify DOM updates.
   * Reset DOM in `beforeEach()` to ensure test isolation.
8. **Naming Convention:**
   * **Pattern:** `<module-name>.test.js`
   * **Example:** `report-form.test.js`, `autosave.test.js`
9. **Regression Tests:** Include reference comments linking to the issue being tested:
   ```javascript
   /**
    * Regression tests for Issue X: Brief description
    * Reference: code_review.md (YYYY-MM-DD) - Issue N
    */
   describe('Feature Name (Issue X Regression)', () => { ... });
   ```

## HANDLING TEST FAILURES vs CODE CHANGES
**Source of Truth:** The *Immediate User Prompt* > `ai_memory.md` > *Existing Code/Tests*.

Analyze the root cause before fixing:
1. **Is it an Intentional Change?**
   * *Criteria:* The failure aligns with a requested refactor, API change, or cleanup in the current prompt.
   * *Action:* Update the test to match the new code. **Do NOT revert the code.**
2. **Is it a Logic Defect?**
   * *Criteria:* The code produces runtime errors, crashes, or mathematical/logical errors *not* requested by the user.
   * *Action:* Fix the application code.
3. **Is it Ambiguous?**
   * *Criteria:* You cannot determine if the failure is a regression or a deprecated feature.
   * *Action:* **STOP.** Do not attempt a fix. Ask the user: *"Tests failed due to [Reason]. Is this expected behavior or a regression?"*

**Constraint:** Never use "quick fixes" (empty try/except, removing assertions) to silence tests.