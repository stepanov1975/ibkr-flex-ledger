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

## Python LINTING & TYPE-CHECKING POLICY
1. **Ruff Workflow:**
   * Command: `ruff check app/ --ignore=E501,W293,W291`
2. **MyPy Workflow:**
   * Command: `mypy`
   * Configuration: `mypy.ini`
   * Scope: first-party runtime code under `app/`.
3. **Suppression Rules:**
   * **Last Resort:** Prefer correcting the type contract over adding `type: ignore`.
   * Every `type: ignore` must name the exact error code and explain why runtime behavior cannot be expressed more accurately.
