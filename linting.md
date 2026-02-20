## JAVASCRIPT LINTING (ESLint)
1. **Check for issues:** `npx eslint static/js/*.js`
2. **Auto-fix issues:** `npx eslint static/js/*.js --fix`
3. **Config file:** `.eslintrc.json` (4-space indent, single quotes, semicolons required)
4. **Completion Gate:** JavaScript code is not complete until ESLint reports zero errors.

## CSS LINTING (Stylelint)
1. **Check for issues:** `npx stylelint "static/css/*.css"`
2. **Auto-fix issues:** `npx stylelint "static/css/*.css" --fix`
3. **Config file:** `.stylelintrc.json` (extends stylelint-config-standard)
4. **Completion Gate:** CSS code is not complete until Stylelint reports zero errors.
5. **Key Rules:**
   * Use short hex colors (`#fff` not `#ffffff`)
   * Use modern `rgb()` notation (not `rgba()` for opacity)
   * Use `clip-path` instead of deprecated `clip` property
   * No duplicate selectors across the file

## TEMPLATE LINTING (djLint)
1. **Check for issues:** `djlint templates/ --lint`
2. **Auto-fix issues:** `djlint templates/ --reformat`
3. **Scope:** Jinja2/HTML templates in `templates/` directory
4. **Completion Gate:** Advisory only - not blocking. Focus on functional issues.
5. **Issue Categories:**
   * **H019 (Fix Required):** `javascript:` URLs - use proper event handlers instead
   * **H021 (Acceptable):** Inline styles - allowed for JS-controlled visibility (`display:none`)
   * **H023 (Acceptable):** Entity references - valid HTML, no functional impact
   * **H030/H031 (Ignore):** Meta description/keywords - irrelevant for internal tools
6. **Key Functional Rules:**
   * Avoid `javascript:` URLs in `href` attributes - use `onclick` or `<button>` elements
   * Ensure all Jinja blocks are properly closed (`{% endif %}`, `{% endfor %}`)
   * Validate HTML tag nesting (unclosed tags cause rendering issues)

## Python LINTING & SUPPRESSION POLICY
1. **Pylint Workflow:**
   * Command: `pylint app/ --disable=C0303,R0913,R0914,R0917,C0301,R0911,R0912,C0302,C0305,R0902`
   * **Zero Tolerance:** No `E` (Error) or `F` (Fatal) messages allowed.
   * **Refactor:** Address `R` (Refactor) and `W` (Warning) messages by code improvement, not suppression.
2. **Ruff Workflow:**
   * Command: `ruff check app/ --ignore=E501,W293,W291`
3. **Suppression Rules:**
   * **Last Resort:** Only suppress linting errors if fixing them introduces risk or reduces readability.
   * **Syntax:**
     * `pylint: disable=broad-except` → Must include comment: `# Reason: <Why specific catch is unsafe>`
     * `pylint: disable=missing-function-docstring` → Allowed ONLY for trivial getters/setters.