"""
Routine registry: keyword sets → routine_id + variable extraction patterns.
Router selection stays DETERMINISTIC — no ML in this hot path.
A wrong match here moves the real mouse.
"""

REGISTRY: dict[str, dict] = {
    "ROUTINE_FORM_FILL": {
        "keywords": [
            "fill form", "fill out", "fill in", "fill",
            "form", "apply", "application", "job", "submit", "intake",
            "complete the form", "enter details", "onboard", "register",
            "sign up", "hiring", "candidate",
        ],
        "description": "Fill a form with applicant details",
        "variable_patterns": {
            "APPLICANT_NAME":  r"for\s+([A-Z][a-z]+)",
            "APPLICANT_EMAIL": r"email[:\s]+(\S+@\S+\.\S+)",
        },
        "variable_defaults": {
            "APPLICANT_NAME":       "Alex",
            "APPLICANT_LASTNAME":   "Johnson",
            "APPLICANT_EMAIL":      "alex@example.com",
            "APPLICANT_PHONE":      "555-0100",
            "APPLICANT_LINKEDIN":   "linkedin.com/in/alexjohnson",
            "APPLICANT_EXPERIENCE": "3",
            "APPLICANT_SALARY":     "95000",
        },
    },
    "ROUTINE_BROWSER_SHOWPIECE": {
        "keywords": [
            "open browser", "browser", "web", "search", "open", "website",
            "navigate", "lookup", "look up", "pull up", "show me", "find",
            "google", "browse", "visit", "go to",
        ],
        "description": "Open a browser and perform a live web action via Browserbase",
        "variable_patterns": {
            "SEARCH_QUERY": r"search\s+(?:for\s+)?(.+)",
        },
        "variable_defaults": {
            "SEARCH_QUERY": "AI agent safety",
        },
    },
    "ROUTINE_LOCKED_FALLBACK": {
        "keywords": [
            "locked", "safe", "fallback", "demo", "test",
            "demonstrate", "run demo", "offline", "safe mode",
        ],
        "description": "Deterministic offline fallback routine",
        "variable_patterns": {},
        "variable_defaults": {
            "NOTE_TEXT": "Shepherd demo run",
        },
    },
}

# Minimum keyword-match confidence to accept a route (0.0–1.0)
CONFIDENCE_THRESHOLD = 0.3
